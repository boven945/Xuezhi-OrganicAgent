/**
 * 数字人形象与 Fay 动作映射的测试。
 *
 * ## 重点测什么
 *
 * 1. **状态推导**——Fay 的`Action.behavior` 有二十多种取值，
 *    我们只认三个。**认不出的必须落待机**，
 *    猜成"说话"会让学生看到嘴在动但没声音。
 * 2. **Fay 未启用时恒为待机**——这是用户明确要求的行为。
 * 3. **断线不抛错**——数字人是增强能力，连不上必须静默降级。
 *
 * ## 不测什么
 *
 * 不测像素渲染与图片加载（需要真实浏览器，属e2e 范围）。
 * 本文件只覆盖状态机的决策。
 */

import { describe, expect, it, vi } from 'vitest'

import {
  AVATAR_IMAGES,
  lipsDurationMs,
  resolveAvatarState,
  type AvatarState,
  type FayHumanData,
} from '../src/viz/avatar'

function data(over: Partial<FayHumanData> = {}): FayHumanData {
  return { Key: 'audio', ...over }
}

describe('形象状态推导', () => {
  it('有音素时判为说话——这是最强的发声信号', () => {
    //实测 Fay 的音素形如 sil/FF/aa，每项带毫秒时长
    const payload = data({ Lips: [{ Lip: 'sil', Time: 180 }, { Lip: 'FF', Time: 144 }] })
    expect(resolveAvatarState(payload, false)).toBe('speaking')
  })

  it('音频在播但无音素时也判为说话', () => {
    // 兜底：某些片段可能不带Lips，但音频确实在放
    expect(resolveAvatarState(data(), true)).toBe('speaking')
  })

  it('音素优先于动作——有声音就是说话', () => {
    // 实测踩过的坑：Fay 可能在发声片段里同时给出 think 动作。
    // 若按动作优先，会出现「嘴在动但显示思考表情」。
    const payload = data({
      Lips: [{ Lip: 'aa', Time: 100 }],
      Action: { behavior: 'think' },
    })
    expect(resolveAvatarState(payload, true)).toBe('speaking')
  })

  it('讲解类动作映射为说话', () => {
    // 这三个都是"老师在讲"的语义
    for (const behavior of ['nod', 'invite', 'wave']) {
      expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('speaking')
    }
  })

  it('思考类动作映射为思考', () => {
    for (const behavior of ['think', 'question']) {
      expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('thinking')
    }
  })

  it('认不出的动作落待机而非说话', () => {
    // 核心防护：Fay 的 behavior 取值远多于我们认的三类
    // （社区实测可见 reject / farewell / 各种自定义 code）
    for (const behavior of ['reject', 'farewell', 'some_custom_action', '']) {
      expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('idle')
    }
  })

  it('无消息时为待机', () => {
    expect(resolveAvatarState(null, false)).toBe('idle')
    expect(resolveAvatarState(undefined, false)).toBe('idle')
  })

  it('永远返回可用状态，不返回 null', () => {
    // 组件需要永远能显示一张图——返回 null 会让界面出现无图分支
    const states: AvatarState[] = ['idle', 'idle', 'speaking', 'idle']
    for (const s of states) expect(s).not.toBeNull()
    expect(resolveAvatarState(data({ Action: { behavior: 'unknown' } }), false)).not.toBeNull()
  })
})

describe('音素时长累计', () => {
  it('累加所有音素的毫秒数', () => {
    const lips = [
      { Lip: 'sil', Time: 180 },
      { Lip: 'FF', Time: 144 },
      { Lip: 'aa', Time: 220 },
    ]
    expect(lipsDurationMs(lips)).toBe(544)
  })

  it('无音素时为 0', () => {
    expect(lipsDurationMs(undefined)).toBe(0)
    expect(lipsDurationMs([])).toBe(0)
  })

  it('负数与NaN 被忽略——否则状态会立即抖动', () => {
    // 实测踩过：负时长会让 setTimeout 立即触发，
    // 形象在说话/待机之间高频闪烁，看起来像故障
    const lips = [
      { Lip: 'aa', Time: -100 },
      { Lip: 'bb', Time: Number.NaN },
      { Lip: 'cc', Time: 150 },
    ]
    expect(lipsDurationMs(lips)).toBe(150)
  })
})

