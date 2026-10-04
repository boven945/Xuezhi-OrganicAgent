"""API 网关层（`backend-api` 模块）。

把既有的化学引擎、模型适配、知识检索与Agent 编排包装为 HTTP 接口。

职责边界（`architecture.md` §2「API 网关：输入校验、请求关联、
路由、限流策略接入和响应整形」）：
**只做编排与整形，不含化学与检索逻辑**。任何领域判断都应下沉到
既有模块，此处不重复实现。

子模块：

- :mod:`app.api.models` —— 请求/响应契约（Pydantic）
- :mod:`app.api.errors` —— 稳定错误码与脱敏映射
- :mod:`app.api.ratelimit` —— 令牌桶限流
- :mod:`app.api.deps` —— 服务装配与生命周期
- :mod:`app.api.streaming` —— SSE 流式问答
- :mod:`app.api.routes` —— 路由
- :mod:`app.api.app` —— 应用工厂与全局异常处理
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """延迟导入并转调 :func:`app.api.app.create_app`。

    刻意不在 ``__init__`` 里导入 FastAPI：这样 ``import app.api.errors``
    不会连带拉起 FastAPI 依赖，纯逻辑测试可独立运行。
    """
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
