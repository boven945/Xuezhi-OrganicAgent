"""官能团识别的化学正确性测试（决策项 I5）。

## 为什么这组测试值得单独写

原先的 ``("羟基", "[OX2H]")`` 只描述"连两个原子且带氢的氧"，
**无法区分醇羟基与羧酸羟基**。实测后果：乙酸、苯甲酸、甘氨酸
全部误报羟基——学生看到"乙酸含羟基"会误以为羧酸是醇。

**但只修这一处还不够。** 联网查到的两种"排除羧酸"写法
（``[#6X4][OX2H]`` 与 ``[OX2H][CX4;!$(...)]``）都会
**漏掉苯酚**——酚羟基连的是芳香碳，不是 sp3 碳。
而本项目语料把"醇羟基 vs 酚羟基"列为高频易错点
（``org-alcohol-phenol-difference``），必须能区分。

最终方案是拆成两条：``醇羟基``（连 sp3 碳）+ ``酚羟基``（连芳香碳）。
本文件把该结论固化为可执行断言，防止将来被"简化"回去。
"""

from __future__ import annotations

import pytest

from app.chem.engine import get_engine


@pytest.fixture(scope="module")
def engine():
    """模块级 fixture：SMARTS 只编译一次，全组共用。"""
    return get_engine()


def _names(engine, smiles: str) -> set[str]:
    """返回命中的官能团名集合。"""
    result = engine.parse(smiles)
    return {g.name for g in result.functional_groups if g.matched}


class TestHydroxylPrecision:
    """羟基识别的化学正确性（I5 核心）。"""

    @pytest.mark.parametrize(
        ("smiles", "name", "expected"),
        [
            # --- 醇羟基：连 sp3 碳 ---
            ("CCO", "乙醇", {"醇羟基"}),
            ("OCCO", "乙二醇", {"醇羟基"}),
            ("CC(C)(C)O", "叔丁醇", {"醇羟基"}),
            ("OC1CCCCC1", "环己醇", {"醇羟基"}),
            ("OCc1ccccc1", "苯甲醇（羟基在侧链）", {"醇羟基", "苯环"}),
            ("c1ccc(CCO)cc1", "苯乙醇（羟基在侧链）", {"醇羟基", "苯环"}),
            # --- 酚羟基：连芳香碳 ---
            ("c1ccccc1O", "苯酚", {"酚羟基", "苯环"}),
            ("c1ccc(O)cc1C", "甲基苯酚", {"酚羟基", "苯环"}),
        ],
    )
    def test_alcohol_vs_phenol(self, engine, smiles, name, expected) -> None:
        """醇羟基与酚羟基须按连接碳的类型正确区分。

        这组用例是本模块存在的理由：若把两条合并回``[OX2H]``，
        羧酸会误报；若都写成 ``[CX4]``，本组全部失败（苯酚被漏）。
        """
        assert _names(engine, smiles) == expected, (
            f"{name}（{smiles}）期望 {expected}，"
            f"实际 {_names(engine, smiles)}"
        )

    @pytest.mark.parametrize(
        ("smiles", "name"),
        [
            ("CC(=O)O", "乙酸"),
            ("OC(=O)c1ccccc1", "苯甲酸"),
            ("NCC(=O)O", "甘氨酸"),
            ("CC(=O)Oc1ccccc1C(=O)O", "阿司匹林"),
        ],
    )
    def test_carboxylic_acid_has_no_hydroxyl(self, engine, smiles, name) -> None:
        """羧酸**不得**命中任何羟基。

        这是 I5 的核心验收点。原``[OX2H]`` 在这四个结构上全部误报。
        羧基里的 O-H 属于羧基的一部分，不是独立的羟基官能团。
        """
        names = _names(engine, smiles)
        assert "醇羟基" not in names, f"{name}不应命中醇羟基，实际 {names}"
        assert "酚羟基" not in names, f"{name} 不应命中酚羟基，实际 {names}"
        assert "羧基" in names, f"{name} 应命中羧基"

    def test_ester_has_no_hydroxyl(self, engine) -> None:
        """酯也不得命中羟基（酯基的 O不带 H，本就不会命中）。"""
        names = _names(engine, "CC(=O)OC")
        assert "酯基" in names
        assert "醇羟基" not in names
        assert "酚羟基" not in names

    def test_aromatic_acid_oh_is_not_phenol(self, engine) -> None:
        """苯甲酸的 OH 连在**羰基碳**上，不是芳香碳，故不算酚羟基。

        这是容易想当然的地方：苯甲酸含苯环，直觉上"环上带 OH"。
        但它的 OH 在 ``-COOH`` 里，连接点是 sp2 羰基碳。
        """
        names = _names(engine, "OC(=O)c1ccccc1")
        assert "羧基" in names and "苯环" in names
        assert "酚羟基" not in names, "苯甲酸的 OH 不在芳香碳上"

    def test_molecule_can_hit_both(self, engine) -> None:
        """同一分子可同时含醇羟基与酚羟基——此时两者都应命中。

        羟基苯甲醇（``Oc1ccccc1CO``）苯环上有酚羟基、
        侧链有醇羟基。实测原子索引不重叠，属正确行为。
        """
        result = engine.parse("Oc1ccccc1CO")
        by_name = {
            g.name: set(g.atom_indices)
            for g in result.functional_groups
            if g.matched
        }
        assert "酚羟基" in by_name
        assert "醇羟基" in by_name
        # 两个羟基是不同原子
        assert not (by_name["酚羟基"] & by_name["醇羟基"]), (
            "两个羟基应指向不同原子"
        )


