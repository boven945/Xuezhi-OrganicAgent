"""语音模块的数据契约。

## 核心设计：结果必须能表达"降级"

`docs/architecture.md` §6 要求语音是**可降级能力**。
多数实现会把这件事表达成"抛异常"或"返回 None"，
但那两种表达都把"没有语音"变成了**异常状态**——
前端因此要写 try/except 才能正常显示。

本模块改为：合成结果是一个**总是能构造成功**的数据类，
用 :attr:`SpeechResult.stage` 标明这次实际处于哪个状态，
用 :attr:`SpeechResult.reason` 给出可直接展示的说明。

这样上层的降级逻辑退化成一行``if result.stage == "ready"``，
而"为什么没有语音"不会丢失（可展示给学生看）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: 本次语音合成的实际状态。
#:
#: 四态而非两态，是为了区分**"用户主动关掉"**与**"服务坏了"**——
#: 前者是正常选择（不该显示任何错误样式），
#: 后者才是需要提示的问题。合并成一个 ``unavailable`` 会丢掉这个区分。
SpeechStage = Literal[
    #: 合成成功，``audio_path`` 有值
    "ready",
    #: 用户或配置显式关闭了语音
    "disabled",
    #: 缺少必要配置（如未指定音色）
    "not_configured",
    #: 服务不可用（网络、依赖缺失、调用失败）
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """一次语音合成的结果。

    Attributes:
        stage: 实际状态。见 :data:`SpeechStage`。
        audio_path: 合成产物路径。**仅 ``stage == "ready"`` 时有值**，
            其余情况为 ``None``。
        reason: 面向学生的简短说明，可直接展示。
            ``stage == "ready"`` 时为空字符串。
        truncated: 文本是否被截断。
        char_count: 实际送去合成的字符数（截断后）。
        detail: 仅服务端日志用的补充信息，**不得返回客户端**。
    """

    stage: SpeechStage
    audio_path: str | None = None
    reason: str = ""
    truncated: bool = False
    char_count: int = 0
    detail: str | None = None

    @property
    def available(self) -> bool:
        """是否真的有音频。

        刻意提供这个便捷属性：上层要判断"能不能播"时
        不该去写 ``result.stage == "ready" and result.audio_path is not None``——
        那样等于要求每个调用方都记得「有 stage 必有 path」这条不变量。
        """
        return self.stage == "ready" and bool(self.audio_path)

    def to_dict(self) -> dict[str, Any]:
        """转为可JSON 序列化的字典。

        **不含 ``detail``**：那是服务端日志用的，
        可能含内部路径（`security-privacy.md` §3）。
        """
        return {
            "stage": self.stage,
            "available": self.available,
            "audio_path": self.audio_path,
            "reason": self.reason,
            "truncated": self.truncated,
            "char_count": self.char_count,
        }


@dataclass(frozen=True, slots=True)
class DigitalHumanResult:
    """向 Fay 推送数字人动作的结果。

    与 :class:`SpeechResult` 分开而非合并：两者的降级方向不同
    （见 :class:`~app.speech.errors.DigitalHumanUnavailableError` 的说明）。
    """

    delivered: bool
    reason: str = ""
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可JSON 序列化的字典（不含 ``detail``）。"""
        return {"delivered": self.delivered, "reason": self.reason}


__all__ = ["DigitalHumanResult", "SpeechResult", "SpeechStage"]
