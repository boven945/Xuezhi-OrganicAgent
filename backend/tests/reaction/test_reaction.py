"""反应机理可视化模块的单测。

覆盖：

- schema / 库加载（含真实 ``data/reactions/candidates.json``，不依赖 RDKit）。
- :func:`build_frames` 纯插值（原子位置、键级、透明度、高亮的真值断言）。
- :func:`generate_mechanism` 在 RDKit 缺失时的降级（抛 ``RuntimeError``）。
- 端点分支（未找到 / RDKit 不可用 / 成功）的状态与信封。

注：RDKit 实际坐标生成须容器内 E2E（见 ``scripts/validate_reactions.py``），
本文件不依赖 RDKit，可在主机直接跑。
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.reaction import frames as frames_mod
from app.reaction.frames import build_frames, generate_mechanism
from app.reaction.library import ReactionLibrary
from app.reaction.routes import (
    MechanismRequest,
    MechanismResponse,
    reaction_mechanism,
)
from app.reaction.schemas import (
    AtomPos,
    BondState,
    ReactionDef,
    ReactionLibraryFile,
)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "reactions"
_CANDIDATES = _DATA_DIR / "candidates.json"


# ---------------------------------------------------------------------------
# 1. schema / 库加载
# ---------------------------------------------------------------------------
def test_load_real_candidates():
    lib = ReactionLibrary.load(_CANDIDATES)
    rxns = lib.all()
    assert len(rxns) == 3
    assert all(r.status == "draft" for r in rxns)
    assert {r.id for r in rxns} == {
        "addition-ethene-bromine",
        "substitution-sn2-bromoethane-hydroxide",
        "esterification-acetic-acid-ethanol",
    }


def test_library_by_id_and_filter():
    lib = ReactionLibrary.load(_CANDIDATES)
    assert lib.by_id("addition-ethene-bromine") is not None
    assert lib.by_id("nope") is None
    assert len(lib.filter(topic="酯化反应")) == 1
    assert len(lib.approved()) == 0  # 全部 draft


def test_schema_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        ReactionLibraryFile(
            reactions=[
                ReactionDef(id="x", name="X", reactant_smiles="C", product_smiles="C"),
                ReactionDef(id="x", name="X", reactant_smiles="C", product_smiles="C"),
            ]
        )


def test_schema_rejects_bad_status():
    with pytest.raises(ValueError):
        ReactionDef(
            id="x",
            name="X",
            reactant_smiles="C",
            product_smiles="C",
            status="review",  # 非法枚举
        )


def test_schema_rejects_empty_smiles():
    with pytest.raises(ValueError):
        ReactionDef(id="x", name="X", reactant_smiles="", product_smiles="C")


# ---------------------------------------------------------------------------
# 2. build_frames 纯插值
# ---------------------------------------------------------------------------
def _synth_reactant_atoms():
    return [
        AtomPos(index=0, element="C", x=0.0, y=0.0, z=0.0),
        AtomPos(index=1, element="C", x=1.5, y=0.0, z=0.0),
        AtomPos(index=2, element="C", x=3.0, y=0.0, z=0.0),
    ]


def _synth_product_atoms():
    return [
        AtomPos(index=0, element="C", x=0.1, y=0.2, z=0.3),
        AtomPos(index=1, element="C", x=1.6, y=-0.2, z=0.1),
        AtomPos(index=2, element="C", x=3.1, y=0.3, z=-0.2),
        AtomPos(index=3, element="Br", x=5.0, y=0.0, z=0.0),
    ]


def _synth_orders():
    # 反应物：(0,1) 单键=1；(1,2) 双键=2
    reactant = {(0, 1): 1.0, (1, 2): 2.0}
    # 产物：(0,1) 单键=1；(1,2) 单键=1；(0,3) 单键=1（新生成）
    product = {(0, 1): 1.0, (1, 2): 1.0, (0, 3): 1.0}
    return reactant, product


def test_build_frames_shape_and_endpoints():
    r_orders, p_orders = _synth_orders()
    mech = build_frames(
        reaction_id="t",
        reactant_atoms=_synth_reactant_atoms(),
        product_atoms=_synth_product_atoms(),
        atom_map={0: 0, 1: 1, 2: 2},
        reactant_bond_orders=r_orders,
        product_bond_orders=p_orders,
        frame_count=11,
        fps=12,
    )
    assert mech.frame_count == 11
    assert len(mech.frames) == 11
    assert mech.atom_map == {0: 0, 1: 1, 2: 2}

    # 首帧：反应物原子在反应物坐标；产物独有原子（3）在其产物坐标
    first = mech.frames[0]

    def pos_of(frame, idx):
        return next(a for a in frame.atoms if a.index == idx)

    assert (pos_of(first, 0).x, pos_of(first, 0).y, pos_of(first, 0).z) == (
        0.0,
        0.0,
        0.0,
    )
    assert (pos_of(first, 1).x, pos_of(first, 1).y, pos_of(first, 1).z) == (
        1.5,
        0.0,
        0.0,
    )
    assert (pos_of(first, 3).x, pos_of(first, 3).y, pos_of(first, 3).z) == (
        5.0,
        0.0,
        0.0,
    )
    # 末帧 = 产物坐标（含新原子 3）
    last = mech.frames[-1]
    by_idx = {a.index: a for a in last.atoms}
    assert by_idx[3].element == "Br"
    assert by_idx[3].x == 5.0


def test_build_frames_bond_state_and_opacity():
    r_orders, p_orders = _synth_orders()
    mech = build_frames(
        reaction_id="t",
        reactant_atoms=_synth_reactant_atoms(),
        product_atoms=_synth_product_atoms(),
        atom_map={0: 0, 1: 1, 2: 2},
        reactant_bond_orders=r_orders,
        product_bond_orders=p_orders,
        frame_count=11,
        fps=12,
    )

    def bond_states(frames, key):
        return [next(b for b in f.bonds if (b.a, b.b) == key or (b.b, b.a) == key)
                for f in frames]

    # (1,2)：双键 → 单键，intact，透明度恒 1，键级 2→1
    b12 = bond_states(mech.frames, (1, 2))
    assert all(b.state is BondState.INTACT for b in b12)
    assert all(b.opacity == 1.0 for b in b12)
    assert b12[0].order == pytest.approx(2.0)
    assert b12[-1].order == pytest.approx(1.0)

    # (0,3)：forming，透明度 0→1 单调增，键级 0→1
    b03 = bond_states(mech.frames, (0, 3))
    assert b03[0].state is BondState.FORMING
    assert b03[0].opacity == pytest.approx(0.0, abs=1e-6)
    assert b03[-1].opacity == pytest.approx(1.0, abs=1e-6)
    assert b03[0].order == pytest.approx(0.0, abs=1e-6)
    assert b03[-1].order == pytest.approx(1.0, abs=1e-6)
    opacities = [b.opacity for b in b03]
    assert opacities == sorted(opacities)

    # 高亮应含所有变化键端点：{0,1,2,3}
    assert set(mech.frames[-1].highlight_atoms) == {0, 1, 2, 3}


def test_build_frames_rejects_small_frame_count():
    r_orders, p_orders = _synth_orders()
    with pytest.raises(ValueError):
        build_frames(
            reaction_id="t",
            reactant_atoms=_synth_reactant_atoms(),
            product_atoms=_synth_product_atoms(),
            atom_map={0: 0, 1: 1, 2: 2},
            reactant_bond_orders=r_orders,
            product_bond_orders=p_orders,
            frame_count=1,
        )


def test_build_frames_output_schema_alias():
    r_orders, p_orders = _synth_orders()
    mech = build_frames(
        reaction_id="t",
        reactant_atoms=_synth_reactant_atoms(),
        product_atoms=_synth_product_atoms(),
        atom_map={0: 0, 1: 1, 2: 2},
        reactant_bond_orders=r_orders,
        product_bond_orders=p_orders,
    )
    dumped = mech.model_dump(by_alias=True)
    assert dumped["schema"] == "reaction-mechanism/v1"
    # 键状态序列化为字符串值
    assert dumped["frames"][0]["bonds"][0]["state"] in (
        "intact",
        "breaking",
        "forming",
    )


# ---------------------------------------------------------------------------
# 3. generate_mechanism 降级（RDKit 缺失）
# ---------------------------------------------------------------------------
def test_generate_mechanism_runs_when_available():
    # 本机 .venv-xuezhi312 装了可用的 RDKit：直接实跑真实反应，确认端到端可用。
    # 若运行环境无 RDKit（被策略拦截），优雅跳过降级测试。
    lib = ReactionLibrary.load(_CANDIDATES)
    rxn = lib.by_id("addition-ethene-bromine")
    try:
        mech = generate_mechanism(rxn, frame_count=12)
    except RuntimeError as e:  # pragma: no cover - 仅在无 RDKit 环境
        if "RDKit 不可用" in str(e):
            pytest.skip("RDKit 不可用，跳过真实帧生成")
        raise
    assert mech.frame_count == 12
    assert len(mech.frames) == 12
    assert mech.atom_map  # 映射非空
    # 教学讲解随动画下发（单一数据源：反应库）
    assert mech.name == rxn.name
    assert mech.transition_note == rxn.transition_note
    assert mech.highlight_note == rxn.highlight_note
    # 首末帧原子数一致（重原子对齐）
    assert len(mech.frames[0].atoms) == len(mech.frames[-1].atoms)


# ---------------------------------------------------------------------------
# 4. 端点分支
# ---------------------------------------------------------------------------
def _fake_mech():
    r_orders, p_orders = _synth_orders()
    mech = build_frames(
        reaction_id="t",
        reactant_atoms=_synth_reactant_atoms(),
        product_atoms=_synth_product_atoms(),
        atom_map={0: 0, 1: 1, 2: 2},
        reactant_bond_orders=r_orders,
        product_bond_orders=p_orders,
    )
    # 模拟真实 generate_mechanism：从反应定义把教学讲解带进输出
    mech.name = "T"
    mech.transition_note = "断键成键说明"
    mech.highlight_note = "高亮提示"
    return mech


def test_endpoint_not_found():
    routes_mod = importlib.import_module("app.reaction.routes")
    routes_mod._LIB = ReactionLibrary(ReactionLibraryFile())  # 空库
    resp = reaction_mechanism(MechanismRequest(reaction_id="missing"))
    assert isinstance(resp, MechanismResponse)
    assert resp.ok is False
    assert resp.error["code"] == "reaction_not_found"


def test_endpoint_success(monkeypatch):
    routes_mod = importlib.import_module("app.reaction.routes")
    lib = ReactionLibrary(
        ReactionLibraryFile(
            reactions=[
                ReactionDef(
                    id="t",
                    name="T",
                    reactant_smiles="C:1=C:2",
                    product_smiles="C:1C:2",
                )
            ]
        )
    )
    routes_mod._LIB = lib
    monkeypatch.setattr(routes_mod, "generate_mechanism", lambda d, **k: _fake_mech())
    resp = reaction_mechanism(MechanismRequest(reaction_id="t"))
    assert resp.ok is True
    assert resp.data is not None
    assert resp.data["schema"] == "reaction-mechanism/v1"
    # 教学讲解字段随动画下发，前端可直接展示
    assert resp.data["name"] == "T"
    assert resp.data["transition_note"] == "断键成键说明"
    assert resp.data["highlight_note"] == "高亮提示"


def test_endpoint_rdkit_unavailable(monkeypatch):
    routes_mod = importlib.import_module("app.reaction.routes")
    lib = ReactionLibrary(
        ReactionLibraryFile(
            reactions=[
                ReactionDef(
                    id="t",
                    name="T",
                    reactant_smiles="C:1=C:2",
                    product_smiles="C:1C:2",
                )
            ]
        )
    )
    routes_mod._LIB = lib

    def _boom(d, **k):
        raise RuntimeError("RDKit 不可用：须在容器内运行")

    monkeypatch.setattr(routes_mod, "generate_mechanism", _boom)
    resp = reaction_mechanism(MechanismRequest(reaction_id="t"))
    assert resp.ok is False
    assert resp.error["code"] == "api_component_unavailable"
