/**
 * 3D 渲染数据的类型定义。
 *
 * 权威来源：后端 `app/chem/engine.py` 的 `_build_atoms_and_bonds`
 * 与 `_embed_conformer`，schema 版本 `molecule-structure/v2`。
 *
 * 核对方式（后端可跑时）：
 *
 * ```bash
 * PYTHONPATH=backend python -c "
 *   import json
 *   from app.chem.engine import get_engine
 *   print(json.dumps(get_engine().parse('CCO').structure.viz_data, ensure_ascii=False))
 * "
 * ```
 *
 * 契约由 `backend/tests/chem/test_render_data.py` 的24 项测试锁定
 * （索引一一对应、键索引在范围内、坐标为有限数字）。
 */

/**
 * 构象生成状态。
 *
 * `failed` 不是异常路径——坐标是增强信息，
 * 生成失败时学生仍应能看到结构式与官能团。
 */
export type ConformerStatus = 'ready' | 'failed'

/** 单个原子（**化学视角**，不含显式氢）。 */
export interface Atom {
  index: number
  element: string
  atomic_number: number
  /** 共价半径（Å），取自 RDKit 周期表。 */
  radius: number
  /** 外层电子数——学生判断成键数的依据。 */
  outer_electrons: number
  formal_charge: number
  /** 隐式氢数（不含显式氢）。乙醇的 C 是 3/2/1。 */
  attached_hydrogens: number
  is_aromatic: boolean
}

/** 单个键（化学视角）。 */
export interface Bond {
  begin: number
  end: number
  /**
   * 键级：1 / 2 / 1.5（芳香）。
   *
   * **用 float 而非 int 是刻意的**：芳香键的 1.5 若被截断为 1，
   * 前端画不出交替单双键——那是高中必考的结构特征。
   */
  order: number
  is_aromatic: boolean
}

/**
 * 渲染用原子（**含显式氢**）。
 *
 * 与 {@link Atom} 的差异有两处，都是后端刻意为之：
 * -多了 ``is_explicit_hydrogen``——用来区分「骨架原子」与「为教学补的 H」；
 * - ``attached_hydrogens`` 恒为 0——氢已显式化，不存在「隐式氢」。
 */
export interface RenderAtomV2 {
  index: number
  element: string
  atomic_number: number
  radius: number
  outer_electrons: number
  formal_charge: number
  attached_hydrogens: number
  is_aromatic: boolean
  is_explicit_hydrogen: boolean
}

export interface RenderBondV2 {
  begin: number
  end: number
  order: number
  is_aromatic: boolean
}

/** 后端 `viz_data` 的完整形状。 */
export interface VizData {
  schema: string
  smiles: string
  atom_count: number
  bond_count: number
  conformer: ConformerStatus
  conformer_note: string
  /** 化学视角（不含显式氢）。 */
  atoms?: Atom[]
  bonds?: Bond[]
  /** 三维坐标 [x, y, z]，单位 Å。与 render_atoms 索引一一对应。 */
  coords?: [number, number, number][]
  render_atoms?: RenderAtomV2[]
  render_bonds?: RenderBondV2[]
  has_explicit_hydrogens?: boolean
}

/**
 * 校验渲染数据是否可用。
 *
 * ## 为什么需要显式校验而不是直接信任类型
 *
 * TypeScript 的类型在**运行时不存在**——后端若返回缺字段的
 * JSON，`coords.map(...)` 会抛 ``Cannot read properties of undefined``，
 * 整页白屏。故渲染前必须校验。
 *
 * @returns 可渲染的原因；`null` 表示数据完好
 */
export function validateVizData(
  viz: unknown,
): { atoms: RenderAtomV2[]; bonds: RenderBondV2[]; coords: [number, number, number][] } | { error: string } {
  if (typeof viz !== 'object' || viz === null) {
    return { error: '结构数据缺失。' }
  }
  const v = viz as VizData

  if (v.conformer !== 'ready') {
    return {
      error: v.conformer_note ?? '该结构没有三维坐标，无法渲染球棍模型。',
    }
  }
  if (!Array.isArray(v.coords) || !Array.isArray(v.render_atoms) || !Array.isArray(v.render_bonds)) {
    return { error: '三维坐标数据不完整，无法渲染。' }
  }
  // 索引错位不会报错，只会让原子飘到错误位置——须在此拦住
  if (v.coords.length !== v.render_atoms.length) {
    return {
      error: `坐标数（${v.coords.length}）与原子数（${v.render_atoms.length}）不一致，数据可能有误。`,
    }
  }
  // NaN/Infinity 会让 WebGL 静默不渲染（画面空白但不报错）
  for (const xyz of v.coords) {
    if (!Array.isArray(xyz) || xyz.length !== 3 || !xyz.every((n) => Number.isFinite(n))) {
      return { error: '坐标包含非法数值，无法渲染。' }
    }
  }

  return {
    atoms: v.render_atoms,
    bonds: v.render_bonds,
    coords: v.coords,
  }
}
