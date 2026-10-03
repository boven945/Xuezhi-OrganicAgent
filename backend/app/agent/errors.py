"""Agent 层的受控错误类型。

`docs/interface-contract.md` §5 要求错误可机器读、不泄露堆栈或密钥。
`architecture.md` §5 要求工具失败以结构化状态返回，不静默降级。
"""

from __future__ import annotations


class AgentError(Exception):
    """Agent 层受控错误基类。

    Attributes:
        code: 稳定错误码。
        user_message: 面向用户的说明，不含内部细节。
        retryable: 是否值得重试。
    """

    code = "agent_internal_error"
    retryable = False

    def __init__(
        self,
        user_message: str,
        *,
        detail: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class ToolError(AgentError):
    """工具调用相关错误的基类。

    ``architecture.md` §5 要求工具失败可区分处理，因此细分为
    未找到、参数非法、执行失败、超时四类。
    """

    code = "tool_error"


class ToolNotFoundError(ToolError):
    """模型请求了未注册的工具。

    这是**白名单机制生效的体现**（`architecture.md` §5）：
    模型只能调用调度层显式注册的工具。

    注意：这不是"工具不存在"的普通错误，而是**安全边界被触碰**，
    因此默认不可重试。
    """

    code = "tool_not_found"

    def __init__(self, tool_name: str, available: tuple[str, ...] = ()) -> None:
        names = "、".join(available) if available else "（无可用工具）"
        super().__init__(
            f"未找到可用工具。当前可用工具：{names}。",
            detail=f"requested={tool_name!r}",
        )
        self.tool_name = tool_name


class ToolArgumentError(ToolError):
    """工具参数不合法（缺必填项、类型不对、含额外字段等）。

    对应 `interface-contract.md` §4：SMILES、工具参数不能因出现在
    自然语言中而被默认信任，必须走各自 schema 校验。
    """

    code = "tool_argument_invalid"


class ToolExecutionError(ToolError):
    """工具执行失败。

    ``architecture.md` §5：工具失败要以结构化状态返回，
    **不得静默替换为模型臆造结果**。
    """

    code = "tool_execution_failed"


class ToolTimeoutError(ToolError):
    """工具执行超时。

    ``architecture.md` §6 要求各组件具备独立超时与失败路径。
    """

    code = "tool_timeout"
    retryable = True


class AgentStepLimitError(AgentError):
    """Agent 达到最大迭代轮数仍未给出最终答复。

    属于**受控中止**而非崩溃：防止模型与工具陷入循环，
    对应 `architecture.md` §6 的可靠性要求。
    """

    code = "agent_step_limit_reached"


__all__ = [
    "AgentError",
    "ToolError",
    "ToolNotFoundError",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "AgentStepLimitError",
]
