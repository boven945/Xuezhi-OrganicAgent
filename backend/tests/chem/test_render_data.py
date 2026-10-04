"""渲染数据契约的测试（决策项 I7）。

## 为什么这些断言值得写死

前端 3D 视图**完全依赖**这份数据的形状。索引错位不会让
任何测试失败，只会让渲染出「原子飘在错误位置」「键连到了
别的原子」——**看起来像渲染 bug，实际是数据 bug**，
极难排查。故在此锁死契约。

## 关键不变量

1. ``coords[i]`` 对应 ``render_atoms[i]``；
2. ``render_bonds[*].begin/end`` 都在 ``render_atoms`` 的索引范围内；
3. 坐标是**有限数字**（NaN/Inf 会让Three.js 静默不渲染）。
"""

from __future__ import annotations

import math

import pytest

from app.chem.engine import get_engine


@pytest.fixture(scope="module")
def engine():
    return get_engine()


class TestConformerGeneration:
    """三维坐标生成。"""

    def test_ethanol_gets_ready_conformer(self, engine) -> None:
        viz = engine.parse("CCO").structure.viz_data
        assert viz["conformer"] == "ready"
        assert viz["has_explicit_hydrogens"] is True
        assert len(viz["coords"]) == 9, "乙醇含 9 个原子（含 6 个 H）"
        # 有说明文字——学生需知道这不是唯一构象
        assert "构象" in viz["conformer_note"]

    def test_coordinates_are_index_aligned_with_render_atoms(self, engine) -> None:
        """**核心不变量**：`coords[i]` 对应 `render_atoms[i]`。

        错位会让原子飘到错误位置，且不报任何错。
        """
        for smi in ("CCO", "c1ccccc1O", "CC(=O)OC"):
            viz = engine.parse(smi).structure.viz_data
            assert len(viz["coords"]) == len(viz["render_atoms"]), f"{smi} 索引数不一致"
            for atom in viz["render_atoms"]:
                assert 0 <= atom["index"] < len(viz["coords"]), f"{smi} 原子索引越界"

    def test_bond_indices_are_within_atom_range(self, engine) -> None:
        """键的端点索引必须在原子表范围内。"""
        for smi in ("CCO", "c1ccccc1O", "CC(=O)OC", "NCC(=O)O"):
            viz = engine.parse(smi).structure.viz_data
            n = len(viz["render_atoms"])
            for bond in viz["render_bonds"]:
                assert 0 <= bond["begin"] < n, f"{smi} begin 越界"
                assert 0 <= bond["end"] < n, f"{smi} end 越界"
                assert bond["begin"] != bond["end"], "键不应连到自身"

    def test_coordinates_are_finite_numbers(self, engine) -> None:
        """坐标必须是有限数字。

        **NaN / Inf 会让 Three.js 静默不渲染**——不抛异常，
        画面就是空的。必须在这里拦住。
        """
        for smi in ("CCO", "c1ccccc1O"):
            viz = engine.parse(smi).structure.viz_data
            for xyz in viz["coords"]:
                assert all(math.isfinite(v) for v in xyz), f"{smi} 坐标非有限值"

    def test_coordinates_are_reproducible(self, engine) -> None:
        """同一分子两次解析坐标必须**完全一致**。

        原因见 `_CONFORMER_SEED` 的注释：种子不固定的话
        学生会看到「同一个乙醇变成两个样子」。
        这是可复现性要求，不是性能优化。
        """
        a = engine.parse("CCO").structure.viz_data["coords"]
        b = engine.parse("CCO").structure.viz_data["coords"]
        assert a == b, "构象生成不可复现——随机种子未生效"

    def test_different_molecules_differ(self, engine) -> None:
        """反向检查：不同分子的坐标**不能**相同。

        防止「固定种子」写成常量导致所有分子形状一样。
        """
        a = engine.parse("CCO").structure.viz_data["coords"]
        b = engine.parse("CC(=O)OC").structure.viz_data["coords"]
        assert a != b, "乙醇与乙酸乙酯坐标相同，疑似写死"

    def test_bond_length_is_physically_plausible(self, engine) -> None:
        """C–C 单键长应在 1.2–1.7 Å 之间（实测约 1.52 Å）。

        若坐标缩放错误（单位从 Å 变成 nm 之类），
        键长会离谱到一眼可见。这条断言能在渲染前拦住。
        """
        viz = engine.parse("CCO").structure.viz_data
        coords = viz["coords"]
        bond = next(
            b for b in viz["render_bonds"] if b["begin"] == 0 and b["end"] == 1
        )
        a, b = coords[bond["begin"]], coords[bond["end"]]
        dist = math.dist(a, b)
        assert 1.2 < dist < 1.7, f"C–C 键长 {dist:.2f} Å 不在合理范围"


