"""模型适配层的受控错误类型。

`docs/interface-contract.md` §5 要求错误具备稳定可机器读的分类，
且不得泄露堆栈或密钥。

`docs/interface-contract.md` §5 列出的相关分类：
模型不可用、知识检索不可用、工具超时、速率限制、内部错误。
本模块对应前两类与超时。
"""

from __future__ import annotations


class LLMError(Exception):
    """模型适配层所有受控错误的基类。

    Attributes:
        code: 稳定错误码，供前端与日志分类。
        user_message: 面向用户的简短说明，不含内部细节或密钥。
        retryable: 是否值得重试。调用方据此决定是否向上层建议重试。
    """

    code = "llm_internal_error"
    retryable = False

    def __init__(
        self,
        user_message: str,
        *,
        detail: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        # detail 只进服务端日志，且由调用方确保不含密钥
        self.detail = detail
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试
        return f"{type(self).__name__}(code={self.code!r}, retryable={self.retryable})"


class LLMNotConfiguredError(LLMError):
    """缺少必要配置（如未设置 API Key）。

    `deployment-operations.md` §6 要求"配置缺失时服务应快速失败并给出
    不含密钥的错误说明"。此类错误**不可重试**，重试无意义。
    """

    code = "llm_not_configured"


class LLMUpstreamError(LLMError):
    """上游服务返回错误或不可用。

    retryable 默认 True：网络抖动、限流、5xx 等属可重试情形。
    调用方仍应遵守有上限的退避策略（`deployment-operations.md` §8）。
    """

    code = "llm_upstream_unavailable"
    retryable = True


class LLMTimeoutError(LLMUpstreamError):
    """请求超时。

    独立成类便于上层区分"超时"与"其他上游错误"，
    对应 `architecture.md` §6 的独立失败路径要求。
    """

    code = "llm_timeout"


class LLMRateLimitError(LLMUpstreamError):
    """被上游限流。

    对应 `interface-contract.md` §5 的"速率限制"分类。
    """

    code = "llm_rate_limited"
    retryable = True


__all__ = [
    "LLMError",
    "LLMNotConfiguredError",
    "LLMUpstreamError",
    "LLMTimeoutError",
    "LLMRateLimitError",
]
