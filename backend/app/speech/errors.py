"""语音模块的受控错误类型。

沿用 `docs/interface-contract.md` §5 的稳定错误码约定，
命名规则 `<域>_<原因>`，与 `app/llm/errors.py`、`app/chem/errors.py` 一致。

## 为什么语音错误全部可降级

`docs/architecture.md` §6 定下的原则是「将文本回答设为主交付，
将动画和语音视为可降级能力」。因此本模块的错误**没有一个是致命的**：
调用方收到任何一种都应继续把文本答案交付给学生，
只是没有语音播报。

这与 `llm_*` 错误不同——那些会让问答链路真的失败。
实现上体现为：上层 :mod:`app.speech.service` 捕获本模块全部异常，
转为 ``available=False`` 的结构化结果，而不上抛。
"""

from __future__ import annotations


class SpeechError(Exception):
    """语音模块所有受控错误的基类。

    Attributes:
        code: 稳定错误码，供前端与日志分类。
        user_message: 面向用户的简短说明，不含内部细节。
        retryable: 是否值得重试。**注意**：多数语音错误重试无意义
            （文本本身没问题，是服务或配置的问题），故默认 False。
    """

    code = "speech_internal_error"
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
        #: 只进服务端日志，**不得返回给客户端**（`security-privacy.md` §3）。
        self.detail = detail
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable

    def __repr__(self) -> str:  # pragma: no cover - 仅供调试
        return f"{type(self).__name__}(code={self.code!r}, retryable={self.retryable})"


class SpeechNotConfiguredError(SpeechError):
    """缺少语音配置（如未指定音色）。

    属**预期的配置状态**，不是崩溃——服务应照常提供文本答案。
    与 :class:`~app.llm.errors.LLMNotConfiguredError` 同样的定位。
    """

    code = "speech_not_configured"


class TTSUnavailableError(SpeechError):
    """语音合成服务不可用。

    两种成因都归到这里：**未配置**（本地edge-tts 不可达）与
    **调用失败**（网络、鉴权、限流）。刻意不细分——
    区分它们对学生没有行动价值，且细分需要依赖上游错误文本，
    反而增加泄露风险（实测教训见 `app/api/errors.py`的
    ``public_message`` 说明）。

    ``retryable=True``：网络抖动重试确实可能成功。
    """

    code = "speech_tts_unavailable"
    retryable = True


class TextTooLongForSpeechError(SpeechError):
    """文本超出语音合成单次上限。

    单独成类而非归入 TTS 不可用：**这是输入问题而非服务故障**，
    正确处理方式是截断或分句，而非重试。
    与 `app/chem/errors.py` 把「结构非法」和「超出支持范围」分开同理。
    """

    code = "speech_text_too_long"


class DigitalHumanUnavailableError(SpeechError):
    """Fay 数字人服务不可用或未启用。

    与 :class:`TTSUnavailableError` 分开，因为**降级策略不同**：
    TTS 不可用可以退回本地合成；而 Fay 不可用时前端应直接隐藏
    数字人画面，不显示"正在连接"的空转状态。
    """

    code = "speech_digital_human_unavailable"
    retryable = True


__all__ = [
    "DigitalHumanUnavailableError",
    "SpeechError",
    "SpeechNotConfiguredError",
    "TTSUnavailableError",
    "TextTooLongForSpeechError",
]
