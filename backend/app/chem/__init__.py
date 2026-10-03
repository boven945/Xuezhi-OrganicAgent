"""化学工具模块。

职责边界见 `docs/architecture.md`：负责 SMILES 解析、结构合法性检查、
受支持的分子属性计算与结构数据输出。

**明确不做的事**（见 `docs/product-scope.md` §5、`docs/architecture.md` §5）：
- 不声称完成了严格的反应机理证明
- 不对超出高中课程范围的结论背书
- 不把 SMILES 解析成功表述为"已校验"

本模块不做网络调用、不读取密钥、不依赖 Agent，可独立测试。
"""

from .errors import (
    ChemError,
    MoleculeTooLargeError,
    UnsupportedStructureError,
    InvalidStructureError,
)
from .models import (
    FunctionalGroupHit,
    MoleculeProperties,
    MoleculeStructure,
    ParseResult,
)
from .engine import ChemEngine, get_engine

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
