"""API 请求与响应的数据契约。

本模块是 ``interface-contract.md` §3「答复语义字段建议」的**实现**，
把文档里的语义字段固化为可校验、可生成 OpenAPI 的 Pydantic 模型。

## 关键设计决定（及其实测依据）

**1. 长度上限取4096 字符，而非"越大越好"。**
``security-privacy.md` §4 要求对文本长度设上限，但阈值需实测。
定为 4096 的理由：中文一字符≈1 token 量级，4096 字节约对应
4Ktoken 上下文，相对模型 512K 窗口可忽略，却能挡住
"粘贴整本教材"这类滥用。**这是工程取值，不是测量结论**——
真实阈值应按 `interface-contract.md` §4 所述经性能测试确定（决策项A2）。

**2. 来源信息无来源时为空列表，绝不伪造。**
``sources`` 默认为空数组而非 None，前端无需处理两种形态。

**3. 错误码用字符串枚举而非 HTTP 状态码。**
同一后端错误在同步与流式接口下HTTP 状态码不同
（SSE 响应头发出后无法再改，见 :mod:`app.api.streaming`），
错误码才是跨传输稳定的标识。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 问题文本最大字符数。见模块 docstring 的决定 1。
MAX_QUESTION_CHARS = 4096

#: 单次返回的检索片段数上限。与
#: :data:`app.agent.knowledge_tools.MAX_TOP_K` 保持一致口径。
MAX_SOURCE_ITEMS = 8


class RequestStatus(str, Enum):
    """请求状态。

    依据 ``interface-contract.md`` §3「使用稳定枚举并定义状态迁移」。

    迁移规则（当前为单轮问答，故只有终态）::

        processing ──▶ completed
                   ├─▶ partial   （文本成功，但可选能力不可用）
                   └─▶ failed

    保留 ``processing`` 是为将来接入任务轮询（A3）预留——
    届时新增 ``submitted`` 状态并在此定义迁移。
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SourceLocator(str, Enum):
    """来源的定位类型。

    ``interface-contract.md` §3 要求 sources 含"source ID、标题、
    版本和定位"。定位的具体形式随来源而异，故用枚举而非自由文本——
    前端可据此决定渲染方式（如页码 vs 章节号）。
    """

    SECTION = "section"
    PAGE = "page"
    CHAPTER = "chapter"
    UNKNOWN = "unknown"


class SourceItem(BaseModel):
    """单条知识来源。

    字段与 ``knowledge-base-implementation.md`` 的
    :class:`~app.knowledge.corpus.KnowledgeSource` 一一对应，
    是其在 API 契约中的投影。
    """

    model_config = ConfigDict(extra="forbid")

    #: 来源标识（语料中的 source_id），供追溯与去重。
    source_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    #: 定位信息，如「第三章 烃」「p.42」。UNKNOWN 时可为空串。
    locator: str = Field(default="", max_length=256)
    locator_kind: SourceLocator = SourceLocator.UNKNOWN
    #: 文本版本标识。自编讲义为 "project-authored"。
    version: str = Field(default="", max_length=128)
    #: 审核状态。**未审核内容必须如实标注**（`security-privacy.md` §4）。
    review_status: str = Field(default="unknown", max_length=64)


class ToolInvocation(BaseModel):
    """一次工具调用的结果记录。

    刻意只暴露**状态**而非输入输出全文：工具输入可能很长，
    且部分输出含检索原文。是否检索到由sources 表达，
    不在此重复（`architecture.md` §6 要求记录工具结果状态）。
    """

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    ok: bool
    error_code: str | None = Field(default=None, max_length=64)


