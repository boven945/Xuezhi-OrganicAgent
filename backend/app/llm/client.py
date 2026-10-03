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
from typing import Any, Sequence

from .config import LLMConfig
from .errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)

logger = logging.getLogger(__name__)

#: 系统提示词基线。
#:
#: `product-scope.md` §6 与 `architecture.md` §1 要求模型明确自身边界，
#: 因此基线提示词要求：区分检索事实与模型推断、不得编造来源、
#: 超出高中范围时明确说明。该提示词是**工程约束而非知识注入**。
BASE_SYSTEM_PROMPT = (
    "你是面向高中有机化学教学的助手。"
    "回答须满足："
    "1) 用高中生可理解的语言解释反应物、条件、产物与关键步骤；"
    "2) 区分「知识库检索到的事实」「化学工具的校验结果」与「你的推断」；"
    "3) 只在确有来源时给出引用，无来源时明确说明；"
    "4) 对超出高中课程范围或条件不全的问题，说明边界并请求补充，"
    "不要编造答案，也不要把推断表述为已验证的化学结论。"
)


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
            return ChatOpenAI(
                model=config.model,
                base_url=config.base_url,
                api_key=config.api_key,
                timeout=config.timeout,
                max_retries=config.max_retries,
                temperature=config.temperature,
                max_completion_tokens=config.max_completion_tokens,
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


__all__ = ["LLMClient", "create_client", "BASE_SYSTEM_PROMPT"]
