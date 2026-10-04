/**
 * viz 模块的测试。
 *
 * ## 重点：几何计算必须可独立测试
 *
 * Three.js 在 happy-dom 里**无法真正渲染**（无 WebGL），
 * 但几何计算（中心、跨度、键数）是纯函数——
 * 这部分必须测，因为它们直接决定画面是否正确：
 * 中心算错 → 分子偏出画面；键数算错 → 双键画成单键。
 *
 * ## 不测什么
 *
 * 不测「渲染出的像素是否正确」——那需要 WebGL 环境与
 * 截图比对，脆弱且难维护。真实渲染由 `e2e/` 的浏览器验证覆盖。
 */

import { describe, expect, it } from 'vitest'

import {
  BALL_SCALE,
  BOND_RADIUS,
  CPK_COLORS,
  bondCylinderCount,
  colorOf,
  elementName,
} from '../src/viz/elements'
import { computeCenter, computeExtent } from '../src/viz/viewer'
import { validateVizData } from '../src/viz/types'
import type { RenderAtomV2, RenderBondV2 } from '../src/viz/types'

// --- 构造符合后端契约的假数据（形状取自 test_render_data.py 的实测）---

function makeAtom(over: Partial<RenderAtomV2> = {}): RenderAtomV2 {
  return {
    index: 0,
    element: 'C',
    atomic_number: 6,
    radius: 0.76,
    outer_electrons: 4,
    formal_charge: 0,
    attached_hydrogens: 0,
    is_aromatic: false,
    is_explicit_hydrogen: false,
    ...over,
  }
}

function makeViz(
  atoms: RenderAtomV2[],
  bonds: RenderBondV2[],
  coords: [number, number, number][],
  extra: Record<string, unknown> = {},
) {
  return {
    schema: 'molecule-structure/v2',
    smiles: 'CCO',
    atom_count: atoms.length,
    bond_count: bonds.length,
    conformer: 'ready',
    conformer_note: '',
    coords,
    render_atoms: atoms,
    render_bonds: bonds,
    has_explicit_hydrogens: true,
    ...extra,
  }
}

describe('CPK 配色', () => {
  it('碳用中灰而非纯黑（可读性考虑）', () => {
    // 纯黑 #000000 的球在深色背景下完全看不见
    expect(CPK_COLORS.C).toBe(0x909090)
  })

  it('氧是红色、氮是蓝色（CPK 惯例）', () => {
    expect(CPK_COLORS.O).toBe(0xff0d0d)
    expect(CPK_COLORS.N).toBe(0x3050f8)
  })

  it('氢是白色', () => {
    expect(CPK_COLORS.H).toBe(0xffffff)
  })

  it('高中常见元素都已登记', () => {
    for (const el of ['H', 'C', 'N', 'O', 'S', 'P', 'Cl', 'Br']) {
      expect(CPK_COLORS[el], `${el} 未登记`).toBeDefined()
    }
  })

  it('未登记元素回落到兜底色而非 undefined', () => {
    // undefined 传给 MeshStandardMaterial.color 会得到黑色，
    // 看起来像"这个原子不存在"——比粉色更容易误导
    expect(colorOf('Xx')).toBe(0xff69b4)
  })

  it('元素中文名覆盖高中范围', () => {
    expect(elementName('C')).toBe('碳')
    expect(elementName('Cl')).toBe('氯')
    // 未登记时返回符号本身，不返回空串
    expect(elementName('Xx')).toBe('Xx')
  })
})

describe('球棍模型几何参数', () => {
  it('球半径小于键长，否则键看不见', () => {
    // 实测 C–C 键长1.52 Å
    const ccRadius = CPK_COLORS.C // 只是取个常量用
    expect(ccRadius).toBeGreaterThan(0)
    expect(0.76 * BALL_SCALE).toBeLessThan(1.52)
  })

  it('键比球细得多（这是球棍与填充模型的区别）', () => {
    expect(BOND_RADIUS).toBeLessThan(0.76 * BALL_SCALE)
  })
})

describe('bondCylinderCount', () => {
  it('单键一根圆柱', () => {
    expect(bondCylinderCount(1, false)).toBe(1)
  })

  it('双键两根', () => {
    expect(bondCylinderCount(2, false)).toBe(2)
  })

  it('三键三根', () => {
    expect(bondCylinderCount(3, false)).toBe(3)
  })

  it('芳香键只画一根（离域用额外标记表达）', () => {
    // 1.5 若四舍五入成 2 会画成双键——那不是苯环该有的样子
    expect(bondCylinderCount(1.5, true)).toBe(1)
  })

  it('异常键级被钳在 1..3，不会产生 0 根或 5 根', () => {
    expect(bondCylinderCount(0, false)).toBe(1)
    expect(bondCylinderCount(9, false)).toBe(3)
  })
})

describe('几何计算', () => {
  it('中心是各原子的平均位置', () => {
    const coords: [number, number, number][] = [
      [0, 0, 0],
      [2, 0, 0],
      [0, 2, 0],
      [0, 0, 2],
    ]
    const c = computeCenter(coords)
    expect(c.x).toBeCloseTo(0.5)
    expect(c.y).toBeCloseTo(0.5)
    expect(c.z).toBeCloseTo(0.5)
  })

  it('空坐标返回原点而非崩溃', () => {
    const c = computeCenter([])
    expect(c.x).toBe(0)
    expect(c.y).toBe(0)
    expect(c.z).toBe(0)
    expect(computeExtent([], c)).toBe(0)
  })

  it('单原子分子的跨度为 0（会导致除零）', () => {
    const coords: [number, number, number][] = [[1, 1, 1]]
    expect(computeExtent(coords, computeCenter(coords))).toBe(0)
  })

  it('跨度是最远原子距离的两倍', () => {
    // 线性三原子：0,1,2 → 中心 1，最远距离 1，跨度 2
    const coords: [number, number, number][] = [
      [0, 0, 0],
      [1, 0, 0],
      [2, 0, 0],
    ]
    expect(computeExtent(coords, computeCenter(coords))).toBeCloseTo(2, 5)
  })
})

describe('validateVizData', () => {
  const atoms = [makeAtom({ index: 0 }), makeAtom({ index: 1, element: 'O' })]
  const bonds: RenderBondV2[] = [{ begin: 0, end: 1, order: 1, is_aromatic: false }]
  const coords: [number, number, number][] = [
    [0, 0, 0],
    [1.5, 0, 0],
  ]

  it('数据完好时通过', () => {
    const r = validateVizData(makeViz(atoms, bonds, coords))
    expect('error' in r).toBe(false)
  })

  it('conformer=failed 时给出可读原因', () => {
    const bad = { ...makeViz(atoms, bonds, coords), conformer: 'failed' as const, conformer_note: '生成失败' }
    const r = validateVizData(bad)
    expect('error' in r).toBe(true)
    if ('error' in r) expect(r.error).toContain('生成失败')
  })

  it('坐标数与原子数不一致时报错（索引错位会让原子飘）', () => {
    const bad = makeViz(atoms, bonds, [[0, 0, 0]]) // 只有 1 个坐标但 2 个原子
    const r = validateVizData(bad)
    expect('error' in r).toBe(true)
    if ('error' in r) expect(r.error).toContain('不一致')
  })

  it('坐标含 NaN 时报错（WebGL 会静默不渲染）', () => {
    const bad = makeViz(atoms, bonds, [
      [Number.NaN, 0, 0],
      [1.5, 0, 0],
    ])
    const r = validateVizData(bad)
    expect('error' in r).toBe(true)
    if ('error' in r) expect(r.error).toContain('非法数值')
  })

  it('坐标含 Infinity 时报错', () => {
    const bad = makeViz(atoms, bonds, [
      [Number.POSITIVE_INFINITY, 0, 0],
      [1.5, 0, 0],
    ])
    expect('error' in validateVizData(bad)).toBe(true)
  })

  it('坐标维度不为 3 时报错', () => {
    const bad = makeViz(atoms, bonds, [
      [0, 0] as unknown as [number, number, number],
      [1.5, 0, 0],
    ])
    expect('error' in validateVizData(bad)).toBe(true)
  })

  it('数据为 null 时报错而非崩溃', () => {
    expect('error' in validateVizData(null)).toBe(true)
    expect('error' in validateVizData(undefined)).toBe(true)
  })

  it('缺 render_bonds 时报错', () => {
    const bad = makeViz(atoms, bonds, coords)
    delete (bad as Partial<typeof bad>).render_bonds
    expect('error' in validateVizData(bad)).toBe(true)
  })
})
