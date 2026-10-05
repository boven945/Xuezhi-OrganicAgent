"""模型客户端封装。

对外提供统一的 ``complete`` 接口，屏蔽上游差异并把异常转为受控错误。

关键行为（对应文档约束）：

- 上游异常一律转为 :mod:`app.llm.errors` 中的受控错误，
  **不把原始异常直接抛给上层**（`interface-contract.md` §5）。
- 超时、限流、上游不可用分别映射为不同错误码（同上 §5）。
- 日志只记录模式标识、耗时与错误类型，**不记录密钥、完整提示词或
  学生原始输入**（`architecture.md` §6、`security-privacy.md` §2）。
- 本层**不做任何化学正确性判断**，也不重写模型结论（同 §1）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from typing import Any

from .config import LLMConfig
from .errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 实测得到的协议约束（2026-10-03，openpangu-2.0-flash + openai 3.24.0）
#
# 1) ``tool_choice`` 只接受 "none" / "auto" / "required"，
#    **不支持** OpenAI 规范中的 ``{"type":"function","function":{"name":...}}``，
#    传入会报 ``ModelArts.81001``。因此无法强制指定某一个具体函数，
#    工具调度层需据此设计：靠 tools 白名单收敛候选 + 提示词引导，
#    不可依赖 tool_choice 精确点名（与 architecture.md §5 的白名单机制一致）。
#
# 2) 模型默认开启深度思考，返回 message 含 ``reasoning_content``。
#    思考过程计入 completion token，token 上限设小会导致正文为空。
#    详见 config.py 中 max_completion_tokens 的说明。
# ---------------------------------------------------------------------------

#: 系统提示词基线。
#:
#: `product-scope.md` §6 与 `architecture.md` §1 要求模型明确自身边界，
#: 因此基线提示词要求：区分检索事实与模型推断、不得编造来源、
#: 超出高中范围时明确说明。该提示词是**工程约束而非知识注入**。
#: **功能契约**——正确性要求，与表达方式无关。
#:
#: 无论用什么人设、什么语气，这四条都必须成立，
#: 因此**单独抽出**，不与人设混写。
#: 混写的后果是人设一改，底线也跟着松。
BASE_SYSTEM_PROMPT = (
    "你是面向高中有机化学教学的助手。"
    "回答须满足："
    "1) 用高中生可理解的语言解释反应物、条件、产物与关键步骤；"
    "2) 区分「知识库检索到的事实」「化学工具的校验结果」与「你的推断」；"
    "3) 只在确有来源时给出引用，无来源时明确说明；"
    "4) 对超出高中课程范围或条件不全的问题，说明边界并请求补充，"
    "不要编造答案，也不要把推断表述为已验证的化学结论。"
)


def compose_system_prompt(persona: str | None = None) -> str:
    """组合「功能契约 + 可选人设」。

    :param persona: 人设段。为``None`` 或空串时只返回功能契约。
    :returns: 完整的系统提示词。

    **为什么要拼接而不是二选一**：
    ``system_prompt`` 参数是**覆盖**语义（见 :meth:`LLMClient.complete`），
    传了它就会丢掉 ``BASE_SYSTEM_PROMPT`` 里的
    「不编造答案」等底线。故此处显式拼接。

    **顺序为何是「契约在前、人设在后」**：
    提示词靠前的内容权重更高。契约是硬要求，应占先；
    人设是表达偏好，放后面不会削弱前者。
    """
    if not persona or not persona.strip():
        return BASE_SYSTEM_PROMPT
    return f"{BASE_SYSTEM_PROMPT}\n\n{persona.strip()}"


class LLMClient:
    """OpenAI 兼容协议客户端。

    线程安全性：底层 SDK 客户端是同步的，本类不持有可变请求状态，
    可在多线程中共享同一实例。
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._chain = self._build_chain(config)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _build_chain(config: LLMConfig) -> Any:
        """构造 LangChain 聊天模型实例。

        构造失败属配置或依赖问题，统一转为 :class:`LLMNotConfiguredError`，
        避免把 SDK 内部异常泄漏到上层。
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - 依赖缺失场景
            raise LLMNotConfiguredError(
                "模型适配依赖缺失，请先安装 langchain-openai。",
                detail=f"ImportError: {exc}",
            ) from exc

        try:
            # 参数经实测确认（langchain-openai 1.6.7）：
            # - 构造参数名为 `model`（不是 `model_name`）
            # - token 上限用 `max_completion_tokens`；
            #   若塞进 model_kwargs 会触发 UserWarning
            # - `streaming=True` 开启流式端点。实测华为 MaaS 的
            #   OpenAI 兼容接口支持 `stream:true`（官方文档
            #   model-call-101 有"流式输出"示例），SSE 线格式为标准
            #   `chat.completion.chunk`。
            #   该参数只影响**是否走流式端点**，`.invoke()` 仍可用；
            #   是否真的流式由调用方选`.stream()` 还是 `.invoke()` 决定。
            return ChatOpenAI(
                model=config.model,
                base_url=config.base_url,
                api_key=config.api_key,
                timeout=config.timeout,
                max_retries=config.max_retries,
                temperature=config.temperature,
                max_completion_tokens=config.max_completion_tokens,
                streaming=True,
            )
        except Exception as exc:  # pragma: no cover - 构造参数异常场景
            raise LLMNotConfiguredError(
                "模型客户端初始化失败，请检查配置。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    @staticmethod
    def _classify_upstream(exc: BaseException) -> LLMError:
        """把上游异常映射为受控错误。

        分类依据实际异常类型与状态码，**不把原始响应体透出**
        （可能包含请求内容）。
        """
        name = type(exc).__name__
        text = str(exc).lower()

        if isinstance(exc, LLMError):
            return exc

        if "timeout" in name.lower() or "timed out" in text or "timeout" in text:
            return LLMTimeoutError("模型服务响应超时，请稍后重试。", detail=name)
        if "ratelimit" in name.lower() or "rate limit" in text or "429" in text:
            return LLMRateLimitError("模型服务当前限流，请稍后重试。", detail=name)
        if "authentication" in name.lower() or "unauthorized" in text or "401" in text:
            return LLMError(
                "模型服务鉴权失败，请检查密钥配置。",
                detail=name,
                code="llm_auth_failed",
            )
        return LLMUpstreamError("模型服务暂时不可用。", detail=name)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _with_system(
        messages: list[dict[str, Any]],
        *,
        persona: str | None = None,
    ) -> list[dict[str, Any]]:
        """确保消息列表里有且只有一条 system 消息。

        ## 为什么要加这个方法（实测发现的缺陷）

        原先 ``invoke_with_tools`` 把 ``messages`` 原样传给模型，
        而 :class:`~app.agent.dispatcher.AgentLoop` 构造的列表里
        **只有 user / assistant / tool 三种 role，不含 system**。
        结果是 :data:`BASE_SYSTEM_PROMPT` 里
        「区分检索事实与推断」「不编造答案」等四条契约
        **在真实问答链路上从未生效**。

        这类缺陷不报错：调用成功、返回 200、测试全绿，
        只是"要求写在那里但模型从没见过"。

        ## 为什么要处理「已存在」的情况

        调用方可能已经放了自己的 system 消息
        （如自定义人设）。此时不能重复插入——
        两条 system 会让模型行为不确定，且后一条未必覆盖前一条。

        **策略**：就地替换第一条，删除其余的。
        这样"谁提供了 system"由调用方决定，本方法只保证不重复。
        """
        system_text = compose_system_prompt(persona)
        rest = [m for m in messages if m.get("role") != "system"]
        if not system_text:
            return rest
        return [{"role": "system", "content": system_text}, *rest]

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def complete(
        self,
        user_message: str,
        *,
        system_prompt: str | None = None,
        history: Sequence[tuple[str, str]] | None = None,
    ) -> str:
        """发送一轮对话并返回文本回复。

        Args:
            user_message: 用户本次输入。
            system_prompt: 覆盖默认系统提示词。传空字符串则不附加系统消息。
            history: 先前轮次，元素为 ``(role, content)``，role 取
                ``"user"`` / ``"assistant"``。

        Returns:
            模型回复文本。

        Raises:
            LLMNotConfiguredError: 客户端未正确配置。
            LLMTimeoutError: 请求超时。
            LLMRateLimitError: 被限流。
            LLMError: 其他上游错误（含鉴权失败）。
        """
        if not user_message or not user_message.strip():
            raise LLMError("输入不能为空。", code="llm_invalid_input")

        messages: list[tuple[str, str]] = []
        effective_system = BASE_SYSTEM_PROMPT if system_prompt is None else system_prompt
        if effective_system:
            messages.append(("system", effective_system))
        for role, content in history or ():
            messages.append((role, content))
        messages.append(("user", user_message))

        started = time.perf_counter()
        try:
            response = self._chain.invoke(messages)
        except Exception as exc:
            error = self._classify_upstream(exc)
            # 只记录错误类型与耗时，不记录输入内容与密钥
            logger.warning(
                "模型调用失败: code=%s type=%s elapsed=%.2fs",
                error.code,
                type(exc).__name__,
                time.perf_counter() - started,
            )
            raise error from None  # 断开原始异常链，避免上层误取原始信息

        elapsed = time.perf_counter() - started
        logger.info(
            "模型调用完成: model=%s elapsed=%.2fs",
            self._config.model,
            elapsed,
        )
        return self._extract_text(response)

    def invoke_with_tools(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        *,
        persona: str | None = None,
    ) -> Any:
        """带工具定义调用模型，返回原始响应对象。

        与 :meth:`complete` 的区别：本方法**不提取文本**，而是把含
        ``tool_calls`` 的消息原样返回，供 Agent 编排层判断是否发起工具调用。

        Args:
            messages: 完整消息列表，元素为 ``{"role":..., "content":...}``；
                其中 assistant 消息可含 ``tool_calls``，tool 消息需带
                ``tool_call_id``（实测该回填方式 openPangu 接受）。
            tools: OpenAI 格式的工具定义数组（由 Agent 层白名单生成）。

        Returns:
            LangChain 的 ``AIMessage``，可含 ``tool_calls``、``content``、
            ``reasoning_content``。

        Raises:
            LLMError: 输入非法或上游错误（分类同 :meth:`complete`）。
        """
        if not tools:
            raise LLMError("工具列表不能为空。", code="llm_invalid_input")
        if not messages:
            raise LLMError("消息列表不能为空。", code="llm_invalid_input")

        started = time.perf_counter()
        try:
            # 不传 tool_choice：实测 openPangu 不支持指定具体函数，
            # 工具收敛依赖白名单（见 module docstring 的协议约束说明）
            bound = self._chain.bind_tools(list(tools))
            response = bound.invoke(
                self._with_system(list(messages), persona=persona)
            )
        except Exception as exc:
            error = self._classify_upstream(exc)
            logger.warning(
                "模型工具调用失败: code=%s type=%s elapsed=%.2fs",
                error.code,
                type(exc).__name__,
                time.perf_counter() - started,
            )
            raise error from None

        logger.info(
            "模型工具调用完成: model=%s elapsed=%.2fs",
            self._config.model,
            time.perf_counter() - started,
        )
        return response

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式调用。

        依据实测：华为 MaaS 的 OpenAI 兼容端点**支持** ``stream:true``
        （官方文档 ``model-call-101`` 有"流式输出"示例，SSE 格式为标准
        ``chat.completion.chunk``），且 ``bind_tools`` 后的 Runnable
        仍带 ``.stream()``（容器内实测确认）。

        但 ``ChatOpenAI`` 的 ``streaming`` 是**构造参数**，构造时已定；
        留此属性是为了让Agent 层能查询而不必知道实现细节。
        """
        return bool(getattr(self._chain, "streaming", False))

    def stream_with_tools(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> Iterator[Any]:
        """带工具定义流式调用模型，逐块产出 ``AIMessageChunk``。

        与 :meth:`invoke_with_tools` 的关系：**同一条链，两种传输**。
        ``bind_tools`` 后的 Runnable 同时具备 ``.stream()`` 与
        ``.invoke()``（实测），故不需要维护两套提示词或参数。

        Args:
            messages: 完整消息列表，同 :meth:`invoke_with_tools`。
            tools: 工具定义数组。

        Yields:
            ``AIMessageChunk`` 序列。调用方负责合并
            （见 :func:`app.agent.dispatcher._merge_chunks`）。

        Raises:
            LLMError: 输入非法、客户端未开启流式
                （code=llm_streaming_unsupported）或上游错误。

        Notes:
            **这是生成器**：调用时代码不执行，异常在迭代时才浮现。
            调用方须在迭代处try/except——Agent 层已如此处理
            （流式失败降级为同步调用）。

            上游约束沿用 ``invoke_with_tools``：不传 ``tool_choice``
            （实测 openPangu 不支持指定具体函数）。
        """
        if not tools:
            raise LLMError("工具列表不能为空。", code="llm_invalid_input")
        if not messages:
            raise LLMError("消息列表不能为空。", code="llm_invalid_input")
        if not self.supports_streaming:
            # 显式失败优于静默返回空流——调用方须能区分
            # 「不支持流式」与「流式但无内容」。
            # 刻意用 LLMError + 自定义 code 而非新增异常类：
            # `errors.py` 的类层次按「上游故障类型」划分，
            # 「不支持流式」是本地配置状态而非新的故障类别。
            raise LLMError(
                "当前模型客户端未开启流式，请改用 invoke_with_tools。",
                code="llm_streaming_unsupported",
            )

        started = time.perf_counter()
        try:
            bound = self._chain.bind_tools(list(tools))
            for chunk in bound.stream(list(messages)):
                yield chunk
        except Exception as exc:
            error = self._classify_upstream(exc)
            logger.warning(
                "模型流式调用失败: code=%s type=%s elapsed=%.2fs",
                error.code,
                type(exc).__name__,
                time.perf_counter() - started,
            )
            raise error from None

        logger.info(
            "模型流式调用完成: model=%s elapsed=%.2fs",
            self._config.model,
            time.perf_counter() - started,
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """从 LangChain 返回值中提取文本。

        ``content`` 可能是 str，也可能是 OpenAI 多模态格式的列表，
        此处兼容两种形态（实测依据：openai 3.24 / langchain-openai 1.6.7）。
        """
        content = getattr(response, "content", None)
        if content is None:
            # 兼容 dict 形态
            if isinstance(response, dict):
                content = response.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            text = "".join(parts)

        # 空回复必须报错：否则上层会把"空字符串"当成成功答复，
        # 表现为学生看到空白但没有任何错误提示（同 interface-contract.md §5）。
        if not text.strip():
            raise LLMError("模型返回内容为空。", code="llm_empty_response")
        return text

    @property
    def model(self) -> str:
        """当前模型标识（供诊断日志与可观测性使用）。"""
        return self._config.model

    def public_config(self) -> dict[str, object]:
        """返回可安全记录的配置摘要（不含密钥）。"""
        return self._config.public_summary()


def create_client(config: LLMConfig | None = None) -> LLMClient:
    """创建客户端。

    Args:
        config: 显式配置。为 ``None`` 时从环境变量读取。

    Raises:
        LLMConfigError: 配置缺失或非法（含缺少 API Key）。
    """
    return LLMClient(config if config is not None else LLMConfig.from_env())


__all__ = [
    "LLMClient",
    "create_client",
    "BASE_SYSTEM_PROMPT",
    "compose_system_prompt",
]
