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
    "api_rate_limited": "请求过于频繁，请稍后再试。",
    "api_internal_error": "服务内部错误，请稍后重试。",
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
    # Agent / 工具
    "tool_timeout": ApiErrorSpec("tool_timeout", 504, True),
    "tool_execution_failed": ApiErrorSpec("tool_execution_failed", 500, True),
    "agent_step_limit_reached": ApiErrorSpec("agent_step_limit_reached", 500, False),
    # 化学：输入问题，归 400
    "chem_invalid_structure": ApiErrorSpec("chem_invalid_structure", 400, False),
    "chem_unsupported_structure": ApiErrorSpec("chem_unsupported_structure", 400, False),
    "chem_structure_too_large": ApiErrorSpec("chem_structure_too_large", 400, False),
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
    # 下层错误码：不给"可能是上游原文"的可信度，一律通用文案。
    # 例：llm_upstream_unavailable / rag_embedding_unavailable
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
    "RETRYABLE_CODES",
    "ApiErrorSpec",
    "build_error_payload",
    "classify",
    "public_message",
]
