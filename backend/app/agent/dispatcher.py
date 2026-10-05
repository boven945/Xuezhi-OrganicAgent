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
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable

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

    两种传输方式（决策项A3 已推进，见 interface-contract-verification.md）：

    - :meth:`run` —— 同步迭代，返回完整结果字典。
    - :meth:`stream` —— 生成器，逐步产出事件（token 增量 / 工具调用 / 来源）。

    **两者共用同一套循环骨架**：``run`` 是``stream`` 的消费者
    （见 :meth:`run` 实现）。这样设计的原因：若各写一套，
    极易出现"同步路径验证过、流式路径没跑到"的分裂——
    教学系统里两个路径给出不同答案比慢更糟。
    """

    def __init__(
        self,
        client: Any,
        dispatcher: ToolDispatcher,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        history: Sequence[tuple[str, str]] | None = None,
        persona: str | None = None,
    ) -> None:
        """
        Args:
            persona: 可选的数字人设（见 :mod:`app.llm.persona`）。
                传``None``（默认）时只保留功能契约，
                行为与引入人设前完全一致。
        """
        self._client = client
        self._dispatcher = dispatcher
        self._max_steps = max_steps
        self._history = list(history or [])
        # 人设原样保存，实际拼接在 LLMClient.compose_system_prompt——
        # 那里同时负责保留功能契约，不让人设把它覆盖掉。
        self._persona = persona

    def _persona_for(self, question: str) -> str | None:
        """按问题生成人设提示词。

        :param question: 学生本次提问，用作「当前课题」。
        :returns: 人设提示词；未启用人设时返回 ``None``。

        **为何直接把问题当课题**：
        学生问「乙醇的官能团是什么」，
        课题就是「乙醇的官能团是什么」——原样传入比规则抽取更准。
        规则抽取（关键词/去停用词）容易把关键限定词丢掉，
        而漏掉限定词的课题会让讲解跑偏。

        真正的裁剪由模型自己完成：人设里说的是
        「围绕它组织讲解，不要偏离」，而不是「只回答这几个字」。

        **为何 ``None`` 要提前返回**：
        未启用人设时返回 ``None``，
        让 :func:`compose_system_prompt` 走「只保留功能契约」那条路，
        行为与引入人设前完全一致。
        """
        if not self._persona:
            return None
        from app.llm.persona import build_teacher_prompt

        return build_teacher_prompt(question)

    # ------------------------------------------------------------------
    # 事件类型
    # ------------------------------------------------------------------

    #: 事件类型常量。字符串字面量而非 Enum——这些值会进JSON 响应，
    #: 用裸字符串可让前端不必解析 Python 侧枚举。
    EVENT_DELTA = "delta"
    EVENT_STAGE = "stage"
    EVENT_TOOL = "tool"
    EVENT_SOURCE = "source"
    EVENT_DONE = "done"

    def stream(self, question: str) -> Iterator[dict[str, Any]]:
        """流式执行一次问答，逐步产出事件。

        事件序列::

            {"type": "stage",  "stage": "thinking", ...}
            {"type": "delta",  "text": "苯酚"}          # 逐 token，可选
            {"type": "tool",   "tool": "search_knowledge", "ok": True}
            {"type": "source", "sources": [...]}        # 结构化来源
            {"type": "done",   "text": 完整答复, "steps": 2}

        Args:
            question: 学生问题（调用方须已校验）。

        Yields:
            事件字典。**最后一件事件必定是 ``done``**（成功时）
            或抛出异常（失败时）。

        Raises:
            AgentStepLimitError: 超过最大轮数。
            app.llm.errors.LLMError: 模型侧错误。

        Notes:
            **``delta`` 事件依赖上游支持流式**。实测华为 MaaS 支持
            ``stream:true``（官方文档 model-call-101 有"流式输出"示例），
            但若 client 不支持流式，本方法**自动降级为只发stage/done**，
            不报错——降级优于失败（`architecture.md` §6）。
        """
        messages: list[dict[str, Any]] = [
            {"role": role, "content": content} for role, content in self._history
        ]
        messages.append({"role": "user", "content": question})

        tools = self._dispatcher._registry.to_openai_tools()  # noqa: SLF001
        invocations: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []

        for step in range(1, self._max_steps + 1):
            yield {
                "type": self.EVENT_STAGE,
                "stage": "thinking" if step == 1 else "continuing",
                "step": step,
            }

            # 优先走流式；不可用或失败则退回一次性调用。
            # 两种模式产出的 message 形态一致（AIMessageChunk 是
            # AIMessage子类），故后续处理代码无须分支。
            message: Any = None
            if getattr(self._client, "supports_streaming", True):
                chunks: list[Any] = []
                # 缓存本轮的文本增量，待确认"本轮不调工具"后再 yield。
                # 原因：模型可能先吐思考文本再吐 tool_calls，
                # 提前 yield 会把思考过程当成答复展示给学生。
                pending: list[str] = []
                try:
                    for chunk in self._client.stream_with_tools(messages, tools):
                        chunks.append(chunk)
                        text = getattr(chunk, "text", None)
                        if text and not getattr(chunk, "tool_calls", None):
                            pending.append(text)
                    if chunks:
                        message = _merge_chunks(chunks)
                        # 本轮无工具调用 → 文本是最终答复，补发 delta。
                        # 放在合并之后：此时才知道该不该给学生看。
                        if pending and not self._extract_tool_calls(message):
                            for piece in pending:
                                yield {"type": self.EVENT_DELTA, "text": piece}
                except NotImplementedError:
                    logger.info("客户端不支持流式，本轮用同步调用")
                    message = None
                except Exception:
                    # 流式失败**不让整个请求失败**——降级重试。
                    # 教学场景下「慢但有答案」远好于「快但报错」。
                    logger.warning("流式调用失败，降级为同步: step=%d", step, exc_info=True)
                    message = None

            if message is None:
                message = self._client.invoke_with_tools(
                    messages, tools, persona=self._persona_for(question)
                )

            tool_calls = self._extract_tool_calls(message)

            if not tool_calls:
                text = self._extract_text(message)
                yield {
                    "type": self.EVENT_DONE,
                    "text": text,
                    "steps": step,
                    "tool_invocations": invocations,
                    "sources": sources,
                }
                return

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
                yield {
                    "type": self.EVENT_TOOL,
                    "tool": name,
                    "ok": result.ok,
                    "error_code": result.error_code,
                }
                # 来源信息只在该工具成功时提取；失败时 result.content 是
                # 错误 JSON，从中提"来源"会得到伪造条目——
                # `interface-contract.md` §3 要求「无来源时为空，不伪造」。
                if result.ok and name == "search_knowledge":
                    found = _extract_sources(result.content)
                    if found:
                        sources.extend(found)
                        yield {
                            "type": self.EVENT_SOURCE,
                            "sources": found,
                        }

        raise AgentStepLimitError(
            f"模型与工具交互超过 {self._max_steps} 轮仍未给出结论，"
            "已停止以避免重复请求。请换一种问法或补充条件。"
        )

    def run(self, question: str) -> dict[str, Any]:
        """执行一次完整问答（同步）。

        **实现为 :meth:`stream` 的消费者**——刻意不另写循环：
        两条路径共用一套逻辑，杜绝"同步能跑、流式跑不通"的分裂
        （这是改造 :meth:`stream` 时最可能的失败模式）。

        Returns:
            含 ``text``（最终答复）、``steps``（迭代轮数）、
            ``tool_invocations``（工具调用记录）、
            ``sources``（结构化来源，2026-10-04 新增）的结果字典。

        Raises:
            AgentStepLimitError: 达到最大轮数仍未得出答复。
            app.llm.errors.LLMError: 模型侧错误（由上层处理）。
        """
        text = ""
        steps = 0
        invocations: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []

        for event in self.stream(question):
            kind = event.get("type")
            if kind == self.EVENT_DONE:
                text = str(event.get("text", ""))
                steps = int(event.get("steps", 1))
                invocations = list(event.get("tool_invocations") or [])
                sources = list(event.get("sources") or [])

        return {
            "text": text,
            "steps": steps,
            "tool_invocations": invocations,
            "sources": sources,
        }

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


#: 知识库片段的 JSON 中，来源数组的键名与字段。
#:
#: **实测依据**（2026-10-04，容器内跑``search_knowledge`` 工具得到）：
#:顶层键为 ``found`` / ``note`` / ``passages`` / ``retrieval_meta``，
#: 每个 passage 含 ``source_id`` / ``title`` / ``edition`` / ``locator`` /
#: ``scope`` / ``text``。权威定义见 :meth:`app.rag.models.RetrievalResult.to_model_payload`。
#:
#: 这里刻意**不用检索层的内部结构**，而是解析工具返回的 JSON——
#: 因为那是跨模块边界唯一稳定的契约。若改为直接访问
#: ``RetrievalResult.chunks``，就把 Agent 层耦合到了 RAG 层的内部实现。
_PASSAGES_KEY = "passages"

#: 允许透出的来源字段。``text`` 刻意**不在其中**：
#: 片段原文已通过模型答复传递，再单独发一遍只会让 SSE 体积翻倍。
_SOURCE_FIELDS = ("source_id", "title", "edition", "locator", "scope")


def _merge_chunks(chunks: list[Any]) -> Any:
    """把流式的 ``AIMessageChunk`` 序列合并为单个 ``AIMessage``。

    为什么能直接相加：**实测** ``AIMessageChunk`` 是 ``AIMessage`` 的子类
    且实现了 ``__add__``，合并时会自动：
    - 拼接 ``content``；
    - 按 ``index`` 归并 ``tool_call_chunks`` 的 ``arguments`` 分片，
      并在完整后把 ``args`` 解析成 dict。

    实测细节：只有半个 JSON 时访问 ``.tool_calls`` **不报错**，
    而是返回 ``args={}``。故调用方不能只看 ``tool_calls`` 非空
    来判断"模型是否调用了工具"——必须结合 ``finish_reason``，
    否则会把未完成的调用误当成"调用了工具但参数为空"。

    Args:
        chunks: chunk 序列，**非空**。

    Returns:
        合并后的消息对象。

    Raises:
        ValueError: ``chunks`` 为空——属编程错误，不静默返回 None。
    """
    if not chunks:
        raise ValueError("chunks 不能为空")
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk
    return merged


def _extract_sources(payload: str) -> list[dict[str, Any]]:
    """从知识库工具返回的 JSON 中提取结构化来源。

    用于填充 API 响应的 ``sources`` 字段（决策项 I2）。
    在此之前该字段恒为空——因为 ``AgentLoop`` 拿不到结构化来源。

    Args:
        payload: ``search_knowledge`` 工具返回的 JSON 字符串。

    Returns:
        来源字典列表，每项含 :data:`_SOURCE_FIELDS` 的键。
        **无法解析或无结果时返回空列表**——绝不返回编造的条目
        （``interface-contract.md`` §3「无来源时为空，不伪造」）。

    Notes:
        失败静默返回空列表是刻意的：来源是**附加信息**，
        解析不了不该让整个问答失败。原文已通过模型答复传递，
        学生仍能得到答案，只是没有来源标注。
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        logger.warning("来源提取失败：非 JSON 内容")
        return []
    if not isinstance(data, dict):
        return []
    # found=False 时 passages 根本不存在
    if not data.get("found"):
        return []
    passages = data.get(_PASSAGES_KEY)
    if not isinstance(passages, list):
        return []

    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in passages:
        if not isinstance(item, dict):
            continue
        # 缺 source_id 的条目无法溯源，跳过
        source_id = item.get("source_id")
        locator = item.get("locator")
        if not source_id or not isinstance(source_id, str):
            continue
        # 同一来源多次命中只记一次（source_id + locator 相同即视为同一条）
        key = (source_id, str(locator or ""))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "source_id": source_id[:128],
                "title": str(item.get("title") or "")[:512],
                "edition": str(item.get("edition") or "")[:128],
                "locator": str(locator or "")[:256],
                "scope": str(item.get("scope") or "")[:64],
            }
        )
    return sources


__all__ = ["ToolDispatcher", "AgentLoop", "DEFAULT_MAX_STEPS"]
