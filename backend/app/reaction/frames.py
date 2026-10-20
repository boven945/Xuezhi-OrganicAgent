"""反应机理帧生成。

模块分两层：

- :func:`build_frames` —— **纯逻辑**，不依赖 RDKit。给定**已按映射号对齐**的
  反应物/产物坐标与键级，生成 ``reaction-mechanism/v1`` 帧序列。主机单测直接覆盖。
- :func:`generate_mechanism` —— **惰性 import RDKit**，从映射号 SMILES 生成 3D 坐标
  后调用 ``build_frames``。须在容器内运行（本机 RDKit 被应用控制策略拦截，见决策 H20）。

设计（对应计划 D-R3）：过渡帧坐标为**风格化插值**，不宣称经过量子化学验证；
断键 ``opacity`` 1→0、成键 0→1，键级在两端间线性插值（如 C=C 的 π 断裂 2→1）。
"""
from __future__ import annotations

import logging
from typing import Optional

from .schemas import (
    AtomPos,
    BondFrame,
    BondState,
    MechanismFrame,
    ReactionDef,
    ReactionMechanismFile,
)

logger = logging.getLogger(__name__)

#: 构象随机种子，与 ``chem/engine.py`` 保持一致（可复现）。
_CONFORMER_SEED = 0xF00D


def _smoothstep(t: float) -> float:
    """平滑插值曲线（两端导数为 0，运动更自然）。"""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def build_frames(
    *,
    reaction_id: str,
    reactant_atoms: list[AtomPos],
    product_atoms: list[AtomPos],
    atom_map: dict[int, int],
    reactant_bond_orders: dict[tuple[int, int], float],
    product_bond_orders: dict[tuple[int, int], float],
    frame_count: int = 24,
    fps: int = 12,
) -> ReactionMechanismFile:
    """由对齐坐标与键级生成逐帧机理数据（纯逻辑，可主机单测）。

    Args:
        reaction_id: 反应定义 id（写入输出）。
        reactant_atoms / product_atoms: 原子列表，索引为**帧索引**
            （生成器以 SMILES 映射号为索引，两端同号对齐）。
            仅一端存在的原子用其自有索引。
        atom_map: 反应物索引 → 产物索引（映射号推导；通常恒等）。
        reactant_bond_orders / product_bond_orders: 键级字典，键为
            ``(min 帧索引, max 帧索引)``，值为当前分子里的键级
            （不存在的键请勿放入，或显式给 0）。
        frame_count: 帧数（≥2）。
        fps: 播放帧率。

    Returns:
        :class:`ReactionMechanismFile`：首帧=反应物构象，末帧=产物构象。
    """
    if frame_count < 2:
        raise ValueError("frame_count 至少为 2")

    r_by_idx = {a.index: a for a in reactant_atoms}
    p_by_idx = {a.index: a for a in product_atoms}
    product_idxs = set(atom_map.values())

    # 规整物理原子：frame_idx → (反应物原子, 产物原子)
    aligned: dict[int, list[Optional[AtomPos]]] = {}
    for r_idx, p_idx in atom_map.items():
        aligned[r_idx] = [r_by_idx.get(r_idx), p_by_idx.get(p_idx)]
    for r_idx in r_by_idx:
        if r_idx not in aligned:
            aligned[r_idx] = [r_by_idx[r_idx], None]
    for p_idx in p_by_idx:
        if p_idx not in product_idxs and p_idx not in aligned:
            aligned[p_idx] = [None, p_by_idx[p_idx]]

    # 高亮：存在性或键级发生变化的键的端点（反应物索引空间）。
    highlight: set[int] = set()
    for k in set(reactant_bond_orders) | set(product_bond_orders):
        ro = reactant_bond_orders.get(k, 0.0)
        po = product_bond_orders.get(k, 0.0)
        if (ro > 0) != (po > 0) or abs(ro - po) > 1e-9:
            highlight.update(k)

    frames: list[MechanismFrame] = []
    for i in range(frame_count):
        t = i / (frame_count - 1)
        s = _smoothstep(t)
        atoms: list[AtomPos] = []
        for fidx, (ra, pa) in aligned.items():
            if ra is not None and pa is not None:
                atoms.append(
                    AtomPos(
                        index=fidx,
                        element=pa.element if t > 0.5 else ra.element,
                        x=_lerp(ra.x, pa.x, s),
                        y=_lerp(ra.y, pa.y, s),
                        z=_lerp(ra.z, pa.z, s),
                    )
                )
            elif ra is not None:
                atoms.append(
                    AtomPos(index=fidx, element=ra.element, x=ra.x, y=ra.y, z=ra.z)
                )
            else:
                atoms.append(
                    AtomPos(index=fidx, element=pa.element, x=pa.x, y=pa.y, z=pa.z)
                )

        bonds: list[BondFrame] = []
        for k in sorted(set(reactant_bond_orders) | set(product_bond_orders)):
            ro = reactant_bond_orders.get(k, 0.0)
            po = product_bond_orders.get(k, 0.0)
            if ro > 0 and po <= 0:
                state = BondState.BREAKING
            elif ro <= 0 and po > 0:
                state = BondState.FORMING
            else:
                state = BondState.INTACT
            order = _lerp(ro, po, s)
            if state is BondState.BREAKING:
                opacity = 1.0 - s
            elif state is BondState.FORMING:
                opacity = s
            else:
                opacity = 1.0
            bonds.append(
                BondFrame(
                    a=k[0], b=k[1], order=order, state=state, opacity=opacity
                )
            )

        frames.append(
            MechanismFrame(
                t=t,
                atoms=atoms,
                bonds=bonds,
                highlight_atoms=sorted(highlight),
            )
        )

    return ReactionMechanismFile(
        reaction_id=reaction_id,
        frame_count=frame_count,
        fps=fps,
        atom_map=dict(atom_map),
        frames=frames,
    )


