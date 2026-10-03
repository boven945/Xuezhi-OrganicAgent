"""模型适配层。

职责边界见 `docs/architecture.md` §2：以 OpenAI 兼容协议连接
华为 ModelArts MaaS（云端）或本地兼容推理服务（离线）。

设计要点：

- **模型负责语言理解与组织，不单独担任化学验证器**（`architecture.md` §1）。
  本层只负责"把请求送达模型、把结果取回"，不做任何化学正确性判断。
- **密钥不进入日志、不进入异常消息、不进入返回值**（`security-privacy.md` §3）。
- **模型不可用时必须明确报错，不得静默降级为编造内容**
  （`product-scope.md` §5、`architecture.md` §5）。
"""

from .config import LLMConfig, LLMConfigError
from .errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from .client import LLMClient, create_client

__all__ = [
    "LLMConfig",
    "LLMConfigError",
    "LLMError",
    "LLMNotConfiguredError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "LLMUpstreamError",
    "LLMClient",
    "create_client",
]
