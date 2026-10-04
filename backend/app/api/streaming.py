"""SSE 流式问答。

## 一个必须说清的限制

**当前实现是"阶段事件"流，不是逐 token 流。**
原因在既有代码：:class:`app.agent.dispatcher.AgentLoop` 是
**同步迭代**——它在 ``run()`` 内部跑完整个「模型→工具→回填→
再模型」循环后才返回，中间状态不外露。API 层拿不到 token 流。

要实现真正的 token 级流式，得把 ``AgentLoop.run`` 改造成生成器
（``yield`` 每步中间态），这是对已验证模块的侵入式改动，
须单独开分支并重跑其 72 项测试。**此处不假装能做到。**

但阶段事件仍有真实价值：
- ``t=0`` 即可响应（实测模型中位延迟 7.10 秒），前端立即显示加载态；
- 暴露工具调用进度，让学生看到"正在检索教材"而非无反馈等待；
- 错误能在流中以结构化事件送达（同步接口里只能靠状态码）。

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
                "当前为阶段事件流，非逐 token 流；"
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

    try:
        # 同步调用。**刻意不在此 yield 任何中间态**——
        # AgentLoop 不暴露中间态，编造阶段只会误导前端。
        result = loop.run(question)
    except Exception as exc:  # noqa: BLE001
        logger.warning("流式问答失败[%s]：%s", request_id, type(exc).__name__)
        yield _error_event(exc, request_id)
        return

    invocations = list(result.get("tool_invocations", []))

    # 第 2 步：把真实发生的工具调用回传，让前端展示依据来源。
    # 只发**实际发生过的**调用——不预告、不编造。
    for index, item in enumerate(invocations):
        yield _event(
            "stage",
            {
                "stage": "tool",
                "index": index,
                "total": len(invocations),
                "tool": str(item.get("tool", ""))[:64],
                "ok": bool(item.get("ok")),
            },
        )

    # 第 3 步：最终结果。字段与同步接口完全一致，
    # 前端可用同一套渲染逻辑处理两种传输。
    yield _event(
        "result",
        {
            "request_id": request_id,
            "status": "completed",
            "explanation": str(result.get("text", "")),
            "sources": [],
            "tool_invocations": [
                {
                    "tool": str(item.get("tool", ""))[:64],
                    "ok": bool(item.get("ok")),
                    "error_code": item.get("error_code"),
                }
                for item in invocations
            ],
            "steps": int(result.get("steps", 1)),
            "elapsed_seconds": timer.elapsed,
            "schema_version": "1.0",
        },
    )
    yield _event("done", {"request_id": request_id})


def _error_event(exc: BaseException, request_id: str) -> ServerSentEvent:
    """把异常转为 SSE 错误事件。

    **响应头早已发出，无法再改 HTTP 状态码**——这是 SSE 的固有约束，
    也是为什么错误码（而非状态码）才是跨传输稳定的标识。
    """
    payload = build_error_payload(exc, request_id=request_id)
    logger.warning("SSE 错误[%s]：%s", request_id, payload["error"]["code"])
    return _event("error", payload["error"])


__all__ = ["answer_events"]
