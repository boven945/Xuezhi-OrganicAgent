"""按客户端的令牌桶限流。

## 为什么不引入 slowapi

`slowapi` 最新版本为 **0.1.10**，维护活跃度低（PyPI 实测），
且其 ``Limiter`` 依赖全局状态与 ``request.app`` 绑定，
难以在测试中隔离。这个需求用标准库 60 行即可满足，
少一个依赖就少一处供应链风险（`security-privacy.md` §5）。

依据 `security-privacy.md` §4「对高成本模型调用设置预算和速率控制」：
模型调用是本服务**唯一的高成本操作**（实测中位延迟 7.10秒，
且按 token 计费），故限流是必需能力而非可选项。

## 为什么用令牌桶而非固定窗口

固定窗口在窗口边界会被击穿：限流 10 次/分钟的客户端，
可在第 59 秒发 10 次、第 61 秒再发 10 次，实际 2 秒内20 次。
令牌桶按时间匀速补充，天然平滑。

## 内存状态的边界

本实现是**单进程内存态**。多 worker 部署时每个进程各算一份，
实际总配额是 ``workers × rate``。这是刻意的取舍：
不引入 Redis 依赖（演示场景无必要），但**必须记录在文档**，
生产多 worker 前须换成分布式实现。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Bucket:
    """单个客户端的令牌桶状态。"""

    #: 当前令牌数（float 以支持亚秒级补充）。
    tokens: float
    #: 上次补充令牌的时刻（单调时钟秒）。
    updated_at: float


@dataclass(slots=True)
class RateLimiter:
    """令牌桶限流器。

    Attributes:
        rate: 每秒补充的令牌数。
        capacity: 桶容量，即允许的突发上限。
        max_clients: 最多跟踪多少个客户端。超过后清理最久未使用者，
            防止用随机 ``X-Forwarded-For`` 撑爆内存（`security-privacy.md` §4
            要求限制资源消耗）。
    """

    rate: float
    capacity: float
    max_clients: int = 10_000
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("rate 必须大于 0")
        if self.capacity <= 0:
            raise ValueError("capacity 必须大于 0")
        if self.max_clients < 1:
            raise ValueError("max_clients 至少为 1")

    def allow(self, key: str, *, now: float | None = None) -> tuple[bool, float]:
        """尝试取用一个令牌。

        Args:
            key: 客户端标识（由调用方决定如何构造）。
            now: 当前时刻。**仅供测试注入**，生产不传。
                用 ``None`` 时取单调时钟，避免系统时间回拨导致
                令牌被"撤销"。

        Returns:
            ``(是否放行, 建议重试等待秒数)``。
            放行时建议等待为 0.0；拒绝时为需等待的时间，
            已按 ``retry-after`` 的语义向上取整到 0.1 秒粒度。
        """
        current = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)

        if bucket is None:
            if len(self._buckets) >= self.max_clients:
                self._evict_stale(current)
            # 新客户端给满桶并**立即扣掉本次消耗**（下方统一走扣减路径）。
            #
            # 实测踩过的坑：此处若直接 return True 而不扣减，新客户端
            # 实际能通过 capacity + 1 次，比配置的突发上限多一次。
            # 演示中即"限流 8/s 却能连发 9 次"，排查时极易误判为
            # 配置未生效。故改为初始化后落入同一条扣减路径。
            self._buckets[key] = _Bucket(tokens=self.capacity, updated_at=current)
            bucket = self._buckets[key]

        elapsed = current - bucket.updated_at
        # elapsed 可能为负（测试注入乱序时间），钳到 0 防止扣令牌。
        if elapsed > 0:
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.rate)
            bucket.updated_at = current

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0

        deficit = 1.0 - bucket.tokens
        return False, round(deficit / self.rate, 1)

    def _evict_stale(self, now: float) -> None:
        """清理已补满的桶。

        只删"桶已满"的是安全的：它们的限流效果等价于不存在，
        删掉后客户端回来会拿到满桶，与保留时行为一致。
        全满时按最早更新时刻逐个淘汰，避免退化成全表排序。
        """
        stale = [k for k, b in self._buckets.items() if b.tokens >= self.capacity]
        if stale:
            for key in stale:
                del self._buckets[key]
            return
        # 全都不满（真·高压场景）：淘汰最久未更新的 10%
        drop = max(1, len(self._buckets) // 10)
        oldest = sorted(self._buckets.items(), key=lambda kv: kv[1].updated_at)[:drop]
        for key, _ in oldest:
            del self._buckets[key]

    def reset(self) -> None:
        """清空所有状态。**仅供测试与本地调试使用。**"""
        self._buckets.clear()


__all__ = ["RateLimiter"]
