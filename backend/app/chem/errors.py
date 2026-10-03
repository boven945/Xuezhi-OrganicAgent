"""化学模块的受控错误类型。

`docs/interface-contract.md` §5 要求错误具备稳定、可机器读的分类，
且不得泄露堆栈或密钥。这里的异常只携带面向用户的简短说明与
稳定错误码，由上层 API 层负责转换为响应。

错误码命名规则：`<域>_<原因>`，全小写，与 `docs/interface-contract.md`
§5 列出的分类对应。
"""

from __future__ import annotations


class ChemError(Exception):
    """化学模块所有受控错误的基类。

    属性:
        code: 稳定错误码，用于前端与日志分类。
        user_message: 面向学生的简短说明，不含内部细节。
        detail: 仅用于服务端日志的补充信息，不得返回给客户端。
    """

    code = "chem_internal_error"

    def __init__(
        self,
        user_message: str,
        *,
        detail: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
        if code is not None:
            self.code = code

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试
        return f"{type(self).__name__}(code={self.code!r}, message={self.user_message!r})"


class InvalidStructureError(ChemError):
    """SMILES 语法错误、结构非法或价态不合理。

    对应 `docs/interface-contract.md` §5 的"化学结构无效"分类。
    """

    code = "chem_invalid_structure"


class UnsupportedStructureError(ChemError):
    """结构可解析，但超出本工具支持范围（元素、规模或特征）。

    与 `InvalidStructureError` 区分：前者是"结构本身不对"，
    后者是"结构没错但本工具不处理"，前端应给予不同提示。
    """

    code = "chem_unsupported_structure"


class MoleculeTooLargeError(UnsupportedStructureError):
    """结构规模超过安全上限，已在解析前拒绝。

    `docs/security-privacy.md` §4 要求对 RDKit 输入限制大小与
    复杂度，防止资源消耗攻击。
    """

    code = "chem_structure_too_large"


__all__ = [
    "ChemError",
    "InvalidStructureError",
    "UnsupportedStructureError",
    "MoleculeTooLargeError",
]
