"""FastAPI 应用装配。

入口：``uvicorn app.api.app:app``。

## 全局异常处理

FastAPI 默认的异常响应有两处不满足项目要求（实测确认）：

1. **``RequestValidationError`` 返回英文技术文案**
   如 ``"Input should be a valid dictionary or object to extract fields from"``。
   本项目面向高中生，英文报错不可接受。
2. **校验失败响应里回显了原始 ``input`` 值**。
   实测 ``{"text": ""}`` 失败时响应含 ``"input": ""``。
   若用户把敏感内容填错字段，会被原样回显——违反
   ``security-privacy.md`` §3。因此自定义处理器**丢弃 input**。

另有一条实测发现必须记录：FastAPI 0.132.0 起默认
``strict_content_type=True``，客户端若不发
``Content-Type: application/json``，请求体**不会**被解析为 JSON，
直接返回 422（**不是 415**）。此行为对前端是硬约束，
已写入 ``docs/interface-contract-verification.md``。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import ServiceRegistry, ServiceSettings, new_request_id
from app.api.errors import API_ERROR_SPECS, build_error_payload, classify
from app.api.routes import router
from app.speech.store import AudioStore

logger = logging.getLogger(__name__)

#: 音频清扫周期（秒）。
#:
#: 取 300（5 分钟）而非更短：清扫本身要遍历目录，
#: 而音频 TTL 是 600 秒——**周期须明显小于 TTL**，
#: 否则极端情况下文件可能在过期后很久才被清走。
SWEEP_INTERVAL_SECONDS: Final[float] = 300.0

#: 校验错误的 Pydantic ``type`` → 中文文案。
#:
#: **按 type 而非 msg 匹配**是刻意的：Pydantic v1→v2 把
#: "field required" 改成 "Field required" 且改了 ``type``，
#: 按英文字符串匹配会**静默失效**（不报错，只是永远匹配不上）。
#: ``type`` 才是稳定标识。未知 type 回落 msg，保证不出现 KeyError。
_VALIDATION_MESSAGES: dict[str, str] = {
    "missing": "缺少必填字段。",
    "string_too_short": "内容太短。",
    "string_too_long": "内容太长。",
    "string_type": "应为文本。",
    "int_type": "应为整数。",
    "int_parsing": "应为整数。",
    "float_type": "应为数值。",
    "model_attributes_type": "请求体格式不正确（须为 JSON 对象）。",
    "json_invalid": "JSON 格式不正确。",
    "extra_forbidden": "包含不支持的字段。",
    "value_error": "取值不合法。",
    "too_short": "内容太短。",
    "greater_than_equal": "数值过小。",
    "less_than_equal": "数值过大。",
}

#: 校验失败时的通用用户文案。**刻意不暴露 Pydantic 的英文原文**
#: （实测默认msg 如 "Input should be a valid dictionary..."）。
_GENERIC_MESSAGES = {
    "api_invalid_input": "输入格式不正确，请检查后重试。",
    "api_internal_error": "服务内部错误，请稍后重试。",
}


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
    """构造应用。

    Args:
        settings: 服务配置。``None`` 时从环境变量读取。

    Returns:
        配置完成的 :class:`FastAPI` 实例。

    Notes:
        用工厂函数而非模块级全局 ``app``，是为了让测试能造多个
        互不干扰的实例（每个实例持有独立的 ``ServiceRegistry``）。
    """
    resolved = settings if settings is not None else ServiceSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """启动与关闭钩子。

        刻意**不在启动时构造模型客户端与向量库**：
        实测嵌入模型首载需数秒，若在 lifespan 里同步加载，
        `uvicorn --reload` 的开发体验会很差，且配置缺失会让
        容器反复重启、看不到任何可诊断的响应。

        改为只做探测并记日志，把真实失败留给 ``/health`` 报告。
        """
        # 复用 app.state 里已建好的容器，**不新建**——
        # 否则 lifespan 的实例与路由依赖拿到的会是两个对象，
        # 限流状态与惰性缓存都会分裂成两份（自查发现的一处冗余）。
        registry: ServiceRegistry = app.state.registry
        probes = registry.probe()
        ready = any(p.name == "llm" and p.ready for p in probes)
        logger.info(
            "服务已启动：ready=%s 组件=%s",
            ready,
            {p.name: p.ready for p in probes},
        )
        if resolved.strict_startup and not ready:
            # 生产显式要求：配置不全就别假装健康，直接失败让编排重启。
            raise RuntimeError("模型服务未配置（缺 MAAS_API_KEY？）且已开启严格启动")

        # ---------------- 音频存储与定时清扫 ----------------
        # 存储挂在 app.state，供路由依赖取用（见 routes.get_audio_store）
        #
        # 目录可由 XUEZHI_AUDIO_DIR 覆盖——**这不是可选项**：
        # 多 worker 部署时若各进程用各自的临时目录，
        # A 进程生成的音频 B 进程读不到，表现为**间歇性 404**，
        # 极难排查（已登记为决策 H23）。容器部署时该目录须挂共享卷。
        store = AudioStore(root=os.environ.get("XUEZHI_AUDIO_DIR") or None)
        app.state.audio_store = store
        store.ensure_root()

        # 定时清扫是**兜底**，不是唯一手段：惰性清理（store.fetch）
        # 已保证不会把过期音频给学生；这个保证磁盘不无限增长。
        # 两者都需要——演示长时间挂机时没有音频被取用，
        # 惰性清理永远不会触发。
        sweep_task = asyncio.create_task(_periodic_sweep(store))
        app.state.audio_sweep_task = sweep_task
        logger.info("音频存储就绪：目录=%s 存活=%ss", store.root, store.ttl_seconds)

        try:
            yield
        finally:
            # 先取消清扫再关停，避免关闭过程中它还在访问目录
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
            limiter = registry.limiter
            if limiter is not None:
                limiter.reset()
            logger.info("服务已关闭")

    async def _periodic_sweep(store: AudioStore) -> None:
        """周期性清扫过期音频。

        刻意**吞掉所有异常**：清扫是维护性工作，
        它失败不该让整个服务崩掉——故只在日志里留痕。
        """
        while True:
            try:
                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
                store.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("音频清扫异常：%s", type(exc).__name__)

    app = FastAPI(
        title="巡微智诊有机化学助手 API",
        version="1.0.0",
        description=(
            "高中有机化学教学问答服务。契约见 docs/interface-contract.md，"
            "实测记录见 docs/interface-contract-verification.md。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved
    # 提前建好容器，使未走 lifespan 的场景（如某些测试）也能取到
    app.state.registry = ServiceRegistry(resolved)

    # ---------------- CORS ----------------
    # 白名单模式。allow_credentials=True 时FastAPI 禁止 ["*"]，
    # 留空列表则完全不注册跨域（纯后端调用场景）。
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-Request-Id"],
            max_age=600,
        )
        logger.info("CORS 已启用，允许来源：%s", list(resolved.cors_origins))

    # ---------------- 限流 ----------------
    # 用 BaseHTTPMiddleware 包裹。见 ratelimit.py 的模块说明：
    # 刻意不用 slowapi（版本停滞、状态难隔离）。
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        registry: ServiceRegistry = request.app.state.registry
        limiter = registry.limiter
        if limiter is None:  # pragma: no cover - 防御性
            return await call_next(request)

        # 只限业务接口；健康检查与文档不限流，
        # 否则排障时探针会被自己的限流挡住。
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client = _client_key(request)
        allowed, retry_after = limiter.allow(client)
        if not allowed:
            payload = build_error_payload(
                _RateLimited(), request_id=new_request_id()
            )
            logger.info("限流触发：%s path=%s", client, request.url.path)
            return JSONResponse(
                status_code=API_ERROR_SPECS["api_rate_limited"].http_status,
                content=payload,
                headers={"Retry-After": str(max(1, int(retry_after)))},
            )
        return await call_next(request)

    # ---------------- 异常处理 ----------------

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """把校验错误转为中文、脱敏的响应。

        **丢弃 ``input`` 字段**（见模块 docstring 的实测依据）。
        """
        request_id = new_request_id()
        details = []
        for err in exc.errors():
            err_type = str(err.get("type", ""))
            loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query")]
            details.append(
                {
                    "field": ".".join(loc) or "__root__",
                    "code": err_type,
                    "message": _VALIDATION_MESSAGES.get(err_type, str(err.get("msg", ""))),
                }
            )
        logger.info("校验失败[%s]：%s", request_id, [d["code"] for d in details])
        return JSONResponse(
            status_code=API_ERROR_SPECS["api_invalid_input"].http_status,
            content={
                "error": {
                    "code": "api_invalid_input",
                    "message": _GENERIC_MESSAGES["api_invalid_input"],
                    "retryable": False,
                    "request_id": request_id,
                },
                "details": details,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底处理器。

        ``security-privacy.md`` §5：内部堆栈只进受控日志。
        这里**不返回异常类名或消息**——它们可能含内部路径、
        密钥片段或上游响应原文。

        状态码取自 :func:`app.api.errors.classify` 而非硬编码 500：
        实测确认缺配置（llm_not_configured）应为 503 而非 500，
        否则编排系统会当成进程内部错误反复重启。
        """
        request_id = new_request_id()
        logger.exception("未处理异常[%s]：%s", request_id, type(exc).__name__)
        payload = build_error_payload(exc, request_id=request_id)
        status_code = classify(exc).http_status
        return JSONResponse(status_code=status_code, content=payload)

    app.include_router(router)
    return app


class _RateLimited(Exception):
    """限流信号异常，用于复用统一错误响应构造。

    **不携带任何消息**——限流信息通过响应头 ``Retry-After`` 传达。
    ``code`` 属性让 :func:`app.api.errors.classify` 识别它，
    否则会被当成未知异常降级为 500 + "服务内部错误"，
    客户端就分不清"你太快了"和"服务端坏了"（实测踩过）。
    """

    code = "api_rate_limited"


def _client_key(request: Request) -> str:
    """构造限流用的客户端标识。

    **只信任 ``X-Forwarded-For`` 的第一段**：它是最靠近客户端的那一跳。
    注意该头可被伪造，故本限流是**防滥用而非防攻击**的——
    真要防攻击需在反向代理层做（``deployment-operations.md` §9）。
    没有代理头时退回到 peer 地址。
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return f"ip:{first}"
    client = request.client
    return f"peer:{client.host}" if client else "peer:unknown"


#: 模块级 app，供 ``uvicorn app.api.app:app`` 使用。
#:
#: 注意：这是在**导入时**构造的，配置错误会导致导入失败——
#: 文档与测试若只需 app 对象元信息（如生成 OpenAPI）时不受影响。
app = create_app()


__all__ = ["app", "create_app"]
