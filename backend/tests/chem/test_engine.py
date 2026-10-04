"""化学引擎测试。

运行前置条件：RDKit 可正常导入。

    pytest backend/tests/chem/ -v

**重要**：若导入 rdkit 时报"应用程序控制策略已阻止此文件"，
说明所在环境启用了 WDAC / Smart App Control，拦截了未签名的
`.pyd`。此时需先处理环境策略（见 `docs/py312-install-verification.md`
§2.2），或改在未受管控的环境执行。

测试用例中标注 `[未实跑]` 的项目前无法在本机验证，详见
`docs/chem-engine-verification.md`。
"""

from __future__ import annotations

import pytest

from app.chem import (
    ChemEngine,
    InvalidStructureError,
    MoleculeTooLargeError,
    UnsupportedStructureError,
    get_engine,
)
from app.chem.models import Verification

rdkit = pytest.importorskip("rdkit", reason="RDKit 不可用")
from rdkit import Chem  # noqa: E402


@pytest.fixture(scope="module")
def engine() -> ChemEngine:
    return get_engine()


# ----------------------------------------------------------------------
# 解析：合法结构
# ----------------------------------------------------------------------
class TestValidStructures:
    @pytest.mark.parametrize(
        ("smiles", "formula", "note"),
        [
            ("CCO", "C2H6O", "乙醇"),
            ("CC(=O)O", "C2H4O2", "乙酸"),
            ("c1ccccc1", "C6H6", "苯"),
            ("C=CC", "C3H6", "丙烯"),
            ("CC#N", "C2H3N", "乙腈"),
            ("CC(N)C(=O)O", "C3H7NO2", "丙氨酸"),
        ],
    )
    def test_common_molecules(self, engine, smiles, formula, note):
        result = engine.parse(smiles)
        assert result.ok is True
        assert result.properties.molecular_formula == formula, note
        assert result.verification == Verification.TOOL_VERIFIED
        assert result.structure is not None
        assert result.structure.canonical_smiles

    def test_scope_note_always_present(self, engine):
        """结果必须始终携带"不代表机理已验证"的说明。

        对应 `docs/product-scope.md` §6：不得把"模型生成"表述成"已校验"。
        """
        result = engine.parse("CCO")
        assert any("不代表反应机理" in n for n in result.notes)

    def test_viz_data_has_no_executable_content(self, engine):
        """可视化数据不得含可执行代码（`docs/security-privacy.md` §4）。"""
        result = engine.parse("CCO")
        viz = result.structure.viz_data
        assert viz["schema"] == "molecule-structure/v1"
        # 不应出现任何脚本或 HTML 载荷
        for key in viz:
            assert "<" not in str(viz[key])
            assert "function" not in str(viz[key]).lower()

    def test_canonical_smiles_differs_from_input(self, engine):
        """规范化 SMILES 应与原始写法可能不同（如等价表示）。"""
        result = engine.parse("OCC")
        assert result.structure.canonical_smiles == "CCO"
        assert result.structure.input_smiles == "OCC"


# ----------------------------------------------------------------------
# 解析：非法结构必须抛出受控错误
# ----------------------------------------------------------------------
class TestInvalidStructures:
    @pytest.mark.parametrize(
        "bad",
        [
            # 注意：单个 "C" 是合法的甲基自由基（实测 formula=CH4），
            # 因此不能作为非法用例。真正非法的输入如下：
            "CC(",  # 括号不匹配
            "CC)",  # 括号不匹配
            "C1CC",  # 环未闭合
            "xyz",  # 非法元素
            "CC==",  # 键符号错误
            "",  # 空串
            "   ",  # 空白
        ],
    )
    def test_invalid_smiles_raises(self, engine, bad):
        with pytest.raises(InvalidStructureError):
            engine.parse(bad)

    def test_error_has_stable_code(self, engine):
        """错误码须稳定，供前端分类处理。"""
        with pytest.raises(InvalidStructureError) as exc:
            engine.parse("CC(")
        assert exc.value.code == "chem_invalid_structure"

    def test_error_message_hides_internals(self, engine):
        """错误信息不得泄露堆栈或内部细节。"""
        with pytest.raises(InvalidStructureError) as exc:
            engine.parse("CC(")
        assert "Traceback" not in exc.value.user_message
        assert "site-packages" not in exc.value.user_message

    def test_none_input_raises(self, engine):
        with pytest.raises(InvalidStructureError):
            engine.parse(None)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 规模限制
