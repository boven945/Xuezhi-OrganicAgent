"""语音与数字人模块。

职责边界（`docs/architecture.md` §5、§6）：

- **做**：把讲解文本转成语音、推送给数字人驱动。
- **不做**：不判断内容正确性（那是 chem 与知识库的事）、
  不决定前端如何展示（那是 frontend 的事）。

## 降级是本模块的第一等公民

`architecture.md` §6 定下「将文本回答设为主交付，
将动画和语音视为可降级能力」。因此：

- :meth:`app.speech.service.SpeechService.speak` **保证不抛异常**，
  任何失败都转成结构化结果；
- 失败时上层**照常交付文本答案**，只是没有语音。

## 为什么 ``engine`` 相关名字用模块级 ``__getattr__`` 延迟导入

``tts.py`` 依赖 ``edge_tts``，``fay.py`` 依赖外网。
若在包顶层直接导出，任何 ``import app.speech.errors``
都会连带触发依赖加载。

改用 :pep:`562` 的模块级 ``__getattr__``：对外用法不变，
但只有真正访问这些名字时才导入实现。

**与 :mod:`app.chem` 的差别**：``chem`` 的延迟导入是为了绕开
Windows 应用控制策略拦截 RDKit（本机无法加载 DLL）；
``speech`` 的延迟导入是为了让**可选依赖缺失时不拖垮整个 API 网关**。
两者都遵循「模块的其余部分必须始终可用」这条底线。
"""

from typing import TYPE_CHECKING, Any

from .config import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    MAX_SPEECH_CHARS,
    SpeechSettings,
)
from .errors import (
    DigitalHumanUnavailableError,
    SpeechError,
    SpeechNotConfiguredError,
    TTSUnavailableError,
    TextTooLongForSpeechError,
)
from .models import DigitalHumanResult, SpeechResult, SpeechStage
from .service import SpeechOutcome, SpeechService, get_service, reset_service

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from .fay import FayClient
    from .tts import TTSEngine

#: 延迟导出的名字。值是 ``(模块内路径, 属性名)``。
#:
#: 刻意**不延迟** :class:`SpeechService` 与 :func:`get_service`：
#: 它们本身不引入第三方依赖（延迟导入发生在实例化时的
#: :class:`TTSEngine` 内部），延迟它们只会让调用方多绕一层。
_LAZY: dict[str, str] = {
    "TTSEngine": "tts",
    "FayClient": "fay",
    "get_engine": "tts",
    "reset_engine": "tts",
}


def __getattr__(name: str) -> Any:
    """按需导入依赖第三方库的模块（:pep:`562`）。

    Raises:
        AttributeError: 名字不是本包公开的惰性导出项。
        ImportError: 依赖缺失——**原样抛出**让调用方看到真实原因，
            不包装成 AttributeError（那会被 ``hasattr`` 误判为"不存在"）。
    """
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    # 缓存到模块字典，后续访问不再走 __getattr__
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY])


__all__ = [
    "DEFAULT_RATE",
    "DEFAULT_VOICE",
    "MAX_SPEECH_CHARS",
    "DigitalHumanResult",
    "DigitalHumanUnavailableError",
    "SpeechError",
    "SpeechNotConfiguredError",
    "SpeechOutcome",
    "SpeechResult",
    "SpeechService",
    "SpeechSettings",
    "SpeechStage",
    "TTSEngine",
    "TTSUnavailableError",
    "TextTooLongForSpeechError",
    "FayClient",
    "get_engine",
    "get_service",
    "reset_engine",
    "reset_service",
]
