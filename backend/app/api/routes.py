"""HTTP 路由。

实现 `interface-contract.md` §2 的交互操作，落地 A2（路由）与
A3（传输方式）两个决策项。

## 路由清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | ``/health`` | 健康检查，返回组件明细 |
| GET | ``/ready`` | 就绪探针，供容器编排决定是否接流量 |
| POST | ``/api/v1/ask`` | 问答（同步） |
| POST | ``/api/v1/ask/stream`` | 问答（SSE 流式） |
| POST | ``/api/v1/molecule`` | 化学结构解析（确定性，无需模型） |
| GET | ``/openapi.json`` | 机器可读契约（FastAPI 内置） |

## 为什么同时提供同步与流式

决策项 A3 originally 列为待定。实测依据：openPangu 短请求中位
延迟 **7.10 秒**，最长可能数十秒。同步 7秒会让前端转圈且无法
取消；流式可在``t=0`` 就返回响应头，让前端立即显示"处理中"。

但流式**不能降级为唯一方案**：SSE 响应头一旦发出就无法再改
HTTP 状态码，错误只能塞进事件流，前端处理更复杂。故两者并存：
流式用于交互体验，同步用于调试、脚本调用与不支持 SSE 的环境。

**当前实现的流式是"阶段事件"而非"逐 token"**——既有
:class:`AgentLoop` 是同步迭代，拿不到中间 token（见
``streaming.py`` 的说明）。真正的 token 级流式需要改造 Agent 循环。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import ServiceRegistry, Timer, new_request_id
from app.api.errors import build_error_payload
from app.api.models import (
    AnswerResponse,
    AskRequest,
    ComponentStatus,
    HealthResponse,
    MoleculeRequest,
    MoleculeResponse,
    RequestStatus,
    ToolInvocation,
)
from app.api.streaming import answer_events

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------
# 依赖
# ----------------------------------------------------------------------


def get_registry(request: Request) -> ServiceRegistry:
    """从应用状态取服务容器。

    挂在 ``app.state`` 而非模块全局：多个 ``TestClient`` 实例
    （测试）与多 worker 进程需要各自独立的容器（依赖备忘录
    记录的"并发编辑器覆盖"同源问题——共享可变全局状态最易出这类 bug）。
    """
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        # 兜底：应用未正确初始化时也能给出可诊断的错误，
        # 而不是 AttributeError 堆栈直接冒给客户端。
        registry = ServiceRegistry()
        request.app.state.registry = registry
    return registry


# ----------------------------------------------------------------------
# 健康检查
# ----------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, tags=["运维"])
def health(registry: ServiceRegistry = Depends(get_registry)) -> HealthResponse:
    """健康检查。

    **始终返回 200**——即使服务不可用。理由：容器编排用
    ``/ready`` 判断是否接流量；若``/health`` 在故障时返回 5xx，
    liveness probe 会反复重启进程，反而掩盖真实故障。
    组件明细放在响应体里，由人来看。
    """
    probes = registry.probe()
    ready = any(p.name == "llm" and p.ready for p in probes)
    if ready:
        status = "ok" if all(p.ready for p in probes) else "degraded"
    else:
        status = "not_ready"
    return HealthResponse(
        status=status,
        ready=ready,
        components=[
            ComponentStatus(name=p.name, ready=p.ready, detail=p.detail)
            for p in probes
        ],
    )


@router.get("/ready", tags=["运维"])
def ready(registry: ServiceRegistry = Depends(get_registry)) -> JSONResponse:
    """就绪探针。

    语义严格按 k8s 约定：就绪返回 200，未就绪返回 503。
    内部 ``/health`` 不复用此函数——两者的状态码语义是**相反**的。
    """
    ready_now = registry.is_ready()
    return JSONResponse(
        status_code=200 if ready_now else 503,
        content={
            "ready": ready_now,
            "schema_version": "1.0",
        },
    )


# ----------------------------------------------------------------------
# 问答
# ----------------------------------------------------------------------


def _build_answer(
    registry: ServiceRegistry,
    question: str,
) -> tuple[dict[str, Any], int]:
    """执行一次问答并整形为响应体。

    Returns:
        ``(响应体, HTTP 状态码)``。

    Notes:
        **不在此处捕获异常**——交给全局异常处理器统一转换，
        避免错误映射逻辑散落在多处（这是本模块初版最易犯的错）。
    """
    timer = Timer()
    request_id = new_request_id()
    loop = registry.get_agent_loop()
    result = loop.run(question)

    invocations = [
        ToolInvocation(
            tool=str(item.get("tool", "?"))[:64],
            ok=bool(item.get("ok")),
            error_code=(str(item["error_code"])[:64] if item.get("error_code") else None),
        )
        for item in result.get("tool_invocations", [])
    ]

    # 检索成功与否体现在工具调用记录里。sources 保持为空列表
    # ——**宁可没有来源，也不能给一个来源形状但内容编造的条目**
    # （interface-contract.md §3）。
    #
    # 为什么现在填不了：search_knowledge 工具确实返回了含来源的JSON，
    # 但它以**字符串**形式放进 ToolResult.content 再回填给模型，
    # AgentLoop.run 的返回结构里只有 text/steps/tool_invocations，
    # 没有结构化的来源字段。要真正填充须扩展 AgentLoop 的返回契约
    # （侵入已验证模块，须单独开分支并重跑其 72 项测试）。
    # 详见 docs/interface-contract-verification.md §6。
    knowledge_ok = any(
        inv.tool == "search_knowledge" and inv.ok for inv in invocations
    )
    knowledge_failed = any(
        inv.tool == "search_knowledge" and not inv.ok for inv in invocations
    )

    status = RequestStatus.COMPLETED
    if knowledge_failed and not knowledge_ok:
        # 检索失败但文本仍返回——文本是主交付（architecture.md §6）
        status = RequestStatus.PARTIAL

    payload = AnswerResponse(
        request_id=request_id,
        status=status,
        explanation=str(result.get("text", "")),
        sources=[],
        visualization=[],
        tool_invocations=invocations,
        steps=int(result.get("steps", 1)),
        elapsed_seconds=timer.elapsed,
    )
    # 状态码与 body 的 status 保持一致：partial 仍是 200，
    # 因为学生拿到了可用答复，用 5xx 会让前端误判为整体失败。
    return payload.model_dump(), (200 if status != RequestStatus.FAILED else 500)


@router.post(
    "/api/v1/ask",
    response_model=AnswerResponse,
    tags=["问答"],
)
def ask(
    payload: AskRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> JSONResponse:
    """同步问答。

    正常与"部分成功"都返回 200；只有整体失败才由异常处理器给 5xx。
    """
    body, status_code = _build_answer(registry, payload.question)
    return JSONResponse(status_code=status_code, content=body)


@router.post(
    "/api/v1/ask/stream",
    tags=["问答"],
    response_class=EventSourceResponse,
    # 必须显式关闭 response_model：Iterator[ServerSentEvent]
    # 不是合法的 Pydantic 字段类型，不关会在装饰器执行期抛
    # FastAPIError（实测踩过，报错信息本身即给出了这个解法）。
    response_model=None,
)
def ask_stream(
    payload: AskRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> Iterator[ServerSentEvent]:
    """流式问答（SSE）。

    事件序列::

        event: meta     # 一开始就发，前端立即显示"处理中"
        event: stage# 阶段进展（检索/工具/作答）
        event: result   # 最终答复（与同步接口同结构）
        event: error    # 失败

    为什么 meta 先发：浏览器 ``EventSource`` 建立连接后，
    服务端要等第一次yield 才能响应。有了 meta 事件，
    前端能立刻拿到 request_id 并渲染加载态。
    """
    return answer_events(payload.question, registry)


# ----------------------------------------------------------------------
# 化学结构
# ----------------------------------------------------------------------


@router.post(
    "/api/v1/molecule",
    response_model=MoleculeResponse,
    tags=["化学工具"],
)
def parse_molecule(
    payload: MoleculeRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> JSONResponse:
    """解析 SMILES结构。

    **不需要模型**：RDKit 解析是确定性的、毫秒级。
    因此该接口在模型不可用时仍能工作——这是刻意的降级设计，
    前端输入框的实时预览不依赖云端模型。

    解析失败返回 200 + ``ok=false``（而非 400）：SMILES 非法是
    用户的正常输入结果，不是服务故障。
    """
    from app.chem.engine import get_engine

    request_id = new_request_id()
    try:
        result = get_engine().parse(payload.smiles)
    except Exception as exc:  # noqa: BLE001 - 统一转为受控错误响应
        spec = build_error_payload(exc, request_id=request_id)
        code = spec["error"]["code"]
        # 化学错误对用户是"你输入的结构式不对"，不是服务端故障
        status_code = 400 if code.startswith(("chem_", "api_invalid")) else 500
        spec["error"]["code"] = code
        return JSONResponse(
            status_code=status_code,
            content=MoleculeResponse(
                request_id=request_id, ok=False, error=spec["error"]
            ).model_dump(),
        )

    data = result.to_dict()
    # **只输出 matched=True 的官能团**。
    #
    # 实测踩过的坑：`FunctionalGroupHit` 对**全部** 11 个模式都返回一条记录，
    # 未命中的用matched=False + 空 atom_indices 标记。初版直接透传，
    # 结果乙醇/乙酸/苯酚全都显示"含醛基、羧基、酯基、氨基、碳碳三键"——
    # 等于把"检测清单"当成"检测结果"报给用户。
    # 底层数据是对的（乙醇只有羟基 matched=true），是本层的过滤缺失。
    groups = [
        g.to_dict()
        for g in (result.functional_groups or ())
        if g.matched
    ]
    ok = bool(result.ok)
    return JSONResponse(
        status_code=200,
        content=MoleculeResponse(
            request_id=request_id,
            ok=ok,
            properties=data.get("properties"),
            structure=data.get("structure"),
            functional_groups=groups if ok else [],
            verification=str(data.get("verification", "")),
            notes=[str(n) for n in (result.notes or ())],
            warnings=[str(w) for w in (result.warnings or ())],
            error=None
            if ok
            else {
                "code": "chem_invalid_structure",
                "message": "无法解析该结构式，请检查写法。",
                "retryable": False,
                "request_id": request_id,
            },
        ).model_dump(),
    )


__all__ = ["get_registry", "router"]
