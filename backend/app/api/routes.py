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
| POST | ``/api/v1/speak`` | 语音合成，返回音频 id |
| GET | ``/api/v1/speak/{audio_id}`` | 取已合成的音频 |
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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import ServiceRegistry, Timer, new_request_id
from app.api.errors import _AudioGoneError, build_error_payload, classify
from app.api.models import (
    AnswerResponse,
    AskRequest,
    ComponentStatus,
    HealthResponse,
    MoleculeRequest,
    MoleculeResponse,
    RequestStatus,
    SourceItem,
    SourceLocator,
    SpeechRequest,
    SpeechResponse,
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
            # caps 必须显式透传：探针层填了，但这里漏掉的话
            # 会被 default_factory 静默填成 {}——前端于是以为
            # 数字人未启用，而实际是"本层没接线"。
            # 实测踩过：加了字段却忘了在这里传，/health 返回空caps，
            # 而探针层明明是正确的。
            ComponentStatus(name=p.name, ready=p.ready, detail=p.detail, caps=p.caps)
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


def _to_source_item(raw: dict[str, Any]) -> SourceItem:
    """把 Agent 层的来源 dict 转成 API 契约模型。

    Agent 层给出的是纯 dict（``_extract_sources`` 的输出），
    本层负责映射到契约模型并**兜住超长字段**——
    ``SourceItem`` 各字段都有 ``max_length`` 约束，
    直接构造会因超长抛 ``ValidationError``，
    那会让一个"来源标题过长"的边缘情况导致整个请求 500。
    故此处按契约上限截断，不让非核心字段拖垮主流程。

    Args:
        raw: Agent 层产出的来源字典。缺字段时给空串而非报错——
            来源是展示信息，不该让问答失败。

    Returns:
        可安全返回的 :class:`SourceItem`。
    """

    def _cut(value: Any, limit: int) -> str:
        return str(value or "")[:limit]

    locator = _cut(raw.get("locator"), 256)
    return SourceItem(
        source_id=_cut(raw.get("source_id"), 128) or "unknown",
        title=_cut(raw.get("title"), 512) or "未命名来源",
        locator=locator,
        # 定位类型由文本形态推断：纯数字/带"第..页"视为页码，其余为章节。
        # 推断失败落到 UNKNOWN，不编造精确类型。
        locator_kind=(
            SourceLocator.PAGE
            if _looks_like_page(locator)
            else SourceLocator.SECTION
            if locator
            else SourceLocator.UNKNOWN
        ),
        version=_cut(raw.get("edition"), 128),
        # Agent 层不返回审核状态，故标unknown——
        # **不写"approved"**：那是审核结论，不是事实（§5 要求如实标注）。
        review_status="unknown",
    )


def _looks_like_page(locator: str) -> bool:
    """判断定位串是否形如页码。

    识别 ``p.42`` / ``第42页`` / ``42`` 三种写法。
    判不准就返回 False——落到 ``section`` 或 ``unknown`` 都比猜错好。
    """
    if not locator:
        return False
    text = locator.strip().lower()
    if text.startswith("p.") or text.startswith("pp."):
        return True
    return "页" in text


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

    # 检索成功与否体现在工具调用记录里。
    #
    # sources 的来源：AgentLoop.stream 在 search_knowledge 成功时
    # 解析工具返回的 JSON 并产出 source 事件，run() 汇总到返回值
    # （决策项 I2，2026-10-04 实现）。此前的空数组是因为
    # AgentLoop 拿不到结构化来源——现已解决。
    #
    # 仍然坚持的原则：**来源只来自工具的真实返回**。
    # 上游 Agent 层已做「工具失败不提取来源」的处理，此处不再重复判断，
    # 但保留一道防御：字段缺失时给空列表而非 None。
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
        sources=[_to_source_item(item) for item in result.get("sources") or []],
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
        # 状态码**取 classify 的权威结果**，不在此处手工推导。
        #
        # 旧写法是 `400 if code.startswith(("chem_","api_invalid")) else 500`，
        # 问题是它与 :func:`classify` 的登记表**各说各话**：
        # classify 认定 `api_component_unavailable` 是 503，
        # 这里却因不匹配前缀而落回 500——**等于把刚修好的语义又抹掉**。
        #
        # 单一事实源：状态码只由 classify 决定，路由不再自行判断。
        spec_info = classify(exc)
        payload = build_error_payload(exc, request_id=request_id)
        return JSONResponse(
            status_code=spec_info.http_status,
            content=MoleculeResponse(
                request_id=request_id, ok=False, error=payload["error"]
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


# ----------------------------------------------------------------------
# 语音（决策 H21）
# ----------------------------------------------------------------------


def get_audio_store(request: Request) -> Any:
    """从应用状态取音频存储。

    存储挂在 ``app.state`` 上而非模块级全局——
    与 :class:`~app.api.deps.ServiceRegistry` 同一理由：
    测试可造多个互不干扰的应用实例。
    """
    return request.app.state.audio_store


@router.post(
    "/api/v1/speak",
    response_model=SpeechResponse,
    tags=["语音"],
)
def synthesize_speech(
    payload: SpeechRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> JSONResponse:
    """合成语音。

    ## 永不失败（决策：语音可降级）

    ``architecture.md`` §6 要求语音是**可降级能力**。
    故本接口**总是返回 200**：TTS 或数字人失败都体现在
    ``stage`` 字段里，而不是 HTTP 错误码。

    前端因此不需要 try/except 之外的分支——
    文本答案的交付不会因为语音失败而中断。
    """
    from app.speech import SpeechService

    request_id = new_request_id()
    service = SpeechService()
    outcome = service.speak(
        payload.text,
        push_digital_human=payload.push_digital_human,
        user=payload.user,
    )
    speech = outcome.speech

    audio_id: str | None = None
    audio_url: str | None = None
    if outcome.available and speech.audio_path:
        from pathlib import Path

        store = get_audio_store(request)
        try:
            record = store.put(Path(speech.audio_path))
        except ValueError as exc:
            # 产物有问题（如为空、超体积）。这属于**服务端问题**
            # 而非用户输入错误，故仍返回 200 + unavailable——
            # 保持"语音永不阻断文本主交付"的一致性。
            logger.warning("语音产物入库失败[%s]：%s", request_id, exc)
            return JSONResponse(
                status_code=200,
                content={
                    "request_id": request_id,
                    "stage": "unavailable",
                    "available": False,
                    "audio_id": None,
                    "audio_url": None,
                    "reason": "语音产物无法保存。",
                    "truncated": speech.truncated,
                    "char_count": speech.char_count,
                    "digital_human_delivered": outcome.digital_human.delivered,
                },
            )
        audio_id = record.audio_id
        # URL 由服务端给出：前端不拼路径，契约变了无须改前端
        audio_url = str(request.url_for("get_speech_audio", audio_id=audio_id))

    return JSONResponse(
        status_code=200,
        content={
            "request_id": request_id,
            "stage": speech.stage,
            "available": outcome.available and audio_id is not None,
            "audio_id": audio_id,
            "audio_url": audio_url,
            "reason": speech.reason,
            "truncated": speech.truncated,
            "char_count": speech.char_count,
            "digital_human_delivered": outcome.digital_human.delivered,
        },
    )


@router.get(
    "/api/v1/speak/{audio_id}",
    response_class=FileResponse,
    tags=["语音"],
    name="get_speech_audio",
)
def get_speech_audio(
    audio_id: str,
    request: Request,
    store: Any = Depends(get_audio_store),
) -> FileResponse:
    """取已合成的音频。

    ## 过期与不存在都回 404（刻意不区分）

    区分会让「曾经存在过」成为可观测信息，而前端不需要知道
    （`security-privacy.md` §3）。统一 404 也不泄露任何内部信息。

    ## 清理不挂在 BackgroundTask 上

    **实测踩到（关键）**：starlette 1.7.0 的 ``BackgroundTask``
    **没有 shield 保护**（读源码确认），而本项目**正好用了**
    ``BaseHTTPMiddleware``——这正是 starlette #1438 报告的组合：
    **客户端断开连接时后台任务被取消**。

    若把"响应完就删"挂在这里，学生一关页面音频就永久残留。
    故清理改由 :class:`~app.speech.store.AudioStore` 承担：
    本次取用时惰性判断超龄，另加定时清扫。
    """
    record = store.fetch(audio_id)
    if record is None:
        # 过期或不存在：都不给。错误体用统一错误契约，
        # 但**不区分二者**（见上方 docstring）
        exc = _AudioGoneError()
        payload = build_error_payload(exc, request_id=new_request_id())
        return JSONResponse(
            status_code=404, content=payload, media_type="application/json"
        )

    return FileResponse(
        path=record.path,
        media_type="audio/mpeg",
        # 不设 Content-Disposition: attachment——那会让浏览器
        # 触发下载而非播放。inline 是这里想要的。
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )

