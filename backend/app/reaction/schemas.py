"""反应机理可视化数据模型。

两个 schema 与 ``knowledge-corpus/v1``、``quiz-bank/v1`` 同级治理：

- ``reaction-library/v1``：静态、教师编审的反应定义（存于 ``data/reactions/``）。
- ``reaction-mechanism/v1``：接口输出，逐帧的原子坐标 + 键状态 + 高亮。

设计要点（对应 ``docs/reaction-mechanism-viz-plan.md`` D-R2~D-R4 与 2026-10-20 的
schema 细化决策）：

- **原子映射显式存于 SMILES 的 ``:n`` 映射号**（RDKit ``GetAtomMapNum`` 读取），
  满足 D-R4（显式映射），且比手写位置索引更不易错位（review 可直接读 SMILES）。
- **断键/成键由"反应物 + 产物结构 + 映射号"推导**，不靠手写位置表；
  数据里仍保留 ``breaking_bonds`` / ``forming_bonds`` 作为**可校验标注**，
  容器内 ``scripts/validate_reactions.py`` 会与推导结果比对，不一致即报错（防手误）。
- 坐标单位 Å，与 ``molecule-structure/v2`` 一致（接口契约 §3.1）。
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BondState(str, Enum):
    """键在反应过程中的状态（由帧生成器按键级推导，非手填）。

    语义基于**键的存在性**，不是键级：

    - ``INTACT``：反应物与产物中都存在（键级可不变，也可降低/升高）。
      例如 C=C→C–C（双键退化为单键）：σ 键保留、键级 2→1，归为 INTACT，
      其 π 组分的"消失"由 ``order`` 线性插值表现，端点进入 ``highlight_atoms``。
    - ``BREAKING``：反应物中存在、产物中**完全消失**（键级 1→0）。opacity 1→0。
    - ``FORMING``：产物中存在、反应物中没有（键级 0→1）。opacity 0→1。

    因此数据标注 ``breaking_bonds`` / ``forming_bonds`` 也只列举"存在性变化"
    的键；双键退化为单键这类"键级变化但键仍存在"的情况**不要**列进来。
    """  # noqa: E501

    INTACT = "intact"
    BREAKING = "breaking"
    FORMING = "forming"


class ReactionDef(BaseModel):
    """单条反应定义（``reaction-library/v1`` 的一条）。

    仅做**结构**校验。化学正确性（断键/成键位点是否与教材一致）
    属教研审核范畴，不在此强制——``status`` 由 ``draft`` 经教师审核升 ``approved``。
    """

    id: str = Field(..., min_length=1)
    name: str
    topic: str = ""
    grade: str = "高中"
    #: 含 ``:n`` 原子映射号的 SMILES（权威化学来源，RDKit 直接解析）。
    reactant_smiles: str
    product_smiles: str
    #: 断键的映射号对（反应物中存在、产物中**完全消失**的键的端点），
    #: 作为可校验标注。注意：双键退化为单键（C=C→C–C）属于"键级变化但键仍存在"，
    #: 不在此列——它由帧生成器的 order 插值表现，并进入 highlight_atoms。
    breaking_bonds: list[tuple[int, int]] = Field(default_factory=list)
    #: 成键的映射号对（产物中生成、反应物中没有的键的端点），作为可校验标注。
    forming_bonds: list[tuple[int, int]] = Field(default_factory=list)
    #: 供 Agent 工具 ``find_reaction_mechanism`` 做关键词匹配的辅助词。
    #: **仅用于路由**，不参与化学正确性判定；数据里可列别称/俗名/主题词。
    keywords: list[str] = Field(default_factory=list)
    transition_note: str = ""
    highlight_note: str = ""
    related_chunk_ids: list[str] = Field(default_factory=list)
    status: Literal["draft", "approved"] = "draft"
    source: str = "textbook-clamped"

    @model_validator(mode="after")
    def _check_smiles_nonempty(self) -> "ReactionDef":
        if not self.reactant_smiles.strip() or not self.product_smiles.strip():
            raise ValueError(f"反应 {self.id!r} 的反应物/产物 SMILES 不能为空")
        return self


class ReactionLibraryFile(BaseModel):
    """反应库文件顶层结构（与 ``knowledge-corpus/v1`` 同级治理）。"""

    model_config = ConfigDict(populate_by_name=True)

    # JSON 键为 ``schema``；字段名用 ``lib_schema`` 以免遮蔽 BaseModel 属性。
    lib_schema: str = Field(default="reaction-library/v1", alias="schema")
    index_version: str = "unversioned"
    reactions: list[ReactionDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "ReactionLibraryFile":
        seen: set[str] = set()
        for r in self.reactions:
            if r.id in seen:
                raise ValueError(f"反应库存在重复 id：{r.id!r}")
            seen.add(r.id)
        return self


class AtomPos(BaseModel):
    """单帧中的单个原子（坐标单位 Å）。"""

    index: int
    element: str
    x: float
    y: float
    z: float


class BondFrame(BaseModel):
    """单帧中的单根键（含当前帧的键级与透明度）。"""

    a: int
    b: int
    order: float  # 当前帧键级；断裂中可低于产物级（如 2→1）
    state: BondState
    opacity: float = 1.0  # 0..1，前端驱动 material.opacity / visible


class MechanismFrame(BaseModel):
    """单帧：原子坐标 + 键状态 + 高亮原子。"""

    t: float
    atoms: list[AtomPos]
    bonds: list[BondFrame]
    highlight_atoms: list[int] = Field(default_factory=list)


class ReactionMechanismFile(BaseModel):
    """接口输出：逐帧动画数据（``reaction-mechanism/v1``）。"""

    model_config = ConfigDict(populate_by_name=True)

    mech_schema: str = Field(default="reaction-mechanism/v1", alias="schema")
    reaction_id: str
    #: 反应名称（取自反应库，便于前端直接展示）。
    name: str = ""
    #: 过渡讲解（教材口径的断键/成键叙述），由反应库提供、随动画展示。
    transition_note: str = ""
    #: 高亮提示（提示学生关注哪些键/原子）。
    highlight_note: str = ""
    frame_count: int = Field(..., ge=2)
    fps: int = Field(default=12, ge=1, le=60)
    #: 反应物原子 index → 产物原子 index（生成器以映射号为 index，故通常恒等）。
    atom_map: dict[int, int] = Field(default_factory=dict)
    frames: list[MechanismFrame] = Field(default_factory=list)


__all__ = [
    "AtomPos",
    "BondFrame",
    "BondState",
    "MechanismFrame",
    "ReactionDef",
    "ReactionLibraryFile",
    "ReactionMechanismFile",
]
