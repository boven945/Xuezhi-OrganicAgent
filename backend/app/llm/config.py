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

#: 本地 qwen 推理服务的 OpenAI 兼容地址（2026-11-23 起为默认）。
#: 由 llama.cpp server 暴露，权重复用 本地权重来源项目的 qwen2.5-7B-Instruct Q4_K_M GGUF。
#: 启动方式见 docs/local-qwen-runtime.md。
LOCAL_QWEN_BASE_URL = "http://127.0.0.1:8080/v1"

#: ModelArts MaaS 的 OpenAI 兼容地址（云端可选项，已不再默认启用）。
#: 依据华为云官方文档（2026-09-29 更新）：
#: https://support.huaweicloud.com/model-call-maas/model-call-021.html
#: 使用 OpenAI SDK 时 base_url 设为 https://api.modelarts-maas.com/openai/v1
MAAS_BASE_URL = "https://api.modelarts-maas.com/openai/v1"

#: 默认端点：本地优先。显式设置 MAAS_BASE_URL 可切回云端。
DEFAULT_BASE_URL = LOCAL_QWEN_BASE_URL

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
ENV_PERSONA = "XUEZHI_PERSONA_ENABLED"
ENV_MODEL = "MAAS_MODEL"
#: 是否走流式端点。未设置时按端点自动推导：本地关、远端开。
ENV_STREAMING = "XUEZHI_LLM_STREAMING"

#: 默认模型：本地 qwen2.5-7B-Instruct（Q4_K_M）。
#: 模型标识须与 llama.cpp server 启动时的 `--model_alias` 一致。
#: 2026-11-23 起项目不再默认使用盘古；设置 MAAS_MODEL 可显式切回云端模型。
DEFAULT_MODEL = "qwen2.5-7b-local"

#: 默认超时（秒）。`architecture.md` §6 要求各组件有独立超时。
DEFAULT_TIMEOUT = 60.0

#: 默认重试次数。`deployment-operations.md` §8 要求"有上限的退避策略"，
#: 这里的上限同时避免放大上游故障。
DEFAULT_MAX_RETRIES = 2


class LLMConfigError(LLMNotConfiguredError):
    """配置不合法（缺键、取值非法等）。"""


def is_local_endpoint(base_url: str) -> bool:
    """端点是否为本机服务。

    本地 llama.cpp / Ollama 服务不需要密钥；`from_env` 据此允许空
    ``MAAS_API_KEY`` 并填占位值。远端端点仍强制要求真实密钥，
    避免把「忘了配 key」误当成「本地模式」。
    """
    from urllib.parse import urlparse

    return urlparse(base_url).hostname in {"127.0.0.1", "localhost", "::1"}


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
    #: 单次回复 token 上限。
    #:
    #: 实测（2026-10-03，openpangu-2.0-flash）：该模型**默认开启深度思考模式**，
    #: 返回的 message 含 ``reasoning_content`` 字段，思考过程占用 completion token。
    #: 设 200 时出现 ``finish_reason=length`` 且 ``content`` 为空（token 全被思考占用）；
    #: 提到 2000 后正常输出。因此**不宜设过小**，否则学生看到空白。
    max_completion_tokens: int = 2048
    #: 是否启用「化学老师」人设（见 :mod:`app.llm.persona`）。
    #:
    #: **默认关闭**（实测结论，见 docs/speech-module-verification.md H28）。
    #:
    #: 开启后人设文本确实会送到模型，但**实测输出无明显变化**：
    #: 口语标记两版都是 0，且开启版反而更长。
    #: 原因是功能契约（区分检索事实与推断、只在有来源时引用）
    #: 天然导向结构化输出，权重压过了后段的人设要求。
    #:
    #: 保留开关而非删除代码：机制本身可验证（system 已送达），
    #: 若日后改写人设（如明确禁止列表与标题）可直接再测。
    persona_enabled: bool = False
    #: 是否使用流式端点。
    #:
    #: 本地 llama.cpp 0.3.x + chatml-function-calling 实测：**stream=true 与
    #: tools 同时出现时返回空流**（2026-11-23），故本地端点默认关闭流式；
    #: 此时 Agent 层按既有降级路径改用同步调用，问答链仍可用。
    #: 云端 MaaS 支持标准 SSE，保持开启。可用 XUEZHI_LLM_STREAMING 显式覆盖。
    streaming: bool = True

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
            "local_endpoint": is_local_endpoint(self.base_url),
            "streaming": self.streaming,
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
        base_url = overrides.pop("base_url", None) or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        model = overrides.pop("model", None) or os.environ.get(ENV_MODEL) or DEFAULT_MODEL

        # 本地推理服务不需要密钥：空 key 填占位值即可。
        # 远端端点仍强制要求真实密钥（避免把漏配 key 误判为本地模式）。
        if not api_key and is_local_endpoint(str(base_url)):
            api_key = "local"
        if not api_key:
            raise LLMConfigError(
                f"缺少模型服务密钥。请设置环境变量 {ENV_API_KEY}（云端模式）后重启服务；"
                "或将服务地址指向本机 OpenAI 兼容端点（本地模式无需密钥）。"
            )

        # 人设开关用**闭式判断**而非"非空即真"：
        # 未设置时默认关（实测人设无效果，见 H28）；显式给值才改变行为。
        persona_raw = overrides.pop("persona_enabled", None)
        if persona_raw is None:
            persona_raw = os.environ.get(ENV_PERSONA, "0")
        persona_on = str(persona_raw).strip().lower() in {"1", "true", "yes", "on"}

        # 流式开关：显式覆盖 > 环境变量 > 按端点自动推导（本地关、远端开）。
        streaming_raw = overrides.pop("streaming", None)
        if streaming_raw is None:
            env_streaming = os.environ.get(ENV_STREAMING)
            if env_streaming is not None and str(env_streaming).strip():
                streaming = str(env_streaming).strip().lower() in {"1", "true", "yes", "on"}
            else:
                streaming = not is_local_endpoint(str(base_url))
        else:
            streaming = bool(streaming_raw)

        return cls(
            persona_enabled=persona_on,
            streaming=streaming,
            api_key=str(api_key),
            base_url=str(base_url),
            model=str(model),
            **overrides,  # type: ignore[arg-type]
        )


__all__ = [
    "LLMConfig",
    "LLMConfigError",
    "is_local_endpoint",
    "LOCAL_QWEN_BASE_URL",
    "MAAS_BASE_URL",
    "DEFAULT_BASE_URL",
    "MAAS_MODELS",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "ENV_STREAMING",
]
