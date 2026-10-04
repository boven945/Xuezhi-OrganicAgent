"""语音服务编排层。

## 这是本模块唯一该被上层调用的入口

上层（API 网关）**只**调用 :func:`speak`，
不直接碰 :class:`~app.speech.tts.TTSEngine` 或
:class:`~app.speech.fay.FayClient`。理由：降级逻辑必须只有一处实现。

若让上层分别调用 TTS 与 Fay，它就得自己写"失败了怎么办"，
而那套逻辑在每个调用点都会走样一次。

## 降级阶梯

`architecture.md` §6 要求语音可降级。本模块的阶梯是：

1. TTS 合成 + Fay 推送 → 两者都成（完整体验）
2. 仅 TTS 合成 → 有音频，无数字人
3. 都不成 → **纯文本**（主交付始终可用）

关键性质：**第 3 步永远是可接受的**。任何一步的失败都不会
让学生的文本答案消失。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import SpeechSettings
from .fay import DEFAULT_FAY_USER, FayClient
from .models import DigitalHumanResult, SpeechResult
from .tts import TTSEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SpeechOutcome:
    """一次语音交付的完整结果。

    把 TTS 与 Fay 的结果**合在一个对象**里返回，
    因为它们总是同时发生（一次答复要么两者都有，要么都没有），
    分开返回会让调用方自己拼装——而拼装逻辑正是容易走样的地方。
    """

    speech: SpeechResult
    digital_human: DigitalHumanResult

    @property
    def available(self) -> bool:
        """是否有音频可播。"""
        return self.speech.available

    def to_dict(self) -> dict[str, Any]:
        """转为可JSON 序列化的字典。

        含 ``stage`` 与 ``reason``，前端据此决定展示什么：
        ``disabled`` / ``not_configured`` 属正常状态（不显示错误样式），
        ``unavailable`` 才提示"语音暂时不可用"。
        """
        return {
            "speech": self.speech.to_dict(),
            "digital_human": self.digital_human.to_dict(),
        }


class SpeechService:
    """语音服务。

    无状态，可安全复用；建议按进程创建单例（见 :func:`get_service`）。
    """

    def __init__(
        self,
        settings: SpeechSettings | None = None,
        *,
        tts: TTSEngine | None = None,
        fay: FayClient | None = None,
    ) -> None:
        # **None 时必须走 from_env()，不能留成 SpeechSettings() 默认值**。
        # 实测踩过（端到端 /health 实测发现）：
        # 原实现是 `settings or SpeechSettings()`，
        # 于是设了 XUEZHI_FAY_ENABLED=1 与 XUEZHI_FAY_URL 后，
        # 容器内 probe() 仍返回 fay=False——
        # **环境变量完全没被读到**，配置形同虚设，
        # 且因为默认值合法，任何校验都不会报错，只有实际行为不对。
        #
        # 与 app/api/deps.py 的 ServiceSettings.from_env() 同一原则：
        # 生产路径一律从环境变量读，测试才显式注入。
        self._settings = settings if settings is not None else SpeechSettings.from_env()
        # 允许注入替身：TTS 依赖外网与Windows 组件，
        # 单元测试不该真去合成音频
        self._tts = tts or TTSEngine(self._settings)
        self._fay = fay or FayClient(self._settings)

    @property
    def settings(self) -> SpeechSettings:
        return self._settings

    def speak(
        self,
        text: str,
        *,
        push_digital_human: bool = True,
        user: str = DEFAULT_FAY_USER,
    ) -> SpeechOutcome:
        """合成语音并可选推送数字人。

        **本方法不抛异常**（除传入非字符串这类编程错误外）。
        它保证一定返回一个 :class:`SpeechOutcome`——
        上层因此不需要 try/except，文本主交付不会被语音问题打断。

        Args:
            text: 讲解文本。
            push_digital_human: 是否推送数字人。
            user: Fay 侧会话标识。**多学生同时使用时必须区分**，
                否则共用 ``"User"`` 会互相打断
                （Fay 非队列模式会清空该用户的前序音频队列）。

        Returns:
            :class:`SpeechOutcome`。
        """
        speech = self._tts.synthesize_blocking(text)

        # 数字人推送**独立于** TTS 成功与否：
        # 只送文本让Fay 自己合成是 Fay 的标准用法，
        # 此时即便本地 TTS 失败，数字人也该能播报。
        # 顺序上先看 Fay 是否启用——未启用就不做任何网络调用。
        if not self._settings.fay_enabled:
            digital_human = DigitalHumanResult(
                delivered=False, reason="数字人功能未启用。"
            )
        elif not push_digital_human:
            digital_human = DigitalHumanResult(
                delivered=False, reason="本次未启用数字人。"
            )
        else:
            digital_human = self._fay.push_text(text, user=user)

        return SpeechOutcome(speech=speech, digital_human=digital_human)

    def probe(self) -> dict[str, bool]:
        """探测各能力可用性，供 ``/health`` 报告。

        **不实际合成音频**——那会让健康检查变成网络调用，
        高频探测会把服务拖垮（与 :meth:`app.api.deps.ServiceRegistry.probe`
        的缓存理由同源）。
        """
        return {
            "tts": self._settings.tts_enabled and bool(self._settings.voice),
            "fay": self._settings.fay_enabled,
        }


_SERVICE: SpeechService | None = None


def get_service() -> SpeechService:
    """获取进程级单例。"""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SpeechService()
    return _SERVICE


def reset_service() -> None:
    """重置单例。仅供测试使用。"""
    global _SERVICE
    _SERVICE = None


__all__ = ["SpeechOutcome", "SpeechService", "get_service", "reset_service"]
