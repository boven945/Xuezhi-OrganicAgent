"""API 层的稳定错误语义。

设计依据 `interface-contract.md` §5 与 `architecture.md` §6：
错误响应须包含请求标识、可公开的简短说明、是否可重试，
**且不得泄露堆栈或密钥**。

## 为什么复用既有错误码

``app.agent`` / ``app.llm`` / ``app.rag`` / ``app.chem`` 四个模块
各自已定义稳定的 ``code``（共 20 个，见各自``errors.py``）。
API 层**不另造一套**，而是把下层错误码**原样透传**到响应体的
``error.code``——这样前端与运维只需维护一张错误码表，
且「检索不可用」与「模型不可用」在数据结构上可区分。

这一点是刻意的：``knowledge_tools.py`` 已经花代价把``rag_*``
错误码透传出来（否则会被``tool_execution_failed`` 覆盖），
API 层若再压平就前功尽弃。

## 与 HTTP 状态码的关系

错误码（机器可读、跨模块稳定）与 HTTP 状态码（供通用客户端与
代理判断）**刻意解耦**：同一个 ``llm_upstream_unavailable``
在问答接口应返回 503（依赖不可用、重试有意义），
在流式接口里则以 SSE ``error`` 事件呈现（响应头早已发出，
无法再改状态码——这正是 SSE 的固有约束，见 ``streaming.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.errors import AgentError
from app.chem.errors import ChemError
from app.llm.errors import LLMError
from app.rag.errors import RAGError


#: 下层模块的异常基类。顺序无关紧要，匹配靠isinstance。
_DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    LLMError,
    RAGError,
    ChemError,
    AgentError,
)

#: API 层自身错误码的对外文案。**只用于 ``api_`` 前缀的码**——
#: 这些文案由本项目编写，不含任何上游内容，可安全透出。
API_MESSAGES: dict[str, str] = {
    "api_invalid_input": "输入格式不正确，请检查后重试。",
    "api_question_empty": "问题不能为空，请输入你想问的内容。",
    "api_question_too_long": "问题太长，请精简后重试。",
    "api_invalid_smiles": "化学结构式无法解析，请检查写法。",
    "api_not_ready": "服务尚未就绪，请稍后再试。",
    # 组件不可用：**不告诉学生「服务内部错误」**——那既不真实也无所行动。
    # 措辞要具体到「哪个能力」并说明影响范围，学生才知道还能做什么。
    "api_component_unavailable": "该功能所需的组件当前不可用，其余功能不受影响。",
    "api_rate_limited": "请求过于频繁，请稍后再试。",
    "api_internal_error": "服务内部错误，请稍后重试。",
}


#: 下层错误码的**本层撰写**文案。
#:
#: 为什么需要这张表：``public_message`` 禁用透传 ``exc.user_message``
#: （实测确认那可能含上游原文），若无本表则所有下层错误都落到
#: 同一句「服务暂时不可用」——学生既不知道是配置问题还是上游故障，
#: 也无从行动。**这里不读异常消息，只按已登记的 code 查表，故无泄露风险。**
DOMAIN_MESSAGES: dict[str, str] = {
    # llm（码取自 app/llm/errors.py 的实际定义，勿凭印象增删）
    "llm_not_configured": "模型服务尚未配置，请设置 MAAS_API_KEY 后重试。",
    "llm_upstream_unavailable": "模型服务暂时不可用，请稍后重试。",
    "llm_timeout": "模型响应超时，请稍后重试。",
    "llm_rate_limited": "模型服务当前繁忙，请稍后重试。",
    # rag
    "rag_embedding_unavailable": "知识库的向量模型不可用，检索功能暂不可用。",
    "rag_index_not_ready": "知识库尚未就绪，请稍后重试。",
    "rag_retrieval_failed": "知识库检索失败，答复可能不完整。",
    "rag_invalid_document": "知识库中存在不合规的文档，请联系维护者。",
    # agent / 工具
    "tool_timeout": "工具调用超时，请稍后重试。",
    "agent_step_limit_reached": "推理轮数已达上限，请把问题拆得更具体些。",
    # agent / 工具的其余四类（`app/agent/errors.py` 按「未找到 / 参数非法 /
    # 执行失败 / 超时」四类细分，`architecture.md` §5 要求可区分处理）。
    #
    # `tool_not_found` **不是**普通故障：它是白名单机制生效的体现
    #（模型请求了未注册的工具 = 安全边界被触碰），默认不可重试。
    # 故文案不写「暂时不可用」——那会诱导学生反复重试，
    # 而正确动作是把问题改回知识问答范围。
    "tool_not_found": "当前问题超出了工具能处理的范围，请换种方式提问。",
    "tool_argument_invalid": "工具收到的参数不合法，请检查输入内容。",
    "tool_execution_failed": "分析工具执行失败，答复可能不完整。",
    # ``ToolError`` 的基类码。四个子类都各自覆写了 ``code``，
    # 正常路径不该见到它；见到说明抛的是未细分的 ``ToolError``。
    # 给出文案而非留空，是为了不让它掉进「服务暂时不可用」——
    # 那会让学生以为重试有用，而实际该做的是换个问法。
    "tool_error": "工具调用出错，请稍后重试。",
    # 三个 *_internal_error 是各域的**基类兜底码**。
    # 正常路径不该出现；出现了说明有未预料的缺陷。
    # 文案不暴露具体域，避免学生去猜是哪个环节坏了。
    "agent_internal_error": "推理过程出错了，请稍后重试。",
    "llm_internal_error": "模型服务出错了，请稍后重试。",
    "rag_internal_error": "知识库检索出错了，请稍后重试。",
    # chem（码取自 app/chem/errors.py 的实际定义，勿凭印象增删）
    #
    # **实测发现的缺陷（2026-10-04，容器内回归）**：本表原先**完全没有**
    # chem 段，导致学生输入非法结构式（实测 `C1CC`）时收到的是
    # 「服务暂时不可用，请稍后重试」——但服务其实好得很，
    # 是他少写了一个右括号。**误导性文案比报错更糟**：
    # 它暗示「重试就能好」，学生会反复重试而不是检查自己的输入。
    #
    # 三个码必须分开，不能合并（`docs/product-scope.md` §5 强调
    # 「结构非法」与「超出支持范围」是不同性质的问题）：
    # - 非法：学生输入有错，提示去改
    # - 超范围：结构没错，只是本工具不处理，提示换结构
    # - 太大：在**解析前**就拒了（security-privacy §4 的资源防护），
    #   与「解析后发现不支持」不是同一回事
    "chem_invalid_structure": "结构式无法识别，请检查括号是否配对、元素符号是否正确。",
    "chem_unsupported_structure": "该结构超出本工具的分析范围，请换用更简单的结构。",
    "chem_structure_too_large": "结构式过于复杂，本工具只处理较小的分子。",
    # 基类兜底码。列出来是为了让「未预期的 chem 错误」
    # 也有明确出口，而不是掉进通用兜底文案。
    "chem_internal_error": "化学分析出错了，请稍后重试。",
}


#: 可重试的错误码后缀（精确匹配下层已定义的 code）。
#:
#: 依据 `deployment-operations.md` §8「有上限的退避策略」：
#: 只有瞬时故障才允许重试，输入错误与能力边界错误重试无意义。
#: **刻意用精确集合而非前缀匹配**——``llm_rate_limited`` 与
#: ``llm_timeout`` 需重试，而 ``llm_not_configured`` 重试一万次
#: 也不会自己好起来。
RETRYABLE_CODES: frozenset[str] = frozenset(
    {
        "llm_upstream_unavailable",
        "llm_timeout",
        "llm_rate_limited",
        "rag_embedding_unavailable",
        "rag_index_not_ready",
        "rag_retrieval_failed",
        "tool_timeout",
        "tool_execution_failed",
        "internal_error",
    }
)


@dataclass(frozen=True, slots=True)
class ApiErrorSpec:
    """一个错误码的对外契约。

    Attributes:
        code: 机器可读错误码，跨版本稳定。
        http_status: 同步接口的 HTTP 状态码。
        retryable: 客户端是否应退避后重试。
    """

    code: str
    http_status: int
    retryable: bool


#: API 层自身产生的错误码。
#:
#: 与下层模块的 code **不重名**（前缀 ``api_``），避免冲突。
API_ERROR_SPECS: dict[str, ApiErrorSpec] = {
    # 输入问题：客户端改输入即可，重试无意义。
    "api_invalid_input": ApiErrorSpec("api_invalid_input", 400, False),
    "api_question_empty": ApiErrorSpec("api_question_empty", 400, False),
    "api_question_too_long": ApiErrorSpec("api_question_too_long", 400, False),
    "api_invalid_smiles": ApiErrorSpec("api_invalid_smiles", 400, False),
    # 服务端未配置好：重试无意义，须人工介入。
    "api_not_ready": ApiErrorSpec("api_not_ready", 503, False),
    # **某个可选组件不可用**（如 RDKit 被系统策略拦截）。
    #
    # 为什么不用 api_not_ready：那是「整个服务没配置完」，
    # 而这里是「服务正常，只是少一个可选能力」——
    # 教学场景下化学引擎挂掉，问答链路仍完整可用。
    # 混为一谈会让编排系统重启一个其实能服务的进程。
    #
    # 状态码取 503 而非 500：这是「暂时不可用」的语义，
    # 组件恢复后无需改代码即自动可用。
    "api_component_unavailable": ApiErrorSpec("api_component_unavailable", 503, False),
    # 限流：明确可重试。
    "api_rate_limited": ApiErrorSpec("api_rate_limited", 429, True),
    # 兜底：不得把内部细节暴露出去。
    "api_internal_error": ApiErrorSpec("api_internal_error", 500, False),
}


#: 下层错误码 → HTTP 状态码与可重试性的显式映射。
#:
#: **为什么必须显式登记**：`_spec_for_domain_code` 的保守默认是
#: 500 + 不可重试。实测确认那个默认对三个关键码是错的——
#: 「服务未就绪」返回 500 会让编排系统当成进程内部错误而反复重启，
#: 而真正需要重启的只有未配置这一种情况。
#:
#: 登记原则：
#: - **503**：服务暂时不可用但正确配置着，重试或等待有意义；
#: - **504**：上游超时，客户端可安全重试；
#: - **429**：上游限流，客户端应退避（注意与本层api_rate_limited 区分，
#:   那是"你请求太频繁"，这是"模型服务限流我们"）；
#: - **500**：其余（真内部错误，或尚未登记的新错误码）。
DOMAIN_CODE_SPECS: dict[str, ApiErrorSpec] = {
    # 模型服务
    "llm_not_configured": ApiErrorSpec("llm_not_configured", 503, False),
    "llm_upstream_unavailable": ApiErrorSpec("llm_upstream_unavailable", 503, True),
    "llm_timeout": ApiErrorSpec("llm_timeout", 504, True),
    "llm_rate_limited": ApiErrorSpec("llm_rate_limited", 429, True),
    # 知识检索：嵌入模型不可用/索引未就绪属服务侧问题
    "rag_embedding_unavailable": ApiErrorSpec("rag_embedding_unavailable", 503, True),
    "rag_index_not_ready": ApiErrorSpec("rag_index_not_ready", 503, True),
    "rag_retrieval_failed": ApiErrorSpec("rag_retrieval_failed", 500, True),
    # 语料片段不合准入要求（`rag/errors.py`：无来源/授权不明/超纲）。
    # **归500 而非 4xx**：这是**入库前**拦截的**服务端数据问题**，
    # 客户端改输入也修不好，重试只会再撞一次同一份坏数据。
    # 归4xx 会误导前端以为是用户的请求有问题。
    "rag_invalid_document": ApiErrorSpec("rag_invalid_document", 500, False),
    # Agent / 工具
    #
    # 状态码按「客户端能不能改」区分，而非一律500：
    # - `tool_argument_invalid` 是**调用方**的请求不合法 → 400，
    #   与 chem 的输入问题同类。学生改输入就能好。
    # - `tool_not_found` 是模型请求了白名单外的工具
    #   （安全边界被触碰，见 agent/errors.py）→ 403，
    #   语义是「服务端不会执行」，客户端重试无意义。
    # - `tool_error` / `*_internal_error` 是未细分或未预料的缺陷 → 500。
    "tool_timeout": ApiErrorSpec("tool_timeout", 504, True),
    "tool_execution_failed": ApiErrorSpec("tool_execution_failed", 500, True),
    "tool_argument_invalid": ApiErrorSpec("tool_argument_invalid", 400, False),
    "tool_not_found": ApiErrorSpec("tool_not_found", 403, False),
    "tool_error": ApiErrorSpec("tool_error", 500, False),
    "agent_step_limit_reached": ApiErrorSpec("agent_step_limit_reached", 500, False),
    "agent_internal_error": ApiErrorSpec("agent_internal_error", 500, False),
    "llm_internal_error": ApiErrorSpec("llm_internal_error", 500, False),
    "rag_internal_error": ApiErrorSpec("rag_internal_error", 500, False),
    # 化学：输入问题，归 400
    "chem_invalid_structure": ApiErrorSpec("chem_invalid_structure", 400, False),
    "chem_unsupported_structure": ApiErrorSpec("chem_unsupported_structure", 400, False),
    "chem_structure_too_large": ApiErrorSpec("chem_structure_too_large", 400, False),
    "chem_internal_error": ApiErrorSpec("chem_internal_error", 500, False),
}


def _spec_for_domain_code(code: str) -> ApiErrorSpec:
    """把下层模块的错误码映射为对外契约。

    未登记的下层错误码走**保守默认**：500 且不可重试。
    宁可让前端不重试，也不要在语义不明时误导客户端打爆上游。
    """
    if code in API_ERROR_SPECS:
        return API_ERROR_SPECS[code]
    if code in DOMAIN_CODE_SPECS:
        return DOMAIN_CODE_SPECS[code]
    return ApiErrorSpec(
        code=code,
        http_status=500,
        retryable=code in RETRYABLE_CODES,
    )


#: 判定「组件不可用」的消息特征（全部转小写后匹配）。
#:
#: **实测来源**：本机 RDKit 被应用控制策略拦截时，
#: `from app.chem import get_engine` 抛出的正是
#: `ImportError("DLL load failed while importing rdchem: ...")`，
#: 且 `__cause__` 为 `None`（实测确认，不是 `OSError`）。
#:
#: 收录多平台表述，因为部署环境不固定（开发机 Windows、
#: 容器 Linux、演示机可能又是别的）：
#: - Windows：`DLL load failed`（加载器措辞）
#: - Linux：`.so: cannot open shared object file` / `wrong ELF class`
#: - macOS：`Library not loaded` / `mach-o, but wrong architecture`
#:
#: **刻意不收`cannot import name`**——实测踩过：
#: 该消息表示「模块存在但符号缺失」，即**代码写错了名字**，
#: 不是组件缺失。收录它会把代码缺陷伪装成环境问题。
#: 宁可漏判成500（开发者看得见），不可误判成503（问题被隐藏）。
_COMPONENT_FAILURE_MARKERS: tuple[str, ...] = (
    "dll load failed",
    "cannot open shared object file",
    "wrong elf class",
    "library not loaded",
    "wrong architecture",
    "no module named",
    "not a win32 application",
)


def _is_component_unavailable(exc: BaseException) -> bool:
    """判断异常是否表示「某个可选组件不可用」。

    识别两类情况：

    1. ``ModuleNotFoundError`` —— 模块确实没装。
    2. ``ImportError`` 但**加载失败**（DLL/``.so`` 被拦、架构不符）。
       这类最容易被漏判——类型是 ImportError，看着像「代码写错了模块名」。

    **为什么不只看类型**：``from app.chem import get_engine`` 写在
    try 块内时，拼错模块名同样抛 ImportError。直接按类型归类会把
    **代码缺陷**也报成「组件不可用」，掩盖真问题。

    判据（保守，宁可漏判不可误判）：

    - 类型是 ``ModuleNotFoundError`` → 一定是组件缺失
    - 类型是 ``ImportError`` 且 ``__cause__`` 是 ``OSError``
      → 底层加载器错误，即组件存在但装不上
    - 类型是 ``ImportError`` 且消息命中已知特征
      → Windows 的 ``DLL load failed`` 属于此类（**实测确认
      此时 ``__cause__`` 为 None**，故必须看消息）
    - 其余 → 判为代码缺陷，走500
    """
    if isinstance(exc, ModuleNotFoundError):
        return True
    if not isinstance(exc, ImportError):
        return False
    cause = exc.__cause__
    if isinstance(cause, OSError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _COMPONENT_FAILURE_MARKERS)


def classify(exc: BaseException) -> ApiErrorSpec:
    """把任意异常归类为对外错误契约。

    Args:
        exc: 捕获到的异常。**可以是任何类型**，包括非项目异常。

    Returns:
        对应的 :class:`ApiErrorSpec`。

    Notes:
        未知异常一律降级为 ``api_internal_error``（500、不可重试），
        **绝不把异常类名或消息透出**——那可能含路径、密钥片段或
        上游响应原文（`security-privacy.md` §3）。
    """
    if isinstance(exc, _DOMAIN_ERRORS):
        return _spec_for_domain_code(exc.code)
    # API 层自身的信号异常（如限流）自带 code，直接按登记表查。
    # 不走此分支的话会被兜底成 500 + "服务内部错误"，
    # 客户端就分不清"你太快了"和"服务端坏了"。
    own_code = getattr(exc, "code", None)
    if isinstance(own_code, str) and own_code in API_ERROR_SPECS:
        return API_ERROR_SPECS[own_code]
    # **组件不可用**：可选依赖缺失或加载失败。
    #
    # 实测踩过：RDKit 的 C++ 扩展被应用控制策略拦截时，
    # `from app.chem import get_engine` 抛 ImportError，
    # 落进兜底变成 500「服务内部错误」——**误导性文案**：
    # 不是服务崩了，是一个可选组件缺失，且问答链路仍可用。
    #
    # **为什么要先看 `__cause__` 再看消息**：代码里的
    # `from app.chem import get_engine` 写在 try 块内时，
    # 任何 ImportError 都会冒到同一个 except——包括
    # 「拼错了模块名」这类真bug。直接按类型归类会把
    # 代码缺陷也报成"组件不可用"，掩盖问题。
    #
    # 判据（保守）：`__cause__` 链上出现 `OSError`（典型的
    # DLL/`.pyd` 加载失败）或消息提到加载失败，才认定是组件问题。
    # 其余 ImportError 一律走 500——**宁可保守，不可掩盖**。
    if _is_component_unavailable(exc):
        return API_ERROR_SPECS["api_component_unavailable"]
    return API_ERROR_SPECS["api_internal_error"]


def public_message(exc: BaseException, spec: ApiErrorSpec) -> str:
    """生成可安全返回给客户端的用户文案。

    只在**文案是本项目自己写的**时才透出。具体判据：
    仅 ``API_ERROR_SPECS`` 中登记的错误码（api_ 前缀）使用其登记文案，
    下层模块错误一律走通用文案。

    为什么这么严——**实测踩过的坑**：``LLMUpstreamError`` 的
    ``user_message`` 可能直接包含上游服务返回的原文，
    实测构造 ``LLMUpstreamError("上游返回: key=sk-abcdef1234")``
    时，该字符串会原样出现在 SSE 响应里。透传"项目自己写的文案"
    这个假设在此不成立：异常消息的来源是上游，不是本项目。

    下层模块自带的 user_message 仍是面向学生的，
    但它们的文案质量依赖各模块作者；API 层作为最后一道闸门，
    宁可给通用文案，也不承担泄露风险。
    """
    # API 层自身登记的错误码：文案由本模块定义，可安全透出。
    if spec.code in API_ERROR_SPECS:
        message = API_MESSAGES.get(spec.code)
        if message:
            return message
    # 下层错误码：用本层登记的针对性文案。
    #
    # **实测踩过的坑（第一版过于收紧）**：曾对所有下层码一律返回
    # 「服务暂时不可用」，结果缺密钥时学生看到的是这句话——
    # 既不知道是配置问题，也无从行动（重启？申请密钥？）。
    # **安全性不受影响**：下表的文案由本模块撰写，
    # 不读取任何异常消息，故无泄露上游原文的风险
    # （泄露风险只来自透传 ``exc.user_message``，那已在上文禁用）。
    message = DOMAIN_MESSAGES.get(spec.code)
    if message:
        return message
    return "服务暂时不可用，请稍后重试。"


def build_error_payload(
    exc: BaseException,
    *,
    request_id: str,
) -> dict[str, Any]:
    """构造错误响应体。

    Args:
        exc: 捕获到的异常。
        request_id: 请求关联标识（回显给客户端便于报障）。

    Returns:
        符合 ``interface-contract.md`` §3 ``error`` 字段语义的字典。
        **不含**堆栈、异常类名、密钥或上游原始响应。

    注意：
        下层异常的 ``detail`` 刻意**不返回**——它是为运维诊断设计的，
        可能含内部路径或上游错误原文。只进日志（``security-privacy.md`` §5）。
    """
    spec = classify(exc)
    return {
        "error": {
            "code": spec.code,
            "message": public_message(exc, spec),
            "retryable": spec.retryable,
            "request_id": request_id,
        }
    }


__all__ = [
    "API_ERROR_SPECS",
    "API_MESSAGES",
    "DOMAIN_MESSAGES",
    "RETRYABLE_CODES",
    "ApiErrorSpec",
    "build_error_payload",
    "classify",
    "public_message",
]
