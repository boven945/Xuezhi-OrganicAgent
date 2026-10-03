"""化学模块的数据契约。

设计约束（`docs/interface-contract.md` §3）：

- `chemistry` 字段必须**区分解析、规则校验和模型推断结果**，
  因此本模块所有输出都带 `verification` 标记，说明该结果由哪一层产生。
- 可视化数据必须是**结构化、经版本化 schema** 的数据，不含可执行代码。
- 无来源时字段为空，不伪造。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verification(str, Enum):
    """结果来源标记。

    `docs/architecture.md` §5 明确：模型提出的工具调用必须受白名单与
    schema 约束，且 SMILES 解析成功不等于反应机理正确。这里用枚举把
    "工具验过" 与 "只是模型说的" 在类型层面隔开。
    """

    #: 由 RDKit 解析并完成合法性检查，未做机理推断
    TOOL_VERIFIED = "tool_verified"
    #: 结构可解析，但本工具未覆盖相应特征
    PARSE_ONLY = "parse_only"
    #: 不属于本工具产出，调用方不应从本模块获得该值
    MODEL_INFERRED = "model_inferred"


@dataclass(frozen=True, slots=True)
class FunctionalGroupHit:
    """一个官能团的命中记录。

    Attributes:
        name: 官能团中文名，面向学生展示。
        smiles: 用于判定该官能团的子结构 SMARTS。
        matched: 是否在分子中命中。
        atom_indices: 命中的原子索引，便于前端在结构上高亮。
    """

    name: str
    smiles: str
    matched: bool
    atom_indices: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的结构化数据。"""
        return {
            "name": self.name,
            "smarts": self.smiles,
            "matched": self.matched,
            "atom_indices": list(self.atom_indices),
        }


@dataclass(frozen=True, slots=True)
class MoleculeProperties:
    """分子属性计算结果。

    仅包含 RDKit 可靠计算的性质，均为客观数值，不含化学结论判断。
    """

    molecular_formula: str
    molecular_weight: float
    #: 显式原子数，**不含隐式氢**（RDKit ``GetNumAtoms()`` 的语义）。
    #: 例：乙醇 CCO 的该值为 3，而分子式为 C2H6O。展示时须避免误解。
    num_atoms: int
    #: 显式键数，同样不含隐式氢。例：乙醇为 2。
    num_bonds: int
    num_rings: int
    num_aromatic_rings: int
    #: 杂原子数（碳以外的显式原子）。乙醇为 1（氧）。
    num_heteroatoms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "molecular_formula": self.molecular_formula,
            "molecular_weight": round(self.molecular_weight, 4),
            "num_atoms": self.num_atoms,
            "num_bonds": self.num_bonds,
            "num_rings": self.num_rings,
            "num_aromatic_rings": self.num_aromatic_rings,
            "num_heteroatoms": self.num_heteroatoms,
        }


@dataclass(frozen=True, slots=True)
class MoleculeStructure:
    """供前端渲染的结构化数据。

    `docs/security-privacy.md` §4 明确禁止前端直接执行模型返回的
    HTML/JavaScript，因此这里只提供 SMILES 与 molblock 文本，
    由前端用 Three.js / Molstar 自行渲染，不含任何可执行内容。
    """

    canonical_smiles: str
    input_smiles: str
    #: 结构化可视化数据（JSON 键值对），由前端解析渲染
    viz_data: dict[str, Any]
    verification: Verification = Verification.PARSE_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_smiles": self.canonical_smiles,
            "input_smiles": self.input_smiles,
            "viz_data": self.viz_data,
            "verification": self.verification.value,
        }


@dataclass(frozen=True, slots=True)
class ParseResult:
    """一次解析的完整结果。

    统一承载性质、官能团与结构化数据，避免调用方多次调用引擎。
    """

    ok: bool
    properties: MoleculeProperties | None = None
    structure: MoleculeStructure | None = None
    functional_groups: tuple[FunctionalGroupHit, ...] = ()
    #: 结构可解析但超出支持范围时的人类可读说明
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default=())
    verification: Verification = Verification.PARSE_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "properties": self.properties.to_dict() if self.properties else None,
            "structure": self.structure.to_dict() if self.structure else None,
            "functional_groups": [g.to_dict() for g in self.functional_groups],
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "verification": self.verification.value,
        }


__all__ = [
    "Verification",
    "FunctionalGroupHit",
    "MoleculeProperties",
    "MoleculeStructure",
    "ParseResult",
]