class TestAtomAndBondTables:
    """原子表与键表。"""

    def test_atom_radius_comes_from_rdkit_periodic_table(self, engine) -> None:
        """原子半径须是 RDKit 的共价半径，不是自造近似。

        断言具体数值：若有人改成"看起来差不多"的常数，
        分子大小比例会失真而测试不会报警。
        """
        viz = engine.parse("CCO").structure.viz_data
        radii = {a["element"]: a["radius"] for a in viz["atoms"]}
        assert radii["C"] == 0.76, "碳的共价半径应0.76 Å（RDKit 实测值）"
        assert radii["O"] == 0.66, "氧的共价半径应 0.66 Å"

    def test_outer_electrons_are_informative(self, engine) -> None:
        """外层电子数让学生能自己判断成键数（高中考点）。"""
        viz = engine.parse("CCO").structure.viz_data
        valence = {a["element"]: a["outer_electrons"] for a in viz["atoms"]}
        assert valence["C"] == 4
        assert valence["O"] == 6

    def test_attached_hydrogens_lets_student_count_bonds(self, engine) -> None:
        """``attached_hydrogens`` 应给出隐式 H 数。

        乙醇的 C 是 CH₃–CH₂–OH，学生要数出「4 个键」；
        若该字段缺失，前端只能自己猜。
        """
        viz = engine.parse("CCO").structure.viz_data
        assert [a["attached_hydrogens"] for a in viz["atoms"]] == [3, 2, 1]

    def test_aromatic_flag_is_set_for_benzene_ring(self, engine) -> None:
        """苯环的碳须标记 ``is_aromatic``。

        前端据此决定是否画交替单双键——那是高中必考的结构特征。
        """
        viz = engine.parse("c1ccccc1").structure.viz_data
        assert any(b["is_aromatic"] for b in viz["bonds"]), "苯环缺芳香标记"
        assert sum(1 for a in viz["atoms"] if a["is_aromatic"]) == 6

    def test_bond_order_is_float_to_allow_1_5(self, engine) -> None:
        """键级用 float 以表达 1.5（芳香）。

        若改成 int，芳香键的1.5 会被截断为 1，
        前端画不出交替结构。
        """
        viz = engine.parse("c1ccccc1").structure.viz_data
        orders = {b["order"] for b in viz["bonds"] if b["is_aromatic"]}
        assert orders == {1.5}, f"芳香键级应为 1.5，实得 {orders}"

    def test_double_bond_is_reported_as_two(self, engine) -> None:
        """碳碳双键的键级应为 2.0。"""
        viz = engine.parse("C=C").structure.viz_data
        assert any(b["order"] == 2.0 for b in viz["bonds"])

    def test_formal_charge_is_reported(self, engine) -> None:
        """形式电荷须如实给出（学生判断离子化合物用）。"""
        viz = engine.parse("CC(=O)[O-]").structure.viz_data
        assert any(a["formal_charge"] == -1 for a in viz["atoms"])


class TestSchemaContract:
    """契约稳定性。"""

    def test_schema_is_v2(self, engine) -> None:
        """schema 版本号变更须同步改本测试——这是刻意的摩擦。

        升版本意味着形状变了，前端的类型定义必须同步更新。
        """
        assert engine.parse("CCO").structure.viz_data["schema"] == "molecule-structure/v2"

    def test_failed_conformer_still_returns_useful_data(self, engine) -> None:
        """构象生成失败时**仍须**给出结构信息。

        坐标是增强信息，没有它学生仍应能看到结构式与官能团。
        故失败路径必须带 ``conformer='failed'`` 而非抛异常。
        """
        # 用超大分子触发可能的失败路径（不强制失败，只验证形状）
        viz = engine.parse("C" * 40).structure.viz_data
        assert viz["conformer"] in {"ready", "failed"}
        if viz["conformer"] == "failed":
            assert "conformer_note" in viz
            assert "atoms" in viz, "失败时仍须给出原子表"
