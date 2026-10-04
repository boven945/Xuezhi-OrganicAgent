"""化学工具模块。

职责边界见 `docs/architecture.md`：负责 SMILES 解析、结构合法性检查、
受支持的分子属性计算与结构数据输出。

**明确不做的事**（见 `docs/product-scope.md` §5、`docs/architecture.md` §5）：
- 不声称完成了严格的反应机理证明
- 不对超出高中课程范围的结论背书
- 不把 SMILES 解析成功表述为"已校验"

本模块不做网络调用、不读取密钥、不依赖 Agent，可独立测试。

## 为什么 ``engine`` 用模块级 ``__getattr__`` 延迟导入

``engine`` 在顶层``from rdkit import Chem``，而**RDKit 的 C++ 扩展
可能被 Windows 应用控制策略（WDAC/Smart App Control）按签名拦截**。

若在此处直接 ``from .engine import ...``，则**任何** ``import app.chem.errors``
都会连带触发 rdkit 加载——包括只是想用错误码的上层模块
（如 :mod:`app.api.errors`，它要透传 ``chem_*`` 错误码）。
后果是：RDKit 不可用时，**整个 API 网关都起不来**，
连``/health`` 都无法回答"化学组件不可用"这件事。

改用 :pep:`562` 的模块级 ``__getattr__`：``from app.chem import ChemEngine``
与 ``app.chem.get_engine()`` 的用法**完全不变**，
但只有真正访问这两个名字时才导入 engine。

实测（2026-10-04，本机WDAC 拦截 rdchem）：
- 修复前：``import app.api.app`` →ImportError
- 修复后：``/health`` 正常返回 ``chem: ready=False``，其余组件可用
"""

from typing import TYPE_CHECKING, Any

from .errors import (
    ChemError,
    InvalidStructureError,
    MoleculeTooLargeError,
    UnsupportedStructureError,
)
from .models import (
    FunctionalGroupHit,
    MoleculeProperties,
    MoleculeStructure,
    ParseResult,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from .engine import ChemEngine

#: 延迟导出的名字。值是 ``(模块内路径, 属性名)``。
_LAZY: dict[str, str] = {
    "ChemEngine": "engine",
    "get_engine": "engine",
}


def __getattr__(name: str) -> Any:
    """按需导入 engine（:pep:`562`）。

    Raises:
        AttributeError: 名字不是本包公开的惰性导出项。
        ImportError: RDKit 不可用（如被应用控制策略拦截）——
            **原样抛出**让调用方看到真实原因，
            不包装成 AttributeError（那会被 ``hasattr`` 误判为"不存在"）。
    """
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    # 缓存到模块字典，后续访问不再走 __getattr__
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY])


__all__ = [
    "ChemError",
    "InvalidStructureError",
    "UnsupportedStructureError",
    "MoleculeTooLargeError",
    "MoleculeProperties",
    "MoleculeStructure",
    "FunctionalGroupHit",
    "ParseResult",
    "ChemEngine",
    "get_engine",
]