# ----------------------------------------------------------------------
class TestSizeLimits:
    def test_too_long_rejected(self, engine):
        long_smi = "C" * 5000
        with pytest.raises(MoleculeTooLargeError):
            engine.parse(long_smi)

    def test_too_many_atoms_rejected(self):
        # 构造 500 个碳原子的链，远超默认上限 200
        small = ChemEngine(max_atoms=10)
        with pytest.raises(UnsupportedStructureError):
            small.parse("C" * 50)

    def test_molecule_too_large_is_unsupported_subclass(self):
        """过大应归为"不支持"而非"非法"，前端提示需区分。"""
        assert issubclass(MoleculeTooLargeError, UnsupportedStructureError)


# ----------------------------------------------------------------------
# 官能团识别（高中范围）
# ----------------------------------------------------------------------
class TestFunctionalGroups:
    @pytest.mark.parametrize(
        ("smiles", "group"),
        [
            ("CCO", "醇羟基"),
            ("c1ccccc1O", "酚羟基"),
            ("CC=O", "醛基"),
            ("CC(=O)C", "酮羰基"),
            ("CC(=O)O", "羧基"),
            ("c1ccccc1", "苯环"),
            ("C=CC", "碳碳双键"),
            # 注意：CC#N 是氰基（C≡N），不含碳碳三键；碳碳三键须用乙炔 C#C
            ("C#C", "碳碳三键"),
            ("CCCl", "卤素原子"),
        ],
    )
    def test_group_detected(self, engine, smiles, group):
        result = engine.parse(smiles)
        names = {g.name for g in result.functional_groups if g.matched}
        assert group in names, f"{smiles} 应识别出 {group}，实际 {names}"

    def test_unmatched_group_reported(self, engine):
        """未命中的官能团也要返回，便于前端显示完整清单。"""
        result = engine.parse("CCO")
        groups = {g.name: g for g in result.functional_groups}
        assert "苯环" in groups
        assert groups["苯环"].matched is False
        assert groups["苯环"].atom_indices == ()

    def test_hit_has_atom_indices(self, engine):
        """命中时应给出原子索引，供前端高亮。"""
        result = engine.parse("CCO")
        hydroxyl = next(g for g in result.functional_groups if g.name == "醇羟基")
        assert hydroxyl.matched
        assert len(hydroxyl.atom_indices) >= 1

    def test_all_groups_checked(self, engine):
        """每次解析都应返回完整官能团清单，长度固定。"""
        a = engine.parse("CCO")
        b = engine.parse("c1ccccc1")
        assert len(a.functional_groups) == len(b.functional_groups)


# ----------------------------------------------------------------------
# 属性计算
# ----------------------------------------------------------------------
class TestProperties:
    def test_ethanol_properties(self, engine):
        """乙醇 CCO 的属性。

        注意：RDKit 的 ``GetNumAtoms()`` 返回**显式原子数**（不含隐式氢），
        实测为 3 而非 9。分子式 C2H6O 与分子量 46.069 已隐含 6 个氢。
        """
        p = engine.parse("CCO").properties
        assert p.molecular_formula == "C2H6O"
        assert 46.0 < p.molecular_weight < 47.0
        assert p.num_atoms == 3
        assert p.num_bonds == 2
        assert p.num_rings == 0
        assert p.num_heteroatoms == 1

    def test_benzene_ring_counts(self, engine):
        p = engine.parse("c1ccccc1").properties
        assert p.num_rings == 1
        assert p.num_aromatic_rings == 1
        assert p.num_heteroatoms == 0

    def test_ring_note_added_for_acyclic(self, engine):
        result = engine.parse("CCO")
        assert any("不含环" in n for n in result.notes)

    def test_no_ring_note_for_benzene(self, engine):
        result = engine.parse("c1ccccc1")
        assert not any("不含环" in n for n in result.notes)

    def test_properties_are_deterministic(self, engine):
        """同一输入两次解析结果必须一致（可复现性要求）。"""
        a = engine.parse("CC(=O)O").properties
        b = engine.parse("CC(=O)O").properties
        assert a == b


# ----------------------------------------------------------------------
# 便捷接口与单例
# ----------------------------------------------------------------------
class TestConvenience:
    def test_is_valid_true(self, engine):
        assert engine.is_valid("CCO") is True

    def test_is_valid_false(self, engine):
        assert engine.is_valid("CC(") is False

    def test_get_engine_is_singleton(self):
        assert get_engine() is get_engine()

    def test_result_serializable(self, engine):
        """结果须可 JSON 序列化，供 API 层直接返回。"""
        import json

        payload = engine.parse("CCO").to_dict()
        text = json.dumps(payload, ensure_ascii=False)
        assert "醇羟基" in text
        assert json.loads(text)["ok"] is True
