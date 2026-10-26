"""反应库深度校验（须 RDKit 可用）。

校验 ``data/reactions/*.json``（``reaction-library/v1``）：

- SMILES 可被 RDKit 解析；映射号在反应物/产物各自唯一。
- 映射原子两端元素一致（防手误把 C 映射到 O）。
- **断键/成键推导**与数据里的显式标注（``breaking_bonds`` / ``forming_bonds``）
  完全一致——数据标注是“可校验假设”，推导是机器事实，不一致即报错。
- 经 :func:`generate_mechanism` 实跑，确认帧生成无异常、首末帧原子数对齐。

运行：``PYTHONPATH=backend python scripts/validate_reactions.py [path]``
无 RDKit 或文件缺失时优雅退出（非零码仅在有真实校验失败）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许以脚本或模块方式运行（与 build_index.py 同约定）。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.reaction.frames import generate_mechanism  # noqa: E402
from app.reaction.library import ReactionLibrary  # noqa: E402
from app.reaction.schemas import ReactionLibraryFile  # noqa: E402

REACTION_DIR = _ROOT / "data" / "reactions"


def _bond_orders_by_map(mol) -> dict[tuple[int, int], float]:
    orders: dict[tuple[int, int], float] = {}
    for bond in mol.GetBonds():
        a = bond.GetBeginAtom().GetAtomMapNum()
        b = bond.GetEndAtom().GetAtomMapNum()
        if a and b:
            orders[(min(a, b), max(a, b))] = bond.GetBondTypeAsDouble()
    return orders


def _elements_by_map(mol) -> dict[int, str]:
    return {
        atom.GetAtomMapNum(): atom.GetSymbol()
        for atom in mol.GetAtoms()
        if atom.GetAtomMapNum()
    }


def main(path: str | None = None) -> int:
    try:
        from rdkit import Chem  # noqa: F401
    except Exception as exc:  # 未安装或被策略拦截
        print(f"[跳过] RDKit 不可用，无法深度校验：{exc}")
        return 0

    p = Path(path) if path else REACTION_DIR / "candidates.json"
    if not p.exists():
        print(f"[跳过] 反应库文件不存在：{p}")
        return 0

    raw = json.loads(p.read_text(encoding="utf-8"))
    data = ReactionLibraryFile.model_validate(raw)
    lib = ReactionLibrary(data)

    failures = 0
    print(f"校验反应库：{p}（{len(data.reactions)} 条）\n")

    for rxn in data.reactions:
        print(f"· {rxn.id}（{rxn.name}）[status={rxn.status}]")
        r_mol = Chem.MolFromSmiles(rxn.reactant_smiles)
        p_mol = Chem.MolFromSmiles(rxn.product_smiles)
        if r_mol is None or p_mol is None:
            print(f"    [失败] SMILES 解析失败")
            failures += 1
            continue

        # 映射号唯一性
        r_maps = [a.GetAtomMapNum() for a in r_mol.GetAtoms() if a.GetAtomMapNum()]
        p_maps = [a.GetAtomMapNum() for a in p_mol.GetAtoms() if a.GetAtomMapNum()]
        if len(r_maps) != len(set(r_maps)) or len(p_maps) != len(set(p_maps)):
            print("    [失败] 映射号在反应物或产物中出现重复")
            failures += 1
            continue

        # 映射原子元素一致性
        r_elem = _elements_by_map(r_mol)
        p_elem = _elements_by_map(p_mol)
        elem_bad = [
            m for m in set(r_elem) & set(p_elem) if r_elem[m] != p_elem[m]
        ]
        if elem_bad:
            print(f"    [失败] 映射原子元素不一致：{elem_bad}")
            failures += 1
            continue

        # 推导断键/成键
        r_orders = _bond_orders_by_map(r_mol)
        p_orders = _bond_orders_by_map(p_mol)
        derived_breaking = {k for k in r_orders if k not in p_orders}
        derived_forming = {k for k in p_orders if k not in r_orders}

        ann_breaking = {tuple(sorted(pair)) for pair in rxn.breaking_bonds}
        ann_forming = {tuple(sorted(pair)) for pair in rxn.forming_bonds}
        if derived_breaking != ann_breaking:
            print(f"    [失败] 断键推导 {sorted(derived_breaking)} ≠ 标注 {sorted(ann_breaking)}")
            failures += 1
        if derived_forming != ann_forming:
            print(f"    [失败] 成键推导 {sorted(derived_forming)} ≠ 标注 {sorted(ann_forming)}")
            failures += 1

        # 实跑帧生成
        try:
            mech = generate_mechanism(rxn, frame_count=24)
            n0 = len(mech.frames[0].atoms)
            n1 = len(mech.frames[-1].atoms)
            if n0 != n1:
                print(f"    [失败] 首末帧原子数不对齐：{n0} vs {n1}")
                failures += 1
            else:
                print(
                    f"    [通过] 推导断键 {sorted(derived_breaking)} / 成键 "
                    f"{sorted(derived_forming)}；帧生成 {mech.frame_count} 帧，"
                    f"原子 {n0} 个，映射 {len(mech.atom_map)} 对"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"    [失败] 帧生成异常：{type(exc).__name__} {exc}")
            failures += 1

    print()
    if failures:
        print(f"[结果] {failures} 条存在校验失败")
        return 1
    print("[结果] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
