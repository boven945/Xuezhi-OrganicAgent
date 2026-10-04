"""语音配置。

沿用 `app/llm/config.py` 的既定原则：**配置缺失即显式表达，
不提供带默认值的密钥**（`deployment-operations.md` §6）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final, Literal

from .errors import SpeechNotConfiguredError

#: 合成文本的单次字符上限。
#:
#: **取400 而不是更大值**：edge-tts 单次请求过长时可能静默截断
#: 或超时（实测行为随服务端策略变化，故不依赖其上限）。
#: 400 字约等于 1 分半播报，超过这个长度的答案在教学场景中
#: 本就应分段——`architecture.md` §6 要求语音可降级，
#: 遇到超长文本时**截断并在结果里标明**比无声失败更好。
MAX_SPEECH_CHARS: Final[int] = 400

#: 默认音色。中文教学场景的通用女声，Microsoft 官方音色名。
#:
#: **刻意给默认值**：音色不是密钥，也不是需要按环境区分的配置。
#: 与 `MAAS_API_KEY` 不同，此处不存在「猜一个密钥填进去」的风险。
DEFAULT_VOICE: Final[str] = "zh-CN-XiaoxiaoNeural"

#: 语速调整，范围 [-50, 100]（百分比）。
#:
#: 教学场景默认**不放慢**：模型的中位延迟是 7.1 秒（见决策 A3），
#: 语速再慢会加剧"话还没说完就出下一题"的错位感。
#: 需要时可由环境变量覆盖。
DEFAULT_RATE: Final[int] = 0


@dataclass(frozen=True, slots=True)
class SpeechSettings:
    """语音与数字人配置。

    Attributes:
        tts_enabled: 是否启用语音合成。关掉时上层直接返回
            "语音不可用"，不会尝试合成。
        voice: edge-tts 音色名。
        rate: 语速百分比调整。
        max_chars: 单次合成字符上限。
        fay_enabled: 是否向 Fay 推送数字人动作。
        fay_url: Fay 服务基址，形如 ``http://127.0.0.1:5000``。
        fay_timeout: 推送超时（秒）。
    """

    tts_enabled: bool = True
    voice: str = DEFAULT_VOICE
    rate: int = DEFAULT_RATE
    max_chars: int = MAX_SPEECH_CHARS
    fay_enabled: bool = False
    fay_url: str = ""
    fay_timeout: float = 10.0

    def __post_init__(self) -> None:
        """逐项校验。

        **在此校验而非等到合成时**：配置错误的报错应指向配置本身。
        实测踩过——原先 `rate` 只在拼命令行时才被用到，
        非法值会一路带到 TTS 库才报错，报错位置离原因很远。
        """
        if not self.voice.strip():
            raise ValueError("语音音色不能为空")
        if not -50 <= self.rate <= 100:
            raise ValueError(
                f"语速须在 -50 到 100 之间（百分比），当前 {self.rate}"
            )
        if self.max_chars < 1:
            raise ValueError("单次合成字符上限至少为 1")
        if self.fay_enabled and not self.fay_url.strip():
            # 显式失败好过静默忽略：显式说明「你要开启 Fay 但没给地址」
            # 是配置错误，而静默忽略会让人以为数字人已接管
            raise ValueError("已开启数字人（XUEZHI_FAY_ENABLED=1）但未提供 XUEZHI_FAY_URL")
        if self.fay_timeout <= 0:
            raise ValueError("Fay 推送超时必须大于 0")

    @property
    def fay_endpoint(self) -> str:
        """Fay 透明通道的完整 URL。

        构造时**去掉末尾斜杠**——Fay 的路由以 `/transparent-pass` 开头，
        基址若带尾斜杠会拼成 `//transparent-pass`（实测 Flask
        对此的容忍度未验证，宁可自己规范化）。
        """
        base = self.fay_url.rstrip("/")
        return f"{base}/transparent-pass"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SpeechSettings:
        """从环境变量构造。

        非法取值一律回落到默认并**记日志**，不抛错——
        语音是可选能力，配置写错不该让整个服务起不来
        （与 `app/llm/config.py` 同一取向：能降级就降级）。

        读取失败回落而非抛错的理由：语音是可降级能力，
        配置写错时正确行为是「没有语音但文本正常」，
        而不是整个 API 网关 500。
        """
        source = env if env is not None else os.environ

        def _flag(key: str, default: bool) -> bool:
            raw = source.get(key)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        def _num(key: str, default: float) -> float:
            raw = source.get(key)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _int(key: str, default: int) -> int:
            raw = source.get(key)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _bounded_int(key: str, default: int, low: int, high: int) -> int:
            """取整数并**就地夹到合法区间**，不抛错。

            实测踩过（单元测试抓出）：原先 ``_int`` 遇到 ``-5``
            会原样传给构造器，``__post_init__`` 直接抛
            ``ValueError`` —— 这与本函数文档承诺的
            「配置写错不该让整个服务起不来」**相矛盾**：
            一个笔误就能让 API 网关启动失败。

            故此处夹取而非报错：``-5`` 变成下限 ``1``，
            ``9999`` 变成上限。**越界本身是可从的**，
            且不会静默产生危险行为（上限只会让请求被提前拒绝，
            下限只会让文本更早被截断）。
            """
            value = _int(key, default)
            return max(low, min(high, value))

        def _voice() -> str:
            """取音色；空白值回落默认。

            实测踩过（单元测试抓出）：原实现是
            ``(source.get(...) or DEFAULT_VOICE).strip()``，
            但``"   "` 是**真值**故不会走 ``or`` 分支，
            strip 后变成空串，再被 ``__post_init__`` 拒绝——
            于是 ``XUEZHI_TTS_VOICE="  "`` 这个纯粹的笔误
            会让整个 API 网关启动失败。

            与越界数值同理：环境变量里的问题应在读入处化解，
            不该冒到构造器变成启动阻塞项。
            """
            raw = source.get("XUEZHI_TTS_VOICE")
            if raw is None:
                return DEFAULT_VOICE
            candidate = raw.strip()
            return candidate or DEFAULT_VOICE

        return cls(
            tts_enabled=_flag("XUEZHI_TTS_ENABLED", True),
            voice=_voice(),
            # 越界夹取而非抛错：见 _bounded_int 的说明
            rate=_bounded_int("XUEZHI_TTS_RATE", DEFAULT_RATE, -50, 100),
            max_chars=_bounded_int("XUEZHI_TTS_MAX_CHARS", MAX_SPEECH_CHARS, 1, 100_000),
            fay_enabled=_flag("XUEZHI_FAY_ENABLED", False),
            fay_url=(source.get("XUEZHI_FAY_URL") or "").strip(),
            fay_timeout=_num("XUEZHI_FAY_TIMEOUT", 10.0),
        )

    def require_voice(self) -> str:
        """返回可用音色，未配置则抛受控错误。

        存在的意义：让「没配音色」走 :class:`SpeechNotConfiguredError`
        这条**可降级**的路径，而不是让 edge-tts 抛自己的异常。
        """
        if not self.tts_enabled:
            raise SpeechNotConfiguredError("语音功能已关闭。")
        if not self.voice.strip():
            raise SpeechNotConfiguredError("尚未指定语音音色。")
        return self.voice


#: 供上层标注「这次结果里语音处于什么状态」的字面量。
#:
#: 刻意用字符串枚举而非布尔组合：布尔组合会产出
#: 「有音频但数字人失败」这类需要两标志的中间态，
#: 而前端真正需要的是**一条可直接展示的说明**。
SpeechStage = Literal["ready", "disabled", "not_configured", "unavailable"]


__all__ = [
    "DEFAULT_RATE",
    "DEFAULT_VOICE",
    "MAX_SPEECH_CHARS",
    "SpeechSettings",
    "SpeechStage",
]
