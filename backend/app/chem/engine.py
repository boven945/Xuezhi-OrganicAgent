"""化学引擎实现。

本模块封装 RDKit，提供 SMILES 解析、合法性检查、属性计算与官能团识别。

安全约束（`docs/security-privacy.md` §4）：
- 输入长度与结构复杂度有上限，超限在解析前拒绝
- 所有 RDKit 异常与警告被捕获并转为受控错误，不向调用方泄露堆栈
- 解析失败时返回明确错误，**不用模型生成结果替代**

能力边界（`docs/architecture.md` §5）：
- 本模块只做"结构是否合法"与"客观属性计算"
- **不判断反应机理是否成立**，SMILES 解析成功不代表机理正确
"""

from __future__ import annotations

import logging
from typing import Any, Final

from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors

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
    Verification,
)

logger = logging.getLogger(__name__)


#: 构象生成的随机种子。
#:
#: **固定值是刻意的**：ETKDG 嵌入本身含随机性，
#: 若不固定，同一分子每次渲染形状都不同——
#: 学生会看到「同一个乙醇变成了两个样子」，
#: 也无法对照反应前后的构象变化。
#:
#: 选`0xF00D`（"food" 的视觉码）仅为可读性；任何固定值均可。
_CONFORMER_SEED: Final[int] = 0xF00D

# RDKit 默认会向 stderr 打印解析警告。工具需要把这些转成结构化状态，
# 因此关闭其日志输出，避免污染服务日志（并可能含用户输入片段）。
RDLogger.DisableLog("rdApp.*")

#: 输入 SMILES 最大字符数。防止超长输入消耗解析资源。
MAX_SMILES_LENGTH: Final[int] = 2000

#: 分子最大重原子数。超出视为超出支持范围，而非"非法"。
MAX_HEAVY_ATOMS: Final[int] = 200

#: 官能团定义表。
#:
#: 说明：以下 SMARTS 取自 RDKit 官方文档与化学实践中的通用写法，
#: **需在目标环境实测确认后再视为已验证**。新增条目时必须先跑
#: `backend/tests/chem/test_engine.py` 中的对照用例。
#:
#: ## 羟基为什么拆成两条（2026-10-04，决策项 I5）
#:
#: 原``("羟基", "[OX2H]")`` 只描述"连两个原子且带氢的氧"，
#: **无法区分醇羟基与羧酸羟基**。实测导致乙酸、苯甲酸、甘氨酸
#: 全部误报羟基——学生看到"乙酸含羟基"会误以为羧酸是醇。
#:
#: 但**只改成排除羧酸仍不够**：`[OX2H][CX4]` 这类写法会
#: **漏掉苯酚**（酚羟基连的是芳香碳，不是 sp3 碳）。
#: 而本项目语料把"醇羟基 vs 酚羟基"列为**高频易错点**
#: （见 `org-alcohol-phenol-difference`），必须能区分。
#:
#: 故拆为两条，与语料口径一致：
#:
#: - ``醇羟基``：羟基连 **sp3 碳**；递归否定排除缩醛/半缩醛/糖
#:   （那些碳同时连羟基与 O/S/N/P）。
#: - ``酚羟基``：羟基连**芳香碳**。
#:
#: 两条**互斥**（实测：乙醇只命中醇羟基，苯酚只命中酚羟基），
#: 但可同时命中——羟基苯甲醇（``Oc1ccccc1CO``）两者都有，
#: 实测原子索引不重叠，属正确行为。
#:
#: **依据**：联网查到的三种候选写法逐一实测（16 个高中常见结构），
#: 详见 `docs/chem-smarts-verification.md`。
_FUNCTIONAL_GROUPS: Final[tuple[tuple[str, str], ...]] = (
    # 醇羟基：连sp3 碳，排除羧酸（其碳为 CX3）与缩醛类。
    ("醇羟基", "[OX2H][CX4;!$(C([OX2H])[O,S,#7,#15])]"),
    # 酚羟基：连芳香碳。末尾的 [c] 是关键——用 [CX4] 会漏掉苯酚。
    ("酚羟基", "[OX2H][c]"),
    ("醛基", "[CX3H1](=O)[#6]"),
    ("酮羰基", "[#6][CX3](=O)[#6]"),
    ("羧基", "[CX3](=O)[OX2H1]"),
    ("酯基", "[CX3](=O)[OX2H0][#6]"),
    ("醚键", "[OD2]([#6])[#6]"),
    ("氨基", "[NX3;H2,H1]"),
    ("碳碳双键", "[CX3]=[CX3]"),
    ("碳碳三键", "[CX2]#[CX2]"),
    ("苯环", "c1ccccc1"),
    ("卤素原子", "[F,Cl,Br,I]"),
)