class VisualizationHint(BaseModel):
    """可选的可视化提示。

    当前只承载化学结构数据。**不接受可执行代码**
    （`security-privacy.md` §4：前端只渲染经schema 校验的数据）。

    字段刻意保持"已验证的结构数据"形态：原子坐标 + 键连接，
    而非 SMILES 字符串或脚本——前端不需要也不应解释脚本。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    #: 结构化数据。具体 schema 随 kind 变化，故用 dict。
    data: dict[str, Any] = Field(default_factory=dict)
    #: 数据来源：工具计算 or 模型推断。二者可信度不同，须区分。
    source: str = Field(default="tool_verified", pattern=r"^[a-z_]{1,32}$")


class AnswerResponse(BaseModel):
    """问答接口的响应体。

    对应 ``interface-contract.md` §3 的全部答复语义字段。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    status: RequestStatus
    #: 面向学生的机理解释。失败时为空串（错误信息在 error 中）。
    explanation: str = ""
    #: 知识来源。**无来源时为空列表，不伪造**（§3 硬约束）。
    sources: list[SourceItem] = Field(default_factory=list)
    #: 可选的化学结构/计算结果。
    visualization: list[VisualizationHint] = Field(default_factory=list)
    #: 工具调用状态记录，供诊断与「依据从哪来」的展示。
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    #: Agent 迭代轮数。1 表示模型未调用工具直接作答。
    steps: int = Field(default=1, ge=1, le=32)
    #: 处理耗时（秒），供前端展示与性能监控。
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    error: dict[str, Any] | None = None
    #: 响应契约版本。前端据此判断字段兼容性（§6）。
    schema_version: str = "1.0"


class AskRequest(BaseModel):
    """问答请求体。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="学生提出的问题，仅 UTF-8 文本。",
    )

    @field_validator("question")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """拒绝纯空白问题。

        ``min_length=1`` 拦得住空串，拦得住 ``"\\n"`` 吗？拦不住——
        而一个只含换行的问题会浪费一次模型调用。
        """
        if not value.strip():
            raise ValueError("问题不能为空或仅含空白字符")
        return value


class MoleculeRequest(BaseModel):
    """化学结构解析请求。

    独立于问答接口，因为它是**确定性**的：RDKit 解析不依赖模型，
    延迟在毫秒级，前端可高频调用（如输入框实时预览）。
    """

    model_config = ConfigDict(extra="forbid")

    smiles: str = Field(
        min_length=1,
        max_length=512,
        description="SMILES 结构式。",
    )

    @field_validator("smiles")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("SMILES 不能为空或仅含空白字符")
        return value


class MoleculeResponse(BaseModel):
    """化学结构解析响应。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    ok: bool
    #: RDKit 计算的客观性质（分子式、分子量、原子/键/环数等）。
    #: **与 structure 分开**——结构用于渲染，性质用于展示，
    #: 前端不应从结构反推性质（`architecture.md` §5：
    #: 通过解析不等于性质正确）。
    properties: dict[str, Any] | None = None
    #: 结构化渲染数据：规范化 SMILES + viz_data。
    #: viz_data 内``conformer`` 当前为 ``"none"``（实测：本层不做
    #: 三维构象生成），前端需自行构象或降级为二维展示。
    structure: dict[str, Any] | None = None
    #: 受支持的功能基团命中。
    functional_groups: list[dict[str, Any]] = Field(default_factory=list)
    #: 解析层级（语法/合法性/性质/基团），供前端区分能信到什么程度。
    verification: str = ""
    #: 结构可解析但超出支持范围时的说明（如"未生成三维坐标"）。
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    schema_version: str = "1.0"


class ComponentStatus(BaseModel):
    """单个依赖组件的状态。

    用于健康检查与诊断。**只暴露状态，不暴露配置内容**
    （``security-privacy.md`` §3：不得记录密钥）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    ready: bool
    #: 可公开的诊断说明，如「未配置 MAAS_API_KEY」。
    detail: str = ""
    #: 子能力开关，供前端**结构化**判断而不必解析 detail 字符串。
    #:
    #: **为什么需要**（实测踩到）：speech 组件的 detail 形如
    #: ``"tts=就绪 fay=未启用"``，前端要判断"数字人是否可用"
    #: 就得 `detail.includes('fay=就绪')` —— 把展示文案变成了契约。
    #: 文案一改（如"就绪"→"可用"）前端就静默失效。
    #:
    #: **只用 bool，不放配置内容**（`security-privacy.md` §3：
    #: 不得暴露密钥与配置值）。
    caps: dict[str, bool] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """健康检查响应。

    区分 ``ready``（可服务）与组件明细：容器编排需要前者判断
    是否接流量，人需要后者排障。
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="ok / degraded / not_ready")
    ready: bool
    components: list[ComponentStatus] = Field(default_factory=list)
    schema_version: str = "1.0"


def utc_now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串。

    带时区后缀（``+00:00``）而非裸字符串——前端 ``new Date()``
    解析无时区的ISO 串会按本地时间处理，导致时间显示错误。
    """
    return datetime.now(tz=None).astimezone().isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# 语音（决策 H21）
# ----------------------------------------------------------------------


class SpeechRequest(BaseModel):
    """语音合成请求。

    ## 为什么不复用 ``AskRequest``

    语音是**独立能力**：同一段讲解文本可能被合成多次
    （学生点"再读一遍"），也可能在问答之外单独触发
    （如只朗读某个结构式）。绑在问答链路上会让
    "重播"变成"再问一次模型"——白花钱且慢7 秒。
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    #: 是否同时推送给数字人。**默认 False**——
    #: 学生主动点"朗读"时不该惊动数字人形象。
    push_digital_human: bool = False
    #: Fay 侧会话标识。多学生共用默认值会互相打断
    #: （Fay 非队列模式会清空该用户前序音频队列）。
    user: str = Field(default="User", min_length=1, max_length=64)


class SpeechResponse(BaseModel):
    """语音合成响应。

    ## 为什么两阶段（决策 H21）

    合成结果**不直接内联**，而是返回 ``audio_id``，
    由前端另请求 :http:get:`/api/v1/speak/{audio_id}` 取音频。

    实测依据：base64 内联体积 **+33%** 且浏览器**无法单独缓存**
    （数据在 JSON 里，不能 range 请求）。独立端点可缓存、可拖动进度。
    另有一个关键好处：**无状态**——不依赖进程内存或本地临时目录，
    因此进程重启不影响契约（见 :class:`~app.speech.store.StoredAudio`）。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str
    #: 语音状态：``ready`` / ``disabled`` / ``not_configured`` / ``unavailable``。
    #: **四态而非布尔**——前端要区分"用户主动关闭"（正常选择，
    #: 不该显示错误样式）与"服务故障"（该提示）。
    stage: str
    #: 是否可播放。前端据此决定是否渲染播放按钮。
    available: bool
    #: 音频 id。**仅 ``available=True`` 时有值**。
    audio_id: str | None = None
    #: 音频 URL，由服务端给出。**前端不拼路径**——
    #: 契约变了前端无须改（实测过 base64 方案下前端要自己处理 data URI）。
    audio_url: str | None = None
    #: 面向学生的简短说明，可直接展示。
    reason: str = ""
    #: 文本是否被截断。学生应知道"还有内容"。
    truncated: bool = False
    #: 实际送去合成的字符数。
    char_count: int = 0
    #: 数字人是否已接收播报内容。
    digital_human_delivered: bool = False


__all__ = [
    "MAX_QUESTION_CHARS",
    "MAX_SOURCE_ITEMS",
    "AnswerResponse",
    "AskRequest",
    "ComponentStatus",
    "HealthResponse",
    "MoleculeRequest",
    "MoleculeResponse",
    "RequestStatus",
    "SourceItem",
    "SourceLocator",
    "SpeechRequest",
    "SpeechResponse",
    "ToolInvocation",
    "VisualizationHint",
    "utc_now_iso",
]
