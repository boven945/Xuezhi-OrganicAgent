"""工具调度与 Agent 主循环。

实测约束（2026-10-03，openpangu-2.0-flash + openai 3.24.0）：

- ``tool_choice`` **只接受** ``"none"`` / ``"auto"`` / ``"required"``；
  传具体的 ``{"type":"function","function":{"name":...}}`` 会报
  ``ModelArts.81001``。因此本层**不使用**精确点名，工具收敛靠白名单。
- 通过 LangChain ``bind_tools`` 调用时，返回的是 **``AIMessage``（没有
  ``choices``）**；``AIMessage.tool_calls`` 的元素是 **dict**，形如
  ``{"name": ..., "args": {...}, "id": ..., "type": "tool_call"}``，
  其中 ``args`` **已是 dict**（不是 OpenAI 原始格式的 JSON 字符串）。
- ``id`` 形如 ``chatcmpl-tool-<hex>``；回填工具结果时用
  ``{"role": "tool", "tool_call_id": <id>, "content": ...}``。
- **``AIMessage`` 可原样追加进消息列表回传**（实测可行，无需手工转 dict）。
- 模型默认开启深度思考，``reasoning_content`` 计入 token 预算，
  故 ``max_completion_tokens`` 不可设小。
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable, Sequence

from .errors import AgentStepLimitError, ToolError
from .tools import ToolRegistry, ToolResult, validate_arguments

logger = logging.getLogger(__name__)

#: Agent 最大迭代轮数。防止"模型↔工具"循环（`architecture.md` §6）。
DEFAULT_MAX_STEPS = 6


class ToolDispatcher:
    """执行模型请求的工具调用。

    职责（`architecture.md` §2）：
    验证工具名与参数、调用受控函数、收集结果、隔离失败。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, name: str, raw_arguments: str | dict[str, Any]) -> ToolResult:
        """执行单个工具调用。

        **本方法不抛异常**（除编程错误外），失败以 :class:`ToolResult`
        返回 ``ok=False``。这样上层能明确区分"工具失败"与"无结果"，
        符合 `architecture.md` §5「不可用服务不得被静默替换」。

        Raises:
            ToolNotFoundError: 工具未注册时**抛出**——这是白名单边界，
                属于需要调用方显式处理的安全事件，不应静默。
        """
        # 1) 白名单校验。未注册即拒绝，不做任何降级
        tool = self._registry.get(name)

        # 2) 参数校验。失败转为结构化结果回填给模型
        try:
            args = validate_arguments(tool, raw_arguments)
        except ToolError as exc:
            return ToolResult(
                ok=False,
                error_code=exc.code,
                error_message=exc.user_message,
            )

        # 3) 执行。带独立超时（architecture.md §6）
        started = time.perf_counter()
        try:
            content = self._call_with_timeout(tool, args)
        except FutureTimeout:
            logger.warning("工具执行超时: tool=%s", name)
            return ToolResult(
                ok=False,
                error_code="tool_timeout",
                error_message=f"工具 {name} 执行超时，请稍后重试或换一种问法。",
            )
        except ToolError as exc:
            logger.warning("工具执行失败: tool=%s code=%s", name, exc.code)
            return ToolResult(
                ok=False, error_code=exc.code, error_message=exc.user_message
            )
        except Exception as exc:  # 兜底：绝不把底层异常透给模型
            logger.warning(
                "工具异常: tool=%s type=%s", name, type(exc).__name__
            )
            return ToolResult(
                ok=False,
                error_code="tool_execution_failed",
                error_message="工具执行出错，无法提供可靠结果。",
            )

        logger.info(
            "工具执行完成: tool=%s elapsed=%.3fs", name, time.perf_counter() - started
        )
        # 4) 结果必须是字符串（供模型读取）。非字符串则序列化，
        #    但保持结构化，不交给模型可执行内容。
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        return ToolResult(ok=True, content=content)

    @staticmethod
    def _call_with_timeout(tool: Any, args: dict[str, Any]) -> Any:
        """在独立线程中执行工具，带超时。

        用线程而非信号量，以便超时后线程自然结束；
        工具本身应保持轻量（RDKit 解析在毫秒级）。
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.handler, **args)
            return future.result(timeout=tool.timeout)


class AgentLoop:
    """驱动模型与工具交互的主循环。

    流程（`architecture.md` §3）：
        用户问题 → 模型决策 → 工具调用 → 回填结果 → 直至模型给出最终答复。

    传输方式（同步 / 流式 / 任务轮询）属待决策项 A3，
    本实现采用**同步迭代**，不做流式。
    """

    def __init__(
        self,
        client: Any,
        dispatcher: ToolDispatcher,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        history: Sequence[tuple[str, str]] | None = None,
    ) -> None:
        self._client = client
        self._dispatcher = dispatcher
        self._max_steps = max_steps
        self._history = list(history or [])

    def run(self, question: str) -> dict[str, Any]:
        """执行一次完整问答。

        Returns:
            含 ``text``（最终答复）、``steps``（迭代轮数）、
            ``tool_invocations``（工具调用记录）的结果字典。

        Raises:
            AgentStepLimitError: 达到最大轮数仍未得出答复。
            app.llm.errors.LLMError: 模型侧错误（由上层处理）。
        """
        messages: list[dict[str, Any]] = [
            {"role": role, "content": content} for role, content in self._history
        ]
        messages.append({"role": "user", "content": question})

        tools = self._dispatcher._registry.to_openai_tools()  # noqa: SLF001
        invocations: list[dict[str, Any]] = []

        for step in range(1, self._max_steps + 1):
            # 每一轮都传 tools：模型据此决定是否继续调用
            message = self._client.invoke_with_tools(messages, tools)
            tool_calls = self._extract_tool_calls(message)

            if not tool_calls:
                return {
                    "text": self._extract_text(message),
                    "steps": step,
                    "tool_invocations": invocations,
                }

            # AIMessage 可原样追加回传（实测可行），这样 tool_calls 得以保留，
            # 模型才能把工具结果与调用对应起来
            messages.append(message)

            for call in tool_calls:
                name = call["name"]
                result = self._dispatcher.execute(name, call.get("args") or {})
                invocations.append(
                    {
                        "tool": name,
                        "ok": result.ok,
                        "error_code": result.error_code,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result.to_model_payload(),
                    }
                )

        raise AgentStepLimitError(
            f"模型与工具交互超过 {self._max_steps} 轮仍未给出结论，"
            "已停止以避免重复请求。请换一种问法或补充条件。"
        )

    @staticmethod
    def _extract_tool_calls(message: Any) -> list[dict[str, Any]]:
        """从 AIMessage 中取出规范化的 tool_calls。

        实测（2026-10-03，langchain-openai 1.6.7）：元素为 dict，
        键为 ``name`` / ``args`` / ``id`` / ``type``，``args`` 已是 dict。

        此处做形状校验：不符合预期的调用直接跳过而非让循环崩溃——
        模型输出不可控，但**不能因此中断整个问答**。
        """
        raw = getattr(message, "tool_calls", None)
        if not raw:
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                logger.warning("跳过形状异常的 tool_call: %s", type(item).__name__)
                continue
            name = item.get("name")
            call_id = item.get("id")
            if not name or not call_id:
                logger.warning("跳过缺少 name/id 的 tool_call")
                continue
            normalized.append(
                {
                    "name": str(name),
                    "id": str(call_id),
                    "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                }
            )
        return normalized

    @staticmethod
    def _extract_text(message: Any) -> str:
        content = getattr(message, "content", None)
        text = content if isinstance(content, str) else ""
        if not text.strip():
            # 空回复明确报错，不让上层把空串当成功
            from app.llm.errors import LLMError

            raise LLMError("模型返回内容为空。", code="llm_empty_response")
        return text


__all__ = ["ToolDispatcher", "AgentLoop", "DEFAULT_MAX_STEPS"]
