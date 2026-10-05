"""服务装配与生命周期。

## 核心问题：缺配置时怎么办

`llm/config.py` 的既定原则是「配置缺失即快速失败」
（`deployment-operations.md` §6）。但对 HTTP 服务而言，
"启动即崩溃"与"启动成功但每个请求都失败"是两种不同的运维语义：

- **崩溃**：容器编排反复重启，日志里全是栈，但**没有任何请求能成功**，
  也没有任何响应能说明原因。
- **可启动但拒绝服务**：``/health`` 返回 ``not_ready`` 并说明
  「缺 MAAS_API_KEY」，运维一眼看到；其余组件（RDKit 等）仍可被探测。

本模块选择后者，因为 API 层的第一职责是**把故障说清楚**。

同时保留 ``strict_startup`` 开关：生产部署应显式设为 true，
让配置错误在启动时暴露，而不是等第一个用户请求失败。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent.dispatcher import AgentLoop, ToolDispatcher
from app.agent.knowledge_tools import build_knowledge_tools
from app.agent.tools import ToolRegistry, build_chem_tools
from app.api.ratelimit import RateLimiter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ServiceSettings:
    """API 服务配置。

    全部来自环境变量，**不提供任何带默认值的密钥**
    （与 :class:`app.llm.config.LLMConfig` 同一原则）。

    Attributes:
        strict_startup: 配置缺失时是否让进程启动失败。
            生产应设 ``XUEZHI_STRICT_STARTUP=1``。
        cors_origins: 允许的跨域来源。白名单，**不用通配符**
            （`security-privacy.md` §3）。
        rate_limit_rps: 每秒允许的请求数。
        rate_limit_burst: 突发上限。
        request_timeout: 单请求端到端超时（秒）。
    """

    strict_startup: bool = False
    cors_origins: tuple[str, ...] = ()
    rate_limit_rps: float = 1.0
    rate_limit_burst: int = 8
    request_timeout: float = 120.0
    #: 检索相似度阈值。``None`` 表示不设——实测 41 条语料下
    #: 五个阈值结果完全相同，设了反而无依据（见 I1）。
    retrieval_threshold: float | None = None
    retrieval_top_k: int = 4
    #: chroma 持久化目录。**不得由用户输入决定**（`security-privacy.md` §4）。
    chroma_path: str = "data/chroma"
    #: 集合名。chromadb 只接受 ``[a-zA-Z0-9._-]``（实测约束）。
    collection_name: str = "xuezhi_organic"
    #: 索引版本，与评估报告对应。变更须同步更新 `docs/`。
    index_version: str = "unversioned"

    def __post_init__(self) -> None:
        """逐项校验配置。

        **在此校验而非等到限流器构造时**：配置错误的报错应指向
        配置本身。实测踩过——原先只由 ``RateLimiter.__post_init__``
        校验，导致 ``ServiceSettings(rate_limit_burst=0)`` 能构造成功，
        却要等到应用启动才抛错，报错位置离原因很远。
        """
        if self.rate_limit_rps <= 0:
            raise ValueError("限流速率必须大于 0")
        if self.rate_limit_burst < 1:
            raise ValueError("限流突发上限至少为 1")
        if self.request_timeout <= 0:
            raise ValueError("请求超时必须大于 0")
        if self.retrieval_top_k < 1:
            raise ValueError("检索返回条数至少为 1")
        if self.collection_name and not all(
            ch.isalnum() or ch in "._-" for ch in self.collection_name
        ):
            # chromadb 对集合名有此约束（实测），越界会在建库时报错
            raise ValueError(
                "集合名只允许字母、数字、点、下划线与连字符："
                f"{self.collection_name!r}"
            )
        if self.retrieval_threshold is not None and not (
            0.0 <= self.retrieval_threshold <= 2.0
        ):
            # cosine 距离范围 [0, 2]
            raise ValueError("检索阈值须在 0.0 到 2.0 之间")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ServiceSettings:
        """从环境变量构造。

        读取失败一律回落到默认值而非抛错——服务配置本身不该
        成为启动阻塞项，真正的阻塞项由 :meth:`ServiceRegistry.probe` 报告。
        """

        def _get(key: str) -> str | None:
            import os

            return (env if env is not None else os.environ).get(key)

        def _num(key: str, default: float) -> float:
            raw = _get(key)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("环境变量 %s 取值非法，回落默认 %s", key, default)
                return default

        def _flag(key: str, default: bool) -> bool:
            raw = _get(key)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        raw_threshold = _get("XUEZHI_RETRIEVAL_THRESHOLD")
        threshold: float | None = None
        if raw_threshold:
            try:
                candidate = float(raw_threshold)
                # cosine 距离范围 [0, 2]，越界说明配置有误
                if 0.0 <= candidate <= 2.0:
                    threshold = candidate
                else:
                    logger.warning(
                        "XUEZHI_RETRIEVAL_THRESHOLD=%.2f 超出 cosine 距离范围 [0,2]，已忽略",
                        candidate,
                    )
            except ValueError:
                logger.warning("XUEZHI_RETRIEVAL_THRESHOLD 不是数字，已忽略")

        raw_origins = _get("XUEZHI_CORS_ORIGINS") or ""
        origins = tuple(o.strip() for o in raw_origins.split(",") if o.strip())

        return cls(
            strict_startup=_flag("XUEZHI_STRICT_STARTUP", False),
            cors_origins=origins,
            rate_limit_rps=_num("XUEZHI_RATE_LIMIT_RPS", 1.0),
            rate_limit_burst=int(_num("XUEZHI_RATE_LIMIT_BURST", 8)),
            request_timeout=_num("XUEZHI_REQUEST_TIMEOUT", 120.0),
            retrieval_threshold=threshold,
            retrieval_top_k=int(_num("XUEZHI_RETRIEVAL_TOP_K", 4)),
            chroma_path=_get("XUEZHI_CHROMA_PATH") or "data/chroma",
            collection_name=_get("XUEZHI_COLLECTION") or "xuezhi_organic",
            index_version=_get("XUEZHI_INDEX_VERSION") or "unversioned",
        )


@dataclass(slots=True)
class ComponentProbe:
    """单个组件的探测结果。"""

    name: str
    ready: bool
    detail: str = ""
    #: 子能力开关。**给机器读**，detail 是给人看的。
    #: 见ComponentStatus.caps 的说明（前端不应解析 detail）。
    caps: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class ServiceRegistry:
    """各模块单例的持有者。

    **刻意不在导入期构造**。理由：
    - RDKit 与嵌入模型的加载有实际开销（实测 bge-small-zh 首载
      需数秒），导入期加载会让 ``uvicorn`` 启动变慢且难以测试；
    - 配置缺失时导入期抛错会让``--reload`` 与文档工具都无法导入模块。

    采用惰性构造 + :meth:`probe` 显式报告可用性的组合。
    """

    settings: ServiceSettings = field(default_factory=ServiceSettings)
    limiter: RateLimiter | None = None
    _llm_client: Any = None
    _store: Any = None
    _agent_loop: Any = None
    #: 化学工具是否可用。RDKit 被应用控制策略拦截时为 False，
    #: 此时 Agent 仅挂知识检索工具。见 :meth:`get_agent_loop`。
    _chem_available: bool = True
    _probes: dict[str, ComponentProbe] = field(default_factory=dict)
    #: 探测结果缓存。见 :meth:`probe` 的说明——``/health`` 高频调用
    #: 不应每次都重跑 RDKit 解析与客户端构造。
    _probe_cache: list[ComponentProbe] | None = None

    def __post_init__(self) -> None:
        if self.limiter is None:
            self.limiter = RateLimiter(
                rate=self.settings.rate_limit_rps,
                capacity=float(self.settings.rate_limit_burst),
            )

    # ------------------------------------------------------------------
    # 惰性构造
    # ------------------------------------------------------------------

    def get_llm_config(self) -> Any:
        """取得模型配置。**只读不建客户端**。

        与 :meth:`get_llm_client` 共用同一实例——
        两者读同一份环境变量，若各读一次，
        理论上可能出现"配置变了但客户端还是旧的"。
        """
        if self._llm_config is None:
            from app.llm.config import LLMConfig

            self._llm_config = LLMConfig.from_env()
        return self._llm_config

    def get_llm_client(self) -> Any:
        """取得模型客户端。失败时抛 :class:`LLMConfigError`。"""
        if self._llm_client is None:
            from app.llm.client import LLMClient

            config = self.get_llm_config()
            self._llm_client = LLMClient(config)
            logger.info("模型客户端已就绪：%s", config.public_summary())
        return self._llm_client

    def get_store(self) -> Any:
        """取得知识库存储。

        复用 :func:`app.rag.store.build_store` 工厂——它已把
        chromadb 导入失败、目录权限问题等转成受控的
        :class:`~app.rag.errors.IndexNotReadyError`，此处不重复该逻辑。

        嵌入模型走 :class:`SentenceTransformerEmbedding`（延迟加载，
        构造不下载权重）。指定 ``XUEZHI_EMBEDDING_PATH`` 时转为离线模式，
        避免无网时尝试连 HuggingFace——断网演示必须预下载约 400MB 权重，
        这是既有部署约束。
        """
        if self._store is None:
            from app.rag.embeddings import (
                SentenceTransformerEmbedding,
                resolve_local_model_path,
            )
            from app.rag.store import build_store

            local_path = resolve_local_model_path()
            embedding = SentenceTransformerEmbedding(
                local_files_only=local_path is not None,
                **({"cache_folder": local_path} if local_path else {}),
            )
            self._store = build_store(
                self.settings.chroma_path,
                self.settings.collection_name,
                embedding,
                index_version=self.settings.index_version,
            )
            logger.info(
                "知识库已初始化：collection=%s 离线权重=%s",
                self.settings.collection_name,
                bool(local_path),
            )
        return self._store

    def get_agent_loop(self) -> Any:
        """取得 Agent 主循环。

        **化学工具是可选的**（实测 2026-10-04）：
        RDKit 的 C++ 扩展可能被 Windows 应用控制策略按签名拦截，
        此时 ``build_chem_tools()`` 抛 ``ImportError``。

        降级策略：**只用知识检索工具**继续服务。
        理由来自 ``architecture.md`` §6——「将文本回答设为主交付，
        动画与语音视为可降级能力」。化学结构解析虽重要，
        但没有它学生仍能问知识性问题；若因此让整个服务不可用，
        代价远大于收益。

        化学能力是否可用由 ``/health`` 的``chem`` 组件如实报告，
        不静默假装正常。
        """
        if self._agent_loop is None:
            tools: list[Any] = []
            try:
                tools.extend(build_chem_tools())
            except ImportError as exc:
                # 只记类型不记完整消息——后者含内部路径
                logger.warning(
                    "化学工具不可用，本次仅启用知识检索: %s", type(exc).__name__
                )
                self._chem_available = False
            tools.extend(
                build_knowledge_tools(
                    self.get_store(),
                    threshold=self.settings.retrieval_threshold,
                    default_top_k=self.settings.retrieval_top_k,
                )
            )
            registry = ToolRegistry(tools)
            dispatcher = ToolDispatcher(registry)
            # 人设：仅在配置开启时注入，关闭时行为与引入人设前一致。
            # 用 `TEACHER_SYSTEM_PROMPT` 作为人设源，
            # 具体如何与功能契约拼接由 LLMClient.compose_system_prompt 负责。
            persona = None
            if self.get_llm_config().persona_enabled:
                from app.llm.persona import TEACHER_SYSTEM_PROMPT

                persona = TEACHER_SYSTEM_PROMPT
            self._agent_loop = AgentLoop(
                self.get_llm_client(), dispatcher, persona=persona
            )
            logger.info(
                "Agent 循环已就绪，工具白名单：%s，人设：%s",
                list(registry.names()),
                "化学老师" if persona else "无",
            )
        return self._agent_loop

    # ------------------------------------------------------------------
    # 探测
    # ------------------------------------------------------------------

    def probe(self) -> list[ComponentProbe]:
        """探测各组件可用性。

        **每个组件独立 try**：一个组件失败不能连带其他组件判为不可用，
        否则运维会看到"全挂"，而实际只是缺个密钥。

        **结果被缓存**：``/health`` 每次请求都会调本方法，
        而 chem 探测要真跑一遍 RDKit 解析、llm 探测要构造客户端。
        不缓存会让健康检查变成性能负担，且编排系统的高频探测会放大它。
        缓存后组件状态在进程生命周期内不变——对配置固定的服务这是成立的
        （要改配置就重启，这正是容器化部署的常态）。
        """
        if self._probe_cache is not None:
            return self._probe_cache

        results: list[ComponentProbe] = []

        # RDKit：纯本地，探测代价极低，且失败意味着整个化学工具不可用。
        try:
            from app.chem.engine import get_engine

            engine = get_engine()
            engine.parse("CCO")
            results.append(ComponentProbe("chem", True, "RDKit 就绪"))
        except Exception as exc:  # noqa: BLE001 - 探测须吞掉一切
            results.append(ComponentProbe("chem", False, type(exc).__name__))
            logger.warning("化学引擎探测失败：%s", type(exc).__name__)

        # 模型服务：缺密钥是预期的配置状态，不是崩溃。
        try:
            self.get_llm_client()
            results.append(ComponentProbe("llm", True, "已配置"))
        except Exception as exc:  # noqa: BLE001
            # 只取异常类型，不取消息——消息可能含配置细节
            results.append(ComponentProbe("llm", False, type(exc).__name__))

        # 知识库：需要嵌入模型，代价较高，只探测一次。
        if "rag" not in self._probes:
            try:
                self.get_store()
                self._probes["rag"] = ComponentProbe("rag", True, "已加载")
            except Exception as exc:  # noqa: BLE001
                self._probes["rag"] = ComponentProbe(
                    "rag", False, type(exc).__name__
                )
                logger.warning("知识库探测失败：%s", type(exc).__name__)
        results.append(self._probes["rag"])

        # 语音：与 chem/rag 分开探测，且**不实际合成音频**。
        # 合成要调edge-tts 的在线服务（耗时数秒），
        # 而 /health 会被编排系统高频轮询——真合成会把
        # 健康检查变成网络基准测试。这里只报配置是否就绪。
        if "speech" not in self._probes:
            try:
                from app.speech import SpeechService

                available = SpeechService().probe()
                detail = (
                    f"tts={'就绪' if available['tts'] else '未启用'} "
                    f"fay={'就绪' if available['fay'] else '未启用'}"
                )
                # 语音**从不**阻断就绪判定：它是纯增强能力
                # （architecture.md §6），故无论哪种状态都记 ready。
                # 真实可用性由 stage 字段与reason 表达，不在此处断言。
                self._probes["speech"] = ComponentProbe(
                    "speech",
                    True,
                    detail,
                    # 结构化子能力：前端据此决定数字人窗口的行为，
                    # 不必解析上面的 detail 字符串。detail 是给人看的，
                    # caps 是给机器读的——两者不能混用。
                    caps={
                        "tts": bool(available["tts"]),
                        "fay": bool(available["fay"]),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - 探测须吞掉一切
                self._probes["speech"] = ComponentProbe(
                    "speech", False, type(exc).__name__
                )
                logger.warning("语音服务探测失败：%s", type(exc).__name__)
        results.append(self._probes["speech"])

        self._probe_cache = results
        return results

    def is_ready(self) -> bool:
        """是否可服务。

        判据：**模型可用即视为可服务**。知识库不可用时，
        Agent 仍能通过化学工具与模型自身知识回答（只是没有教材来源），
        此时应返回 200 + ``partial`` 而非整站不可用——
        这与 ``architecture.md`` §6「将文本回答设为主交付」一致。
        """
        return any(p.name == "llm" and p.ready for p in self.probe())


def new_request_id() -> str:
    """生成请求关联标识。

    用 UUID4 十六进制前 16 位。**不含任何个人信息或凭据**
    （``interface-contract.md` §3 对 request_id 的硬约束）。
    """
    return uuid.uuid4().hex[:16]


class Timer:
    """单调计时器。

    用 :func:`time.perf_counter` 而非 :func:`time.time`：
    后者会被 NTP 校正或夏令时拨动，可能得到负的耗时。
    """

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed(self) -> float:
        return round(time.perf_counter() - self._start, 3)


__all__ = [
    "ComponentProbe",
    "ServiceRegistry",
    "ServiceSettings",
    "Timer",
    "new_request_id",
]