#: 解析结果中不做机理判断的固定说明，随结果一并返回，
#: 避免下游把"工具校验通过"误读为"机理已被证明"。
_SCOPE_NOTE: Final[str] = "本结果仅确认结构合法性与客观属性，不代表反应机理已被验证。"


class ChemEngine:
    """RDKit 封装。

    无状态，可安全复用；建议按进程创建单例（见 :func:`get_engine`）。
    """

    def __init__(self, *, max_atoms: int = MAX_HEAVY_ATOMS) -> None:
        self._max_atoms = max_atoms
        self._patterns: tuple[tuple[str, str, Chem.Mol], ...] = self._compile_patterns()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _compile_patterns() -> tuple[tuple[str, str, Chem.Mol], ...]:
        """预编译官能团 SMARTS。

        模式本身非法属于开发期错误（不是用户输入问题），因此直接抛出，
        不降级为静默跳过。

        返回 (中文名, 原始 SMARTS 文本, 编译后的 Mol) 三元组。原始文本单独
        保留是因为 ``Chem.Mol`` 不提供还原 SMARTS 的接口（实测确认无
        ``GetSmarts``），而结果中需要回报判定依据。
        """
        compiled: list[tuple[str, str, Chem.Mol]] = []
        for name, smarts in _FUNCTIONAL_GROUPS:
            pattern = Chem.MolFromSmarts(smarts)
            if pattern is None:
                raise RuntimeError(f"内置官能团 SMARTS 无法编译：{name} -> {smarts}")
            compiled.append((name, smarts, pattern))
        return tuple(compiled)

    @staticmethod
    def _describe_problems(mol: Chem.Mol) -> list[str]:
        """收集 RDKit 能识别的化学问题描述。"""
        problems = Chem.DetectChemistryProblems(mol)
        messages: list[str] = []
        for problem in problems:
            try:
                messages.append(problem.Message())
            except Exception:  # pragma: no cover - 防御性
                messages.append(problem.GetType())
        return messages

    def _validate_size(self, smiles: str) -> None:
        if not isinstance(smiles, str):
            raise InvalidStructureError("结构输入必须是文本。")
        if not smiles.strip():
            raise InvalidStructureError("结构输入不能为空。")
        if len(smiles) > MAX_SMILES_LENGTH:
            raise MoleculeTooLargeError(
                f"结构表达式过长（{len(smiles)} 字符，上限 {MAX_SMILES_LENGTH}）。"
            )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def parse(self, smiles: str) -> ParseResult:
        """解析 SMILES 表达式。

        Args:
            smiles: 用户提供的 SMILES。

        Returns:
            :class:`ParseResult`。结构非法时**抛出**受控异常而非返回
            ``ok=False``，因为"解析失败"是需要调用方显式处理的错误状态，
            不应与"解析成功但内容有限"混淆。

        Raises:
            InvalidStructureError: SMILES 语法错误或结构非法。
            MoleculeTooLargeError: 输入过长。
            UnsupportedStructureError: 可解析但规模超出支持范围。
        """
        self._validate_size(smiles)

        try:
            mol = Chem.MolFromSmiles(smiles)
        except Exception as exc:  # RDKit 偶发抛出的非预期异常
            # 不向用户暴露底层异常细节
            logger.warning("SMILES 解析异常", extra={"error_type": type(exc).__name__})
            raise InvalidStructureError(
                "结构表达式无法解析，请检查写法。",
                detail=str(exc),
            ) from exc

        if mol is None:
            # RDKit 已关闭日志，此处补一条结构化说明供上层展示
            raise InvalidStructureError(
                "结构表达式无效，可能存在括号不匹配、价态不合理或使用了不存在的元素。",
                detail="Chem.MolFromSmiles returned None",
            )

        if mol.GetNumHeavyAtoms() > self._max_atoms:
            raise UnsupportedStructureError(
                f"分子含 {mol.GetNumHeavyAtoms()} 个重原子，"
                f"超出本工具上限 {self._max_atoms}，暂不支持可视化与性质分析。",
                detail="heavy atom count exceeded",
            )

        properties = self._compute_properties(mol)
        structure = self._build_structure(mol, smiles)
        groups = self._detect_functional_groups(mol)
        notes = [_SCOPE_NOTE]
        if properties.num_rings == 0:
            notes.append("该分子不含环结构。")

        return ParseResult(
            ok=True,
            properties=properties,
            structure=structure,
            functional_groups=groups,
            notes=tuple(notes),
            verification=Verification.TOOL_VERIFIED,
        )

    def _compute_properties(self, mol: Chem.Mol) -> MoleculeProperties:
        """计算客观分子属性。所有字段均为确定性计算结果。"""
        return MoleculeProperties(
            molecular_formula=rdMolDescriptors.CalcMolFormula(mol),
            molecular_weight=Descriptors.MolWt(mol),
            num_atoms=mol.GetNumAtoms(),
            num_bonds=mol.GetNumBonds(),
            num_rings=rdMolDescriptors.CalcNumRings(mol),
            num_aromatic_rings=rdMolDescriptors.CalcNumAromaticRings(mol),
            num_heteroatoms=rdMolDescriptors.CalcNumHeteroatoms(mol),
        )

    def _build_structure(self, mol: Chem.Mol, original: str) -> MoleculeStructure:
        """构造供前端渲染的结构化数据。

        只输出 SMILES 文本与 JSON 结构，不含任何可执行代码
        （`docs/security-privacy.md` §4）。

        ## 为什么在这里生成三维坐标（2026-10-04，决策项 I7）

        原实现只输出 ``atom_count`` / ``bond_count``，
        **前端拿不到任何可渲染的东西**——不知道原子在哪、
        不知道键连的是谁。这不是"前端自行处理"的设计，
        而是**数据缺口**：让前端从 SMILES 反推结构等于
        重新实现一遍化学信息学。

        故改为输出 ``atoms`` / ``bonds`` / ``coords``。
        坐标用 RDKit 的 ETKDG + MMFF 力场生成，**实测 1–17 ms**
        （见 ``docs/frontend-viz-verification.md``），同步返回即可，
        不需要异步任务。

        坐标生成的取舍：

        - **加显式氢**（``AddHs``）——高中教学必须看到 C 上的 H，
          否则学生数不出``CH₃`` 的四个键。
        - **固定随机种子**——否则每次调用坐标都不同，
          同一分子在两次渲染中形状不一样，无法对照。
        - **芳香环显式交替**——不做 kekulization 的话
          苯环的键全是 ``aromatic`` 类型，前端画不出单双键交替，
          而那是高中必考的结构特征。
        """
        canonical = Chem.MolToSmiles(mol)
        viz_data: dict[str, Any] = {
            "schema": "molecule-structure/v2",
            "smiles": canonical,
            "atom_count": mol.GetNumAtoms(),
            "bond_count": mol.GetNumBonds(),
        }

        # 分子量/环数等性质已在 properties 层给出，此处不重复。
        #
        # **只输出加氢后的一套结构**（实测 render_* 占 payload 64%，
        # 且信息完全覆盖无氢版本——两套并存是冗余）。
        viz_data.update(self._build_atoms_and_bonds(mol))
        viz_data.update(self._embed_conformer(mol))
        return MoleculeStructure(
            canonical_smiles=canonical,
            input_smiles=original,
            viz_data=viz_data,
            verification=Verification.TOOL_VERIFIED,
        )

    @staticmethod
    def _build_atoms_and_bonds(mol: Chem.Mol) -> dict[str, Any]:
        """输出原子表与键表（**不含**显式氢）。

        前端渲染用加氢后的版本（见 :meth:`_embed_conformer`），
        这里的 ``atoms`` / ``bonds`` 是「化学视角」的结构：
        元素种类、成键数、形式电荷、是否芳香。

        原子半径取 RDKit 周期表的**共价半径**（``GetRcovalent``），
        不自己硬编码——那份表来自实验数据，
        硬编码等于用自己的近似替换权威数据。
        """
        table = Chem.GetPeriodicTable()
        atoms: list[dict[str, Any]] = []
        for atom in mol.GetAtoms():
            symbol = atom.GetSymbol()
            atoms.append(
                {
                    "index": atom.GetIdx(),
                    "element": symbol,
                    "atomic_number": atom.GetAtomicNum(),
                    # 共价半径（Å）——球棍模型的球半径基准
                    "radius": round(table.GetRcovalent(symbol), 3),
                    # 外层电子数：学生判断成键数的依据
                    "outer_electrons": table.GetNOuterElecs(symbol),
                    "formal_charge": atom.GetFormalCharge(),
                    # 挂在该原子上的氢数（**不含隐式氢**），
                    # 前端据此标注「CH₃」而非让化学去猜
                    "attached_hydrogens": atom.GetTotalNumHs(),
                    "is_aromatic": atom.GetIsAromatic(),
                }
            )

        bonds: list[dict[str, Any]] = []
        for bond in mol.GetBonds():
            bonds.append(
                {
                    "begin": bond.GetBeginAtomIdx(),
                    "end": bond.GetEndAtomIdx(),
                    # 1 / 2 / 1.5（芳香）。用 float 以便表达 1.5
                    "order": bond.GetBondTypeAsDouble(),
                    "is_aromatic": bond.GetIsAromatic(),
                }
            )
        return {"atoms": atoms, "bonds": bonds}

    @staticmethod
    def _embed_conformer(mol: Chem.Mol) -> dict[str, Any]:
        """生成三维坐标。

        失败时**不抛异常**——坐标是增强信息，
        没有它前端仍可显示结构式与性质。
        返回的 ``conformer`` 字段标明实际状态：
        ``ready`` / ``failed`` / ``skipped``。
        """
        try:
            # 加显式氢：教学必须可见
            with_h = Chem.AddHs(mol)
            # 固定种子 → 可复现。同一分子两次渲染形状一致。
            cid = AllChem.EmbedMolecule(with_h, randomSeed=_CONFORMER_SEED)
            if cid != 0:
                return {
                    "conformer": "failed",
                    "conformer_note": "无法生成三维坐标（分子可能过于柔性或含异常结构）。",
                }
            # MMFF 力场优化：让键长键角接近真实值
            AllChem.MMFFOptimizeMolecule(with_h, maxIters=200)

            conf = with_h.GetConformer()
            coords: list[list[float]] = []
            for i in range(with_h.GetNumAtoms()):
                pos = conf.GetAtomPosition(i)
                coords.append([round(pos.x, 4), round(pos.y, 4), round(pos.z, 4)])

            # 原子表需与含氢后的坐标对齐，故一并输出加氢后的原子表
            table = Chem.GetPeriodicTable()
            atoms: list[dict[str, Any]] = []
            for atom in with_h.GetAtoms():
                symbol = atom.GetSymbol()
                atoms.append(
                    {
                        "index": atom.GetIdx(),
                        "element": symbol,
                        "atomic_number": atom.GetAtomicNum(),
                        "radius": round(table.GetRcovalent(symbol), 3),
                        "outer_electrons": table.GetNOuterElecs(symbol),
                        "formal_charge": atom.GetFormalCharge(),
                        "attached_hydrogens": 0,
                        "is_aromatic": atom.GetIsAromatic(),
                        "is_explicit_hydrogen": symbol == "H",
                    }
                )

            bonds: list[dict[str, Any]] = []
            for bond in with_h.GetBonds():
                bonds.append(
                    {
                        "begin": bond.GetBeginAtomIdx(),
                        "end": bond.GetEndAtomIdx(),
                        "order": bond.GetBondTypeAsDouble(),
                        "is_aromatic": bond.GetIsAromatic(),
                    }
                )

            return {
                "conformer": "ready",
                "conformer_note": "坐标为 ETKDG 嵌入 + MMFF 优化结果，代表一种可能构象（非唯一）。",
                "coords": coords,
                # **坐标、render_atoms、render_bonds 三者索引一一对应**
                #（均为加氢后的顺序），前端按索引取用即可，不需再做映射。
                # 与 ``atoms`` / ``bonds`` 的区别：后者是「化学视角」
                #（不含显式氢，用于性质与官能团判断），这里是
                #「渲染视角」（含显式氢，用于球棍模型）。
                "render_atoms": atoms,
                "render_bonds": bonds,
                "has_explicit_hydrogens": True,
            }
        except Exception as exc:  # noqa: BLE001 - 坐标是增强信息，失败不应影响解析
            logger.warning("构象生成失败：%s", type(exc).__name__)
            return {
                "conformer": "failed",
                "conformer_note": "生成三维坐标时出错，结构式与性质不受影响。",
            }

    def _detect_functional_groups(self, mol: Chem.Mol) -> tuple[FunctionalGroupHit, ...]:
        """识别高中课程范围内的常见官能团。

        未命中不代表结构简单，命中也不代表机理成立；仅作教学辅助。
        """
        hits: list[FunctionalGroupHit] = []
        for name, smarts, pattern in self._patterns:
            match = mol.GetSubstructMatch(pattern)
            hits.append(
                FunctionalGroupHit(
                    name=name,
                    smiles=smarts,
                    matched=bool(match),
                    atom_indices=tuple(match) if match else (),
                )
            )
        return tuple(hits)

    def is_valid(self, smiles: str) -> bool:
        """便捷判断：表达式是否可被解析为合法分子。

        面向不需要完整结果的调用方（如前置校验）。
        """
        try:
            self.parse(smiles)
        except ChemError:
            return False
        return True


_ENGINE: ChemEngine | None = None


def get_engine() -> ChemEngine:
    """获取进程级单例。

    预编译的 SMARTS 模式可复用，避免每次调用重复解析。
    """
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ChemEngine()
    return _ENGINE


__all__ = ["ChemEngine", "get_engine", "MAX_SMILES_LENGTH", "MAX_HEAVY_ATOMS"]