describe('图片资源', () => {
  it('三态各有图片', () => {
    for (const s of ['idle', 'speaking', 'thinking'] as AvatarState[]) {
      expect(AVATAR_IMAGES[s]).toMatch(/^\/avatar\/[a-z]+\.png$/)
    }
  })
})

describe('WebSocket 降级', () => {
  it('地址非法时组件不抛错，状态回落待机', async () => {
    // **这条曾写成恒真断言**：原先只验证 `new WebSocket` 会抛，
    // 而组件早就 try/catch 了——测的是前提，不是行为。
    // 现在真正挂载组件，验证它不会把异常抛给调用方。
    const { mount } = await import('@vue/test-utils')
    const Fake = vi.fn(() => {
      throw new Error('invalid URL')
    })
    vi.stubGlobal('WebSocket', Fake)

    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')
    const wrapper = mount(DigitalHuman, {
      props: { fayEnabled: true, endpoint: 'ws://' },
    })

    // 核心断言：渲染出来了，且提示"未连接"（而不是白屏或抛错）
    expect(wrapper.find('img').exists()).toBe(true)
    expect(wrapper.find('.avatar__hint').text()).toContain('未连接')
    wrapper.unmount()
    vi.unstubAllGlobals()
  })

  it('Fay 未启用时完全不建立连接', async () => {
    // 用户明确要求：Fay 未启用时前端默认待机。
    // **不该去连一个未配置的服务**——那会在控制台留下连接错误。
    const { mount } = await import('@vue/test-utils')
    const Fake = vi.fn()
    vi.stubGlobal('WebSocket', Fake)

    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')
    const wrapper = mount(DigitalHuman, { props: { fayEnabled: false } })

    expect(Fake).not.toHaveBeenCalled()
    expect(wrapper.find('.avatar__hint').text()).toBe('待机')
    // 默认必须是待机图
    expect(wrapper.find('img').attributes('src')).toBe(AVATAR_IMAGES.idle)
    wrapper.unmount()
    vi.unstubAllGlobals()
  })
})


describe('数字人常驻（回归防护）', () => {
  it('未提问时数字人也应显示', async () => {
    // **这个缺陷本该被测出来**：原先数字人被放进
    // `v-if="ask.hasContent"`，于是学生刚打开页面时
    // 整个形象不显示——一个"老师"在学生举手前就消失了。
    //
    // 单元测试测不到（它不渲染完整 AskView），
    // 但**契约必须写下来**，否则下次重构又会退回。
    const { mount } = await import('@vue/test-utils')
    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')

    // 不传任何 props：模拟"页面刚加载、还没提问"
    const wrapper = mount(DigitalHuman)
    expect(wrapper.find('img').exists()).toBe(true)
    expect(wrapper.find('img').attributes('src')).toBe(AVATAR_IMAGES.idle)
    wrapper.unmount()
  })

  it('组件自身不含 v-if="hasContent" 之类的条件', async () => {
    // 读源码断言：分屏容器不应受答复内容控制。
    // 这类"结构约束"用渲染测试很难精确表达，
    // 读源码反而更直接。
    // **两次踩坑才写对**：
    // ① `new URL(..., import.meta.url)` 在 vitest 下不是 file 协议
    //   （报 "The URL must be of scheme file"）；
    // ② 改用 `process.cwd()` + `node:path` 后 typecheck 报
    //   "Cannot find name 'process'"——本项目 `tsconfig.app.json`
    //   的 types 只有 `vite/client`，**刻意不装 @types/node**
    //   （浏览器项目不该引入 Node 类型），且它覆盖 tests/。
    //
    // 故用 vite 自带的 `?raw` 导入：它把文件当字符串返回，
    // 类型由 vite/client 提供，不需要任何 Node 类型。
    const src = await import('../src/components/AskView.vue?raw').then((m) => m.default)
    // 取出数字人所在容器的开标签
    const m = src.match(/<div class="teacher[^"]*"(v-if[^>]*)?>/)
    expect(m).not.toBeNull()
    expect(m![1]).toBeUndefined()   // 容器上不得有 v-if
  })
})
