"""SSE 流式问答。

## 事件序列

```
meta     请求一开始就发，让前端立即渲染加载态并显示 request_id
stage    阶段进展（thinking / continuing / tool）
delta    **token 增量**，学生看到文字逐渐出现
source   知识来源，在检索发生的瞬间推送
result   最终答复，字段与同步接口一致
done     收尾
error    失败（此时 HTTP 状态码只能是 200，见下）
```

## 逐token 流已实现（2026-10-04，决策项 I3）

**此前是"阶段事件流"**——`AgentLoop` 是同步迭代，API 层拿不到中间态。
现已把 :meth:`AgentLoop.stream` 改造为生成器，``run()`` 改为消费它，
本模块直接转发其事件。

依据（均为实测，非推断）：
- 华为 MaaS 的 OpenAI 兼容端点**支持** ``stream:true``，
  SSE 线格式为标准 ``chat.completion.chunk``
  （官方文档 ``model-call-101`` 有"流式输出"示例）；
- ``bind_tools`` 后的 LangChain Runnable **同时**有 ``.stream()``
  与 ``.invoke()``，故流式与工具调用**不需要两套逻辑**；
- ``AIMessageChunk`` 是 ``AIMessage`` 子类且实现 ``__add__``，
  合并时会自动按 ``index`` 归并 ``tool_calls`` 的参数分片。

**中间轮不产 delta**：模型可能先吐思考文本再吐 tool_calls，
把思考过程当答复展示会误导学生。故 delta 只在本轮无工具调用时发出。

## 为什么错误仍是 HTTP 200

SSE 响应头一旦发出就无法再改状态码（实测：Agent 抛错时流式仍返 200）。
这正是错误码而非状态码才是跨传输稳定标识的原因。

## 中文转义实测

SSE 的 ``data`` 字段用 JSON 编码，而 FastAPI 默认 ``ensure_ascii=True``，
故中文会被转成 ``\\u82ef\\u915a`` 形式（**实测确认**）。
浏览器端 ``EventSource`` 收到后须``JSON.parse`` 才能还原为中文——
直接 ``event.data`` 显示会是转义串。此行为须写入前端契约文档。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from fastapi.sse import ServerSentEvent

from app.agent.dispatcher import AgentLoop
from app.api.deps import ServiceRegistry, Timer, new_request_id
from app.api.errors import build_error_payload

logger = logging.getLogger(__name__)


def _event(name: str, data: dict[str, Any]) -> ServerSentEvent:
    """构造一个 SSE 事件。"""
    return ServerSentEvent(event=name, data=data)


def answer_events(
    question: str,
    registry: ServiceRegistry,
) -> Iterator[ServerSentEvent]:
    """生成一次问答的 SSE 事件序列。

    **必须是生成器函数，不能返回 ``EventSourceResponse``**——
    这是实测踩过的坑：路由声明了 ``response_class=EventSourceResponse``
    后，FastAPI 会把返回值当作**事件项的异步迭代源**来消费。
    若此处再包一层``EventSourceResponse``，就会报
    ``TypeError: 'EventSourceResponse' object is not iterable``。
    对照实测：声明 ``response_class`` + 直接 ``yield`` 可行；
    不声明而返回 ``EventSourceResponse`` 实例则报
    ``AttributeError: 'ServerSentEvent' object has no attribute 'encode'``。

    Args:
        question: 已通过 Pydantic 校验的问题文本。
        registry: 服务容器。

    Yields:
        :class:`ServerSentEvent` 序列，见 :func:`app.api.routes.ask_stream`。
    """
    request_id = new_request_id()

    # 第 0 步：立刻发 meta。前端据此渲染加载态并显示 request_id。
    # 这一步不触碰任何模型/向量库，故无论后端是否可用都能发出——
    # 保证「连接成功但无响应」这种最坏情况不会发生。
    yield _event(
        "meta",
        {
            "request_id": request_id,
            "schema_version": "1.0",
            "note": (
                "逐 token 流已启用：delta 事件须追加拼接；"
                "最终完整答复在 result 事件中。"
            ),
        },
    )

    timer = Timer()

    # 第 1 步：告诉前端即将开始真实工作。
    yield _event("stage", {"stage": "thinking", "message": "正在分析问题"})

    try:
        loop = registry.get_agent_loop()
    except Exception as exc:  # noqa: BLE001 - 装配失败也要走 SSE 错误通道
        yield _error_event(exc, request_id)
        return

    # 第 2 步起：消费 Agent 的事件流。
    #
    # **为何不再直接调 run()**：AgentLoop.stream() 才是真正的事件源
    # （决策项 I3，2026-10-04）。改用它之后：
    # - token 增量能实时转发（delta 事件），学生看到文字逐渐出现；
    # - 工具调用与来源在发生的瞬间就能推送，不必等全部结束。
    #
    # 同步接口走 run()，流式走 stream()，**两者共用同一循环**，
    # 不会出现"同步能答、流式答不通"的分裂。
    invocations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    final_text = ""
    steps = 0

    try:
        for event in loop.stream(question):
            kind = event.get("type")

            if kind == AgentLoop.EVENT_DELTA:
                # 逐块转发。前端须**追加**而非覆盖。
                # 注意：SSE 的 data 字段里中文会被转义为 \uXXXX，
                # 浏览器须 JSON.parse 才能还原（实测行为）。
                yield _event("delta", {"text": str(event.get("text", ""))})

            elif kind == AgentLoop.EVENT_TOOL:
                # 工具调用是**独立事件类型**，不是 stage 的子情形——
                # 初版把它嵌在 stage 分支里，导致 tool 事件被静默丢弃
                # （实测抓到：两个工具调用一个都没出现在流里）。
                record = {
                    "tool": str(event.get("tool", ""))[:64],
                    "ok": bool(event.get("ok")),
                    "error_code": event.get("error_code"),
                }
                invocations.append(record)
                yield _event(
                    "tool",
                    {"index": len(invocations) - 1, **record},
                )

            elif kind == AgentLoop.EVENT_STAGE:
                stage = str(event.get("stage", "")) or "thinking"
                yield _event(
                    "stage",
                    {
                        "stage": stage[:32],
                        "message": "正在分析问题"
                        if stage == "thinking"
                        else "正在继续推理",
                        "step": int(event.get("step", 1)),
                    },
                )

            elif kind == AgentLoop.EVENT_SOURCE:
                found = list(event.get("sources") or [])
                sources.extend(found)
                # 来源实时推送——学生能在答复生成过程中就看到依据
                yield _event(
                    "source",
                    {"sources": [_source_payload(item) for item in found]},
                )

            elif kind == AgentLoop.EVENT_DONE:
                final_text = str(event.get("text", ""))
                steps = int(event.get("steps", 1))
                # 以 Agent 层的最终值为准（与上面累积的一致，双保险）
                invocations = list(event.get("tool_invocations") or invocations)
                sources = list(event.get("sources") or sources)

    except Exception as exc:  # noqa: BLE001
        logger.warning("流式问答失败[%s]：%s", request_id, type(exc).__name__)
        yield _error_event(exc, request_id)
        return

    # 第 3 步：最终结果。字段与同步接口完全一致，
    # 前端可用同一套渲染逻辑处理两种传输。
    knowledge_failed = any(
        inv["tool"] == "search_knowledge" and not inv["ok"] for inv in invocations
    )
    knowledge_ok = any(
        inv["tool"] == "search_knowledge" and inv["ok"] for inv in invocations
    )
    yield _event(
        "result",
        {
            "request_id": request_id,
            # 检索失败但文本仍返回 —— 与同步接口同一口径
            "status": "partial"
            if (knowledge_failed and not knowledge_ok)
            else "completed",
            "explanation": final_text,
            "sources": [_source_payload(item) for item in sources],
            "tool_invocations": invocations,
            "steps": max(1, steps),
            "elapsed_seconds": timer.elapsed,
            "schema_version": "1.0",
        },
    )
    yield _event("done", {"request_id": request_id})


def _source_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """把 Agent 层的来源 dict 转成 SSE 载荷。

    与同步接口的 :class:`SourceItem` 保持字段一致，
    这样前端处理两种传输时能用同一套逻辑。
    """
    return {
        "source_id": str(raw.get("source_id", ""))[:128],
        "title": str(raw.get("title", ""))[:512],
        "edition": str(raw.get("edition", ""))[:128],
        "locator": str(raw.get("locator", ""))[:256],
        "scope": str(raw.get("scope", ""))[:64],
    }


def _error_event(exc: BaseException, request_id: str) -> ServerSentEvent:
    """把异常转为 SSE 错误事件。

    **响应头早已发出，无法再改 HTTP 状态码**——这是 SSE 的固有约束，
    也是为什么错误码（而非状态码）才是跨传输稳定的标识。
    """
    payload = build_error_payload(exc, request_id=request_id)
    logger.warning("SSE 错误[%s]：%s", request_id, payload["error"]["code"])
    return _event("error", payload["error"])


__all__ = ["answer_events"]
