"""Edge-TTS 语音合成。

## 为什么用 edge-tts 而不是其他 TTS

- **无需密钥**：省掉一层凭据管理（`security-privacy.md` §3）。
- **纯 Python wheel**（实测 `edge_tts-7.2.8-py3-none-any.whl`）：
  无原生扩展，**不触发本机的应用控制策略**——
  这与 RDKit 的情况相反（对比 `chem-engine-verification.md` §2）。
- 中文音色质量可用，且有Word/Sentence 边界事件可驱动字幕。

代价是**必须联网**（它调用微软的在线服务），
这使它不满足"完全离线"。`product-scope.md` §5 已把
"保证第三方服务始终可用"列为非目标，故可接受；
但断网演示须改用本地 TTS，该项已登记为未决。

## API 实测依据（不要凭印象改）

`edge-tts==7.2.8` 实测签名：

```text
Communicate(text, voice, *, rate='+0%', volume='+0%', pitch='+0Hz',
            boundary='SentenceBoundary', connect_timeout=10, receive_timeout=60)
Communicate.save(audio_fname, metadata_fname=None)
```

**两个易错点**（都是实测确认，不是推测）：

1. ``rate`` 是**字符串**且**须带符号**（``'+0%'`` / ``'-10%'``），
   不是整数。传 ``0`` 或 ``10`` 都不对。
2. 异常类型是 ``edge_tts.exceptions`` 下的具体类
   （``NoAudioReceived`` / ``WebSocketError`` / ``UnexpectedResponse`` 等），
   **不应逐个捕获**——逐个捕获等于把库的实现细节抄进本项目，
   库升级新增异常类型时会静默漏网。统一捕获其基类
   ``EdgeTTSException`` 与 ``OSError`` / ``asyncio.TimeoutError``。
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Final

from .config import SpeechSettings
from .errors import SpeechNotConfiguredError, TTSUnavailableError, TextTooLongForSpeechError
from .models import SpeechResult

logger = logging.getLogger(__name__)

#: 单次合成调用的墙钟超时（秒）。
#:
#: 取 30 而非库默认的 receive_timeout=60：教学场景里等60 秒才
#: 告诉学生"语音不可用"毫无意义——文本早已显示，学生可以先读。
#: 语音是**附加**体验，不值得让它拖住主流程的观感。
SYNCHRONIZE_TIMEOUT: Final[float] = 30.0


class TTSEngine:
    """Edge-TTS 封装。

    无状态，可安全复用；建议按进程创建单例（见 :func:`get_engine`）。
    """

    def __init__(self, settings: SpeechSettings | None = None) -> None:
        # None 时从环境变量读，不留默认值——否则 XUEZHI_TTS_VOICE 等
        # 配置完全无效，且默认值合法故不会报错（详见 service.py 同处说明）
        self._settings = settings if settings is not None else SpeechSettings.from_env()

    @property
    def settings(self) -> SpeechSettings:
        return self._settings

    @staticmethod
    def _format_rate(rate: int) -> str:
        """把整数百分比转成 edge-tts 要求的字符串。

        实测确认 ``rate`` 须为**带符号的字符串**：``'+0%'`` / ``'-10%'``。
        正数不写 ``+`` 也能被解析，但显式写出来更一致、更好读。

        边界：0 须转成 ``'+0%'``，转成 ``'0%'`` 不符合库的约定格式。
        """
        return f"{rate:+d}%"

    def _truncate(self, text: str) -> tuple[str, bool]:
        """按上限截断文本。

        在**句子边界**截断而非硬截：硬截会把词/字切一半，
        合成出的语音末尾是断裂的半个字。找不到合适边界时
        才退到硬截，并明确返回 ``truncated=True``。

        截断点留一个省略号：学生会知道"还有内容"，
        而不是以为这就是全部。
        """
        limit = self._settings.max_chars
        if len(text) <= limit:
            return text, False

        window = text[:limit]
        # 依次尝试中英文句末标点，取最靠后的一个
        for punct in ("。", "！", "？", "；", ". ", "! ", "? ", "; "):
            idx = window.rfind(punct)
            if idx > limit // 2:
                # +1 是把标点本身带上
                return window[: idx + 1] + "……", True
        return window + "……", True

    async def synthesize(self, text: str) -> SpeechResult:
        """合成语音。

        **本方法不抛异常**（除编程错误外）：任何失败都转成
        ``stage="unavailable"`` 的结果。这是 :mod:`app.speech.service`
        能"永远返回结果"的前提。

        Args:
            text: 待播报文本。

        Returns:
            :class:`SpeechResult`。成功时 ``audio_path`` 指向
            临时文件，**调用方负责在用完后删除**（见
            :func:`cleanup`）。
        """
        settings = self._settings
        if not settings.tts_enabled:
            return SpeechResult(stage="disabled", reason="语音功能已关闭。")

        # ---- 输入校验 ----
        if not isinstance(text, str):
            raise TypeError("待合成文本必须是字符串")
        stripped = text.strip()
        if not stripped:
            # 空白输入不算错误：模型可能返回空答复，
            # 这时"没有语音"是正确结果，不必提示任何异常
            return SpeechResult(stage="disabled", reason="没有可播报的内容。")

        try:
            voice = settings.require_voice()
        except SpeechNotConfiguredError as exc:
            return SpeechResult(stage="not_configured", reason=exc.user_message)

        truncated_text, truncated = self._truncate(stripped)
        char_count = len(truncated_text)

        # ---- 依赖可用性 ----
        # 延迟导入：edge_tts 未安装时 API 网关仍须能启动
        # （与 app/chem/__init__.py 的 PEP 562 延迟加载同一考量）。
        try:
            from edge_tts import Communicate
        except ImportError as exc:
            logger.warning("edge-tts 未安装，语音不可用：%s", type(exc).__name__)
            return SpeechResult(
                stage="unavailable",
                reason="语音合成组件未安装。",
                truncated=truncated,
                char_count=char_count,
                detail="edge_tts import failed",
            )

        # ---- 合成 ----
        # 用临时目录而非固定文件名：并发请求下固定名会互相覆盖，
        # 而学生可能同时收到两条答复的音频。
        tmp_dir = Path(tempfile.mkdtemp(prefix="xuezhi-tts-"))
        out_path = tmp_dir / "speech.mp3"

        try:
            rate = self._format_rate(settings.rate)
            communicate = Communicate(truncated_text, voice, rate=rate)
            await asyncio.wait_for(
                communicate.save(str(out_path)),
                timeout=SYNCHRONIZE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._cleanup_dir(tmp_dir)
            logger.warning("语音合成超时（%.0fs）", SYNCHRONIZE_TIMEOUT)
            return SpeechResult(
                stage="unavailable",
                reason="语音合成超时。",
                truncated=truncated,
                char_count=char_count,
                detail="asyncio.TimeoutError",
            )
        except Exception as exc:  # noqa: BLE001 - 见模块说明：统一捕获基类
            self._cleanup_dir(tmp_dir)
            # 只记类型不记完整消息——上游消息可能含内部 URL 或令牌
            logger.warning("语音合成失败：%s", type(exc).__name__)
            return SpeechResult(
                stage="unavailable",
                reason="语音服务暂时不可用。",
                truncated=truncated,
                char_count=char_count,
                detail=type(exc).__name__,
            )

        # ---- 校验产物 ----
        # 实测教训：不校验会得到"stage=ready 但文件不存在"的结果，
        # 前端拿到一个 404 的音频 URL 却毫无提示地失败。
        if not out_path.exists() or out_path.stat().st_size == 0:
            self._cleanup_dir(tmp_dir)
            logger.warning("语音合成未产出有效音频")
            return SpeechResult(
                stage="unavailable",
                reason="语音合成未产出音频。",
                truncated=truncated,
                char_count=char_count,
                detail="empty output",
            )

        return SpeechResult(
            stage="ready",
            audio_path=str(out_path),
            truncated=truncated,
            char_count=char_count,
        )

    def synthesize_blocking(self, text: str) -> SpeechResult:
        """同步包装 :meth:`synthesize`。

        给同步调用方用。**内部起独立事件循环**而非
        ``asyncio.run``——后者在被已有事件循环的线程里调用会抛
        ``RuntimeError``，而 FastAPI 的同步端点恰好可能跑在
        有循环的线程中。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.synthesize(text))

        # 已在事件循环中：另起线程跑，避免嵌套
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.synthesize(text)).result()

    @staticmethod
    def _cleanup_dir(path: Path) -> None:
        """删除临时目录，失败只记日志。

        刻意吞掉所有异常：清理失败不该让主流程失败，
        残留的临时文件由操作系统回收。
        """
        try:
            for child in path.iterdir():
                try:
                    os.remove(child)
                except OSError:
                    pass
            path.rmdir()
        except OSError as exc:
            logger.debug("临时目录清理失败：%s", type(exc).__name__)

    @staticmethod
    def cleanup(result: SpeechResult) -> None:
        """清理 :meth:`synthesize` 产出的临时文件。

        供调用方在播放完成后调用。**幂等**：文件已不存在时不报错。
        """
        if not result.audio_path:
            return
        path = Path(result.audio_path)
        parent = path.parent
        TTSEngine._cleanup_dir(parent if parent.name.startswith("xuezhi-tts-") else path)


_ENGINE: TTSEngine | None = None


def get_engine() -> TTSEngine:
    """获取进程级单例。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = TTSEngine()
    return _ENGINE


def reset_engine() -> None:
    """重置单例。仅供测试使用。"""
    global _ENGINE
    _ENGINE = None


__all__ = ["SYNCHRONIZE_TIMEOUT", "TTSEngine", "get_engine", "reset_engine"]