class TestOtherGroupsUnaffected:
    """其余官能团不受本次改动影响。"""

    @pytest.mark.parametrize(
        ("smiles", "expected"),
        [
            ("CC=O", {"醛基"}),
            ("CC(=O)C", {"酮羰基"}),
            ("CC(=O)O", {"羧基"}),
            ("CC(=O)OC", {"酯基", "醚键"}),
            ("CCO", {"醇羟基"}),
            ("CN", {"氨基"}),
            ("C=C", {"碳碳双键"}),
            ("C#C", {"碳碳三键"}),
            ("c1ccccc1", {"苯环"}),
            ("CCCl", {"卤素原子"}),
            ("CC#N", set()),  # 氰基不是碳碳三键
        ],
    )
    def test_standard_cases(self, engine, smiles, expected) -> None:
        """标准用例应与既有行为一致。

        特别注意 ``CC#N``（乙腈）：含C≡N 但**不是**碳碳三键，
        故不应命中——这是本项目早期实测纠正过的认知
        （见 docs/chem-engine-verification.md）。
        """
        assert _names(engine, smiles) == expected, (
            f"{smiles} 期望 {expected}，实际 {_names(engine, smiles)}"
        )

    def test_group_count_is_stable(self, engine) -> None:
        """官能团总数须固定——前端按清单渲染，长度变化会出问题。

        本次从 11 条增至 12 条（羟基拆成两条），此后不应再变。
        """
        a = engine.parse("CCO")
        b = engine.parse("c1ccccc1O")
        assert len(a.functional_groups) == len(b.functional_groups)
        assert len(a.functional_groups) == 12, (
            f"官能团应为 12 条（羟基拆为醇/酚两条），"
            f"实际 {len(a.functional_groups)}"
        )

    def test_unmatched_still_reported(self, engine) -> None:
        """未命中的官能团仍须返回（前端要显示完整清单）。"""
        result = engine.parse("CCO")
        groups = {g.name: g for g in result.functional_groups}
        assert "苯环" in groups
        assert groups["苯环"].matched is False
        assert groups["苯环"].atom_indices == ()
        assert groups["酚羟基"].matched is False

    def test_hit_atom_indices_point_to_oxygen(self, engine) -> None:
        """命中时原子索引应指向氧原子（前端据此高亮）。"""
        from rdkit import Chem

        for smiles, group in (("CCO", "醇羟基"), ("c1ccccc1O", "酚羟基")):
            result = engine.parse(smiles)
            hit = next(g for g in result.functional_groups if g.name == group)
            assert hit.matched, f"{smiles} 应命中 {group}"
            mol = Chem.MolFromSmiles(smiles)
            symbols = {mol.GetAtomWithIdx(i).GetSymbol() for i in hit.atom_indices}
            assert "O" in symbols, f"{group} 的原子索引应含氧，实得 {symbols}"


__all__ = [
    "TestHydroxylPrecision",
    "TestOtherGroupsUnaffected",
]
