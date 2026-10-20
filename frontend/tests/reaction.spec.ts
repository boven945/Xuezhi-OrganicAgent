/**
 * 反应机理播放器的**契约一致性**与**几何插值**测试。
 *
 * ## 为什么这个文件存在
 *
 * 反应动画端点有两个失败路径（实测），前端必须都接住，
 * 否则要么白屏、要么把"化学引擎不可用"误显成"回答加载中"：
 *
 * 1. HTTP 200 + `ok=false`（如 `reaction_not_found`）：`request()` 返回信封，
 *    前端据 `resp.ok` 决定退回文本讲解——**不抛**；
 * 2. HTTP 503（`api_component_unavailable`，RDKit 缺失）：`request()`
 *    **抛** `ApiRequestError`——前端据 `code` 提示"化学引擎暂不可用"。
 *
 * 这两点与 `HealthBadge` 契约测试同口径：**用后端真实信封/状态码测**，
 * 而不是自造一组"看起来对"的值。
 *
 * ## 几何插值必须可独立测
 *
 * 与 `viz.spec.ts` 同口径：Three.js 在 happy-dom 里无法真渲染，
 * 但帧间线性插值（原子坐标、键透明度）是纯函数——
 * 这部分直接决定动画对不对（淡入淡出是不是反了、中点是不是对齐），
 * 必须单测。真实渲染由 `e2e/` 浏览器验证覆盖。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchReactionMechanism } from '../src/api/client'
import {
  bondKey,
  interpolateAtomPositions,
  interpolateBondOpacities,
} from '../src/viz/ReactionPlayer'
import type { MechanismFrame } from '../src/types/api'

// --- 造符合后端契约的假数据 ---

function atom(index: number, x: number, y = 0, z = 0): MechanismFrame['atoms'][number] {
  return { index, element: 'C', x, y, z }
}

function bond(
  a: number,
  b: number,
  opacity: number,
  state: MechanismFrame['bonds'][number]['state'] = 'intact',
): MechanismFrame['bonds'][number] {
  return { a, b, order: 1, state, opacity }
}

function frame(
  t: number,
  atoms: MechanismFrame['atoms'],
  bonds: MechanismFrame['bonds'],
): MechanismFrame {
  return { t, atoms, bonds, highlight_atoms: [] }
}

// ===========================================================================
// 契约：fetchReactionMechanism 的信封/降级处理
// ===========================================================================

function mockFetch(body: unknown, opts: { ok: boolean; status: number }) {
  const res = {
    ok: opts.ok,
    status: opts.status,
    json: async () => body,
  }
  return vi.fn(async () => res)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('fetchReactionMechanism 信封处理', () => {
  it('200 + ok=true 时返回 data', async () => {
    const data = {
      schema: 'reaction-mechanism/v1',
      reaction_id: 'r',
      name: 'R',
      transition_note: 'tn',
      highlight_note: 'hn',
      frame_count: 2,
      fps: 12,
      atom_map: {},
      frames: [frame(0, [atom(0, 0)], [bond(0, 1, 1)])],
    }
    vi.stubGlobal('fetch', mockFetch({ request_id: 'x', ok: true, data }, { ok: true, status: 200 }))
    const resp = await fetchReactionMechanism('r')
    expect(resp.ok).toBe(true)
    expect(resp.data?.reaction_id).toBe('r')
    expect(resp.data?.transition_note).toBe('tn')
  })

  it('200 + ok=false（reaction_not_found）时返回信封而不抛', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(
        {
          request_id: 'x',
          ok: false,
          error: { code: 'reaction_not_found', message: '未收录', retryable: false, request_id: 'x' },
          data: null,
        },
        { ok: true, status: 200 },
      ),
    )
    const resp = await fetchReactionMechanism('missing')
    // **关键**：信封返回，前端据 ok=false 退回文本讲解，不抛异常
    expect(resp.ok).toBe(false)
    expect(resp.error?.code).toBe('reaction_not_found')
    expect(resp.data).toBeNull()
  })

  it('503（api_component_unavailable）时抛 ApiRequestError', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(
        {
          error: {
            code: 'api_component_unavailable',
            message: '化学引擎暂不可用',
            retryable: true,
            request_id: '',
          },
        },
        { ok: false, status: 503 },
      ),
    )
    await expect(fetchReactionMechanism('r')).rejects.toMatchObject({
      code: 'api_component_unavailable',
    })
  })
})

// ===========================================================================
// 几何插值（纯函数，不需要 WebGL）
// ===========================================================================

describe('bondKey', () => {
  it('规范键把小 index 放前面', () => {
    expect(bondKey(0, 1)).toBe('0-1')
    expect(bondKey(1, 0)).toBe('0-1')
    expect(bondKey(3, 2)).toBe('2-3')
  })
})

describe('interpolateAtomPositions', () => {
  const frames = [
    frame(0, [atom(0, 0), atom(1, 0)], []),
    frame(1, [atom(0, 2), atom(1, 4)], []),
  ]

  it('进度 0 取首帧坐标', () => {
    const m = interpolateAtomPositions(frames, 0)
    expect(m.get(0)).toEqual([0, 0, 0])
    expect(m.get(1)).toEqual([0, 0, 0])
  })

  it('进度末取末帧坐标', () => {
    const m = interpolateAtomPositions(frames, 1)
    expect(m.get(0)).toEqual([2, 0, 0])
    expect(m.get(1)).toEqual([4, 0, 0])
  })

  it('进度中点取两帧线性平均', () => {
    const m = interpolateAtomPositions(frames, 0.5)
    expect(m.get(0)).toEqual([1, 0, 0])
    expect(m.get(1)).toEqual([2, 0, 0])
  })

  it('某帧缺失该原子时回退到相邻帧坐标，不报错', () => {
    // 原子 1 只在末帧出现：进度 0 时回退到末帧坐标（持久网格不会飘）
    const asymmetric = [
      frame(0, [atom(0, 0)], []),
      frame(1, [atom(0, 0), atom(1, 5)], []),
    ]
    const m = interpolateAtomPositions(asymmetric, 0)
    expect(m.get(1)).toEqual([5, 0, 0])
  })
})

describe('interpolateBondOpacities', () => {
  // 成键 (0,1)：首帧透明、末帧不透明
  const forming = [
    frame(0, [], [bond(0, 1, 0, 'forming')]),
    frame(1, [], [bond(0, 1, 1, 'forming')]),
  ]
  // 断键 (0,1)：首帧不透明、末帧透明
  const breaking = [
    frame(0, [], [bond(0, 1, 1, 'breaking')]),
    frame(1, [], [bond(0, 1, 0, 'breaking')]),
  ]

  it('成键：0→1，中点 0.5', () => {
    expect(interpolateBondOpacities(forming, 0).get('0-1')).toBeCloseTo(0, 6)
    expect(interpolateBondOpacities(forming, 1).get('0-1')).toBeCloseTo(1, 6)
    expect(interpolateBondOpacities(forming, 0.5).get('0-1')).toBeCloseTo(0.5, 6)
  })

  it('断键：1→0，中点 0.5（淡出方向不能反）', () => {
    expect(interpolateBondOpacities(breaking, 0).get('0-1')).toBeCloseTo(1, 6)
    expect(interpolateBondOpacities(breaking, 1).get('0-1')).toBeCloseTo(0, 6)
    expect(interpolateBondOpacities(breaking, 0.5).get('0-1')).toBeCloseTo(0.5, 6)
  })

  it('(a,b) 与 (b,a) 视为同一根键', () => {
    const m = interpolateBondOpacities(forming, 0.5)
    // 规范键保证 (a,b) 与 (b,a) 落到同一个 map 键
    expect(bondKey(1, 0)).toBe(bondKey(0, 1))
    expect(m.get(bondKey(1, 0))).toBeCloseTo(0.5, 6)
  })
})
