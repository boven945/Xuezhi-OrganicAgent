"""模型适配层配置。

配置来源与优先级：显式传入 > 环境变量。**不提供任何带默认值的密钥**，
缺失即报错（`deployment-operations.md` §6：配置缺失时服务应快速失败）。

安全约束（`security-privacy.md` §3）：
- API Key 只从环境变量或未跟踪的本地配置读取
- 任何 ``repr`` / ``str`` / 日志输出都不得包含密钥明文
- 错误消息只说明"缺少哪个键"，不输出键的值
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import LLMNotConfiguredError

#: ModelArts MaaS 的 OpenAI 兼容地址。
#: 依据华为云官方文档（2026-09-29 更新）：
#: https://support.huaweicloud.com/model-call-maas/model-call-021.html
#: 使用 OpenAI SDK 时 base_url 设为 https://api.modelarts-maas.com/openai/v1
MAAS_BASE_URL = "https://api.modelarts-maas.com/openai/v1"

#: 官方文档列出的 openPangu 模型标识（model 参数取值）。
#: 同上来源。仅收录经官方文档确认的值，不猜测。
MAAS_MODELS = frozenset(
    {
        "openpangu-2.0-pro",
        "openpangu-2.0-flash",
    }
)

#: 环境变量名
ENV_API_KEY = "MAAS_API_KEY"
ENV_BASE_URL = "MAAS_BASE_URL"
ENV_MODEL = "MAAS_MODEL"

#: 默认模型。openPangu-2.0-Flash 是官方文档中成本与延迟较低的选项，
#: 且文档记录其支持原生 Function Call，符合 `product-scope.md` 的工具调用需求。
DEFAULT_MODEL = "openpangu-2.0-flash"

#: 默认超时（秒）。`architecture.md` §6 要求各组件有独立超时。
DEFAULT_TIMEOUT = 60.0

#: 默认重试次数。`deployment-operations.md` §8 要求"有上限的退避策略"，
#: 这里的上限同时避免放大上游故障。
DEFAULT_MAX_RETRIES = 2


class LLMConfigError(LLMNotConfiguredError):
    """配置不合法（缺键、取值非法等）。"""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """模型适配配置。

    Attributes:
        model: 模型标识。须为官方文档确认的取值之一。
        base_url: OpenAI 兼容端点。
        api_key: 访问密钥。**绝不参与 repr/str**。
        timeout: 单次请求超时（秒）。
        max_retries: 最大重试次数（有上限）。
        temperature: 采样温度。教学场景偏低以保证稳定性。
        max_completion_tokens: 单次回复的 token 上限。
    """

    model: str
    base_url: str
    api_key: str = field(repr=False)
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    temperature: float = 0.3
    max_completion_tokens: int = 2048

    def __post_init__(self) -> None:
        # 逐项校验，错误消息只说"哪个键不合规"，不输出密钥
        if not self.model or not self.model.strip():
            raise LLMConfigError("模型标识不能为空。")
        if not self.base_url or not self.base_url.strip():
            raise LLMConfigError("服务地址不能为空。")
        if not self.base_url.startswith(("http://", "https://")):
            raise LLMConfigError("服务地址必须以 http:// 或 https:// 开头。")
        if not self.api_key or not self.api_key.strip():
            raise LLMConfigError(
                f"缺少模型服务密钥。请设置环境变量 {ENV_API_KEY}。"
            )
        if self.timeout <= 0:
            raise LLMConfigError("超时时间必须大于 0。")
        if self.max_retries < 0:
            raise LLMConfigError("重试次数不能为负。")
        if not 0.0 <= self.temperature <= 2.0:
            raise LLMConfigError("采样温度须在 0.0 到 2.0 之间。")
        if self.max_completion_tokens <= 0:
            raise LLMConfigError("回复 token 上限必须大于 0。")

    @property
    def is_official_maas_model(self) -> bool:
        """模型标识是否为官方文档确认的 MaaS 预置模型。

        供上层做兼容性提示；**不因此拒绝自定义模型**（允许自建兼容端点）。
        """
        return self.model in MAAS_MODELS

    def public_summary(self) -> dict[str, object]:
        """返回**可安全记录**的配置摘要。

        刻意不含 ``api_key``，也不含 base_url 的完整值（可能含租户信息）。
        供诊断日志使用（`architecture.md` §6 要求记录模式标识与配置状态）。
        """
        from urllib.parse import urlparse

        parsed = urlparse(self.base_url)
        return {
            "model": self.model,
            "provider_host": parsed.netloc or "(unknown)",
            "path": parsed.path or "/",
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
            "official_maas_model": self.is_official_maas_model,
            "api_key_configured": True,
        }

    @classmethod
    def from_env(cls, **overrides: object) -> LLMConfig:
        """从环境变量构造配置。

        Args:
            **overrides: 覆盖任意字段。主要用于测试注入。

        Raises:
            LLMConfigError: 缺少 API Key 或取值非法。
                错误消息只说明缺少哪个键，**不含其值**。
        """
        api_key = overrides.pop("api_key", None) or os.environ.get(ENV_API_KEY, "")
        base_url = overrides.pop("base_url", None) or os.environ.get(ENV_BASE_URL) or MAAS_BASE_URL
        model = overrides.pop("model", None) or os.environ.get(ENV_MODEL) or DEFAULT_MODEL

        if not api_key:
            raise LLMConfigError(
                f"缺少模型服务密钥。请设置环境变量 {ENV_API_KEY} 后重启服务。"
            )

        return cls(
            api_key=str(api_key),
            base_url=str(base_url),
            model=str(model),
            **overrides,  # type: ignore[arg-type]
        )


__all__ = [
    "LLMConfig",
    "LLMConfigError",
    "MAAS_BASE_URL",
    "MAAS_MODELS",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
]
