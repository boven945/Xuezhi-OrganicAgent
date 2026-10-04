"""临时音频的存储与过期管理。

## 为什么需要独立模块

``SpeechService.speak()`` 产出的是**临时文件路径**，
而路径只在**产生它的进程内**有意义（`/tmp/xuezhi-tts-xxxx/speech.mp3`）。
多 worker 部署时，A 进程返回的路径在 B 进程上根本不存在；
进程重启后更是不存在。

故需要一个**以 audio_id 为键、可跨请求定位**的存储层，
并配套两条清理路径（见 :class:`AudioStore`）。

## 决策依据（H21，2026-10-04）

### 为什么两阶段而非 base64 内联

实测与联网核实：

- base64 内联**体积 +33%**且**不能被浏览器单独缓存**
  （数据在 JSON 里，浏览器无法 range 请求、无法复用缓存）。
  实测26KB 音频 → base64 后约 35KB；短语音可接受，
  但**长语音会明显劣化**。
- 独立音频端点可被浏览器缓存、可 range 请求（拖动进度条）。

### 为什么清理要两条路径（用户决策：两者都要）

**实测踩到的坑**：starlette 1.7.0 的 ``BackgroundTask``
**没有 shield 保护**（``inspect.getsource`` 确认源码里无 ``CancelScope``），
而本项目**正好用了** ``BaseHTTPMiddleware``——
这正是 starlette #1438 报告的组合：**客户端断开时后台任务被取消**。

后果：若把清理挂在 ``BackgroundTask`` 上，**客户端一断开就漏**，
临时文件永久残留。故：

- **惰性清理**（:meth:`AudioStore.fetch`）：取音频时判断是否超龄，
  超了就删并返回 404。**保证正确性**——不依赖任何后台机制，
  进程重启也不影响（过期的自然被拒）。
- **定时清扫**（:meth:`AudioStore.sweep`）：兼顶磁盘不被残留占满。
  演示长时间挂机时尤其必要。

**两者不是冗余**：前者保证「不会把过期音频给学生」，
后者保证「磁盘不会无限增长」。缺前者会给出过期音频，
缺后者磁盘会满。

## 多 worker 的行为

本模块**不试图**在多 worker 间共享目录（那需要 Redis 或共享卷，
属独立部署决策）。单 worker 下完全正确；
多 worker 时每个进程各扫自己的目录，**清理是幂等的**
（删不存在的文件不算失败），故即便重复执行也无害。

**前提**：所有 worker 必须挂载**同一个**数据目录——
否则 A 进程生成的音频 B 进程读不到。这是部署要求，
已写入 ``deployment-operations.md`` 的待办（见决策 H23）。
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

#: audio_id 的格式。
#:
#: 刻意用 ``uuid4().hex``（32 位十六进制）而非短 id：
#: 猜对概率可忽略，且长度仍在URL 友好范围内。
#:
#: **不透明**是重点——不编码时间、进程号等信息，
#: 避免泄露"这是第几个请求"这类可被推断的信息。
_AUDIO_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")

#: 音频默认存活时长（秒）。
#:
#: 取 600（10 分钟）而非更长：教学场景中语音是**即时消费**的，
#: 学生听到后音频就没有价值了。留10 分钟足够覆盖
#: "生成后student 走神了一会儿"的情况。
#:
#: 同时它也是**安全属性**：过期的音频不应还能被拿到
#（可能是上一节课的内容）。
DEFAULT_TTL_SECONDS: Final[float] = 600.0

#: 允许的最大音频字节数（20MB）。
#:
#: 实测 26KB 的语音远低于此。上限的作用是**防御**：
#: 若将来换成会产出大文件的 TTS，不会让单个请求撑爆内存
#: （``FileResponse`` 会把文件读进内存，见下方读取策略）。
MAX_AUDIO_BYTES: Final[int] = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StoredAudio:
    """一条已存储的音频记录。"""

    audio_id: str
    path: Path
    size: int
    created_at: float

    def age(self, now: float | None = None) -> float:
        """存活时长（秒）。"""
        return (now if now is not None else time.time()) - self.created_at

    def is_expired(self, ttl: float, now: float | None = None) -> bool:
        return self.age(now) > ttl


class AudioStore:
    """临时音频的存储与清理。

    ## 文件布局

    ``<root>/<audio_id>.mp3``——**平铺，不分子目录**。
    子目录会让"扫目录清理"变成递归遍历，
    而平铺只需一次 ``iterdir``（实测本项目音频量级很小）。

    ## 命名与安全

    ``audio_id`` 由**服务端生成**（uuid4），不接受用户输入。
    即便将来开放了查询端点，也不存在"用户指定文件名"这条路径——
    :meth:`fetch` 会校验 id 格式，不合法的直接拒绝，
    杜绝了 ``../`` 这类路径穿越。
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("音频存活时长必须大于 0")
        self._root = Path(root) if root is not None else Path(tempfile.gettempdir()) / "xuezhi-audio"
        self._ttl = ttl_seconds

    @property
    def root(self) -> Path:
        return self._root

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def ensure_root(self) -> Path:
        """确保目录存在并返回。"""
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def put(self, source: Path) -> StoredAudio:
        """把合成产物登记进存储。

        刻意**移动**而非复制：产物本身在临时目录里，
        留着两份没有意义，且容易让人以为其中的还在被使用。

        Args:
            source: :meth:`app.speech.tts.TTSEngine.synthesize` 的产物。

        Returns:
            :class:`StoredAudio`，其 ``audio_id`` 用于后续取回。

        Raises:
            ValueError: 源文件不存在、为空或超出体积上限。
        """
        if not source.exists():
            raise ValueError("待存储的音频文件不存在")
        size = source.stat().st_size
        if size == 0:
            raise ValueError("待存储的音频文件为空")
        if size > MAX_AUDIO_BYTES:
            raise ValueError(
                f"音频体积 {size} 字节超出上限 {MAX_AUDIO_BYTES}"
            )

        audio_id = uuid.uuid4().hex
        target = self.ensure_root() / f"{audio_id}.mp3"
        source.replace(target)
        return StoredAudio(
            audio_id=audio_id, path=target, size=size, created_at=time.time()
        )

    def fetch(self, audio_id: str) -> StoredAudio | None:
        """按 id 取音频，**顺带做惰性清理**。

        找不到或已过期都返回 ``None``——调用方一律回 404，
        **不区分二者**：区分会让"曾经存在过"成为可观测信息，
        而前端不需要知道（`security-privacy.md` §3）。

        这是 :data:`DEFAULT_TTL_SECONDS` 的**正确性保证**：
        即使定时清扫从未运行过，也不会把过期音频交给学生。
        """
        if not _AUDIO_ID_PATTERN.match(audio_id):
            # 格式不合法 = 不是我方生成的 id，直接拒绝。
            # 刻意不查文件系统——那会给路径穿越留下缝隙。
            return None

        path = self._root / f"{audio_id}.mp3"
        if not path.exists():
            return None

        created = path.stat().st_mtime
        now = time.time()
        if now - created > self._ttl:
            #过期：删掉并返回 None。
            # 删除失败只记日志——返回 None 仍能让调用方回 404，
            # 不该因为清理失败而把请求变成 500。
            self._unlink(path, reason="expired")
            return None

        return StoredAudio(
            audio_id=audio_id,
            path=path,
            size=path.stat().st_size,
            created_at=created,
        )

    def sweep(self, now: float | None = None) -> int:
        """删除所有超龄文件。

        与 :meth:`fetch` 的分工：那个保证「不给过期音频」，
        这个保证「磁盘不无限增长」。**两者都需要**——
        演示长时间挂机时，没有音频被取用，
        惰性清理永远不会触发，磁盘就满了。

        Returns:
            删除的文件数。**幂等**：删不存在的文件不算失败，
            故多 worker 各扫各的目录也不会互相干扰。
        """
        if not self._root.exists():
            return 0
        current = now if now is not None else time.time()
        removed = 0
        for path in self._root.glob("*.mp3"):
            try:
                if current - path.stat().st_mtime > self._ttl:
                    self._unlink(path, reason="sweep")
                    removed += 1
            except OSError as exc:  # noqa: PERF203 -逐个文件，单个失败不影响其余
                logger.debug("扫描 %s 失败：%s", path.name, type(exc).__name__)
        if removed:
            logger.info("语音清扫：删除 %d 个超龄文件", removed)
        return removed

    def count(self) -> int:
        """当前存储的文件数（供测试与诊断用）。"""
        if not self._root.exists():
            return 0
        return sum(1 for _ in self._root.glob("*.mp3"))

    @staticmethod
    def _unlink(path: Path, *, reason: str) -> None:
        """删除单个文件，失败只记日志。"""
        try:
            path.unlink()
        except OSError as exc:
            logger.warning(
                "删除音频失败（%s）：%s", reason, type(exc).__name__
            )


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_AUDIO_BYTES",
    "AudioStore",
    "StoredAudio",
]