def generate_mechanism(
    definition: ReactionDef,
    *,
    frame_count: int = 24,
    fps: int = 12,
) -> ReactionMechanismFile:
    """从反应定义生成逐帧机理数据（惰性 import RDKit）。

    **须在容器内运行**：本机 RDKit 被应用控制策略拦截（决策 H20）。
    若 RDKit 不可用，抛出 ``RuntimeError("RDKit 不可用…")``，由端点转 503。

    Args:
        definition: 已加载的 :class:`ReactionDef`。
        frame_count / fps: 帧数与帧率。

    Returns:
        :class:`ReactionMechanismFile`。
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except (ImportError, OSError) as exc:  # 未安装或被策略拦截
        raise RuntimeError(
            "RDKit 不可用，无法生成 3D 坐标（须在容器内运行）"
        ) from exc

    r_mol = Chem.MolFromSmiles(definition.reactant_smiles)
    p_mol = Chem.MolFromSmiles(definition.product_smiles)
    if r_mol is None or p_mol is None:
        raise ValueError(f"反应 {definition.id} 的 SMILES 解析失败")

    r_mol = Chem.AddHs(r_mol)
    p_mol = Chem.AddHs(p_mol)
    if AllChem.EmbedMolecule(r_mol, randomSeed=_CONFORMER_SEED) != 0:
        raise RuntimeError(f"反应物构象生成失败：{definition.id}")
    if AllChem.EmbedMolecule(p_mol, randomSeed=_CONFORMER_SEED) != 0:
        raise RuntimeError(f"产物构象生成失败：{definition.id}")
    if AllChem.MMFFOptimizeMolecule(r_mol) != 0:
        logger.warning("反应物 MMFF 未完全收敛：%s", definition.id)
    if AllChem.MMFFOptimizeMolecule(p_mol) != 0:
        logger.warning("产物 MMFF 未完全收敛：%s", definition.id)

    r_atoms, r_ift = _collect_atoms(r_mol)
    p_atoms, p_ift = _collect_atoms(p_mol)

    # 映射号原子 identity 对齐；未映射原子在各自侧用负索引。
    atom_map = {
        fid: fid for fid in r_atoms if fid > 0 and fid in p_atoms
    }

    reactant_atoms = [
        AtomPos(index=i, element=s, x=pos[0], y=pos[1], z=pos[2])
        for i, (s, pos) in r_atoms.items()
    ]
    product_atoms = [
        AtomPos(index=i, element=s, x=pos[0], y=pos[1], z=pos[2])
        for i, (s, pos) in p_atoms.items()
    ]
    r_orders = _collect_bond_orders(r_mol, r_ift)
    p_orders = _collect_bond_orders(p_mol, p_ift)

    mech = build_frames(
        reaction_id=definition.id,
        reactant_atoms=reactant_atoms,
        product_atoms=product_atoms,
        atom_map=atom_map,
        reactant_bond_orders=r_orders,
        product_bond_orders=p_orders,
        frame_count=frame_count,
        fps=fps,
    )
    # 教学讲解随动画一并下发（单一数据源：反应库），避免前端再硬编码。
    mech.name = definition.name
    mech.transition_note = definition.transition_note
    mech.highlight_note = definition.highlight_note
    return mech


def _collect_atoms(mol):
    """返回 (atoms_by_frame_index, atom_idx→frame_index)。"""
    conf = mol.GetConformer()
    atoms: dict[int, tuple[str, tuple[float, float, float]]] = {}
    idx_to_frame: dict[int, int] = {}
    nxt = -1
    for atom in mol.GetAtoms():
        mn = atom.GetAtomMapNum()
        fid = mn if mn else nxt
        nxt -= 1
        pos = conf.GetAtomPosition(atom.GetIdx())
        atoms[fid] = (atom.GetSymbol(), (pos.x, pos.y, pos.z))
        idx_to_frame[atom.GetIdx()] = fid
    return atoms, idx_to_frame


def _collect_bond_orders(mol, idx_to_frame) -> dict[tuple[int, int], float]:
    orders: dict[tuple[int, int], float] = {}
    for bond in mol.GetBonds():
        a = idx_to_frame[bond.GetBeginAtomIdx()]
        b = idx_to_frame[bond.GetEndAtomIdx()]
        orders[(min(a, b), max(a, b))] = bond.GetBondTypeAsDouble()
    return orders


__all__ = ["build_frames", "generate_mechanism"]
