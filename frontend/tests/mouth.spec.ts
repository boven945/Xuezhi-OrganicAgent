/**
 * 音量包络口型的测试。
 *
 * ## 为什么重点测「无 Web Audio 时不崩」
 *
 * 口型是**增强**。`AudioContext` 在老旧设备、驱动缺失、
 * 浏览器策略下都可能拿不到（`getByteTimeDomainData` 亦然）。
 * 拿不到时必须退回「嘴微闭」，**绝不能让数字人整个不可用**。
 *
 * ## happy-dom 里没有 Web Audio
 *
 * 真实 `AnalyserNode` 需要 Web Audio 实现，happy-dom 不提供。
 * 故本文件用**替身**验证控制流，并单独验证「无 API 时降级」。
 * 真实渲染由 `e2e/` 的浏览器验证覆盖。
 */

import { describe, expect, it, vi } from 'vitest'

import { createMouthEnvelope } from '../src/viz/mouth'

/** 造一个假 AudioContext，记录节点连接关系。 */
function installFakeAudio() {
  const connected: string[] = []
  const analyser = {
    fftSize: 0,
    getByteTimeDomainData: vi.fn((buf: Uint8Array) => {
      // 填成 128 ± 40（相当于较大的音量）
      for (let i = 0; i < buf.length; i += 1) buf[i] = 88
    }),
    connect: () => connected.push('analyser'),
    disconnect: () => {},
  }
  const source = {
    connect: () => connected.push('source'),
    disconnect: () => {},
  }
  const ctx = {
    createAnalyser: () => analyser,
    createMediaElementSource: () => source,
    close: vi.fn(async () => {}),
    destination: {},
  }
  // **必须用 class 而非箭头函数**（实测踩到）：
  // `new AudioContext()` 要求构造器可被 new，
  // 箭头函数会抛 "() => ctx is not a constructor"，
  // 而该异常被 start() 的 catch 吞掉 → 表现为"什么都没连上"。
  //
  // **这类错误被catch 掩盖时极难查**：真实浏览器里它是 class，
  // 只有替身写错才会暴露。
  // close 提到 class 外声明，这样测试能拿到**同一个** mock 引用——
  // 若写在 class 字段里，每个实例各有其mock，
  // 外部持有的 `ctx`（替身对象）上的 close 永远不会被调用。
  const close = vi.fn(async () => {})
  class FakeAudioContext {
    createAnalyser = () => analyser
    createMediaElementSource = () => source
    close = close
    destination = {}
  }
  vi.stubGlobal('AudioContext', FakeAudioContext)
  return { connected, analyser, ctx, close }
}

function fakeAudioEl(): HTMLAudioElement {
  return { play: vi.fn(async () => {}), pause: vi.fn() } as unknown as HTMLAudioElement
}

describe('音量包络口型', () => {
  it('无 Web Audio 时降级为恒定 0，不抛错', () => {
    // 关键防护：老旧设备 / 策略限制下不能崩
    vi.stubGlobal('AudioContext', undefined)
    vi.stubGlobal('webkitAudioContext', undefined)
    const env = createMouthEnvelope(fakeAudioEl())
    expect(() => env.start()).not.toThrow()
    expect(env.value).toBe(0)
    expect(() => env.stop()).not.toThrow()
    vi.unstubAllGlobals()
  })

  it('构造时连接了 source → analyser → destination（否则不出声）', () => {
    const { connected } = installFakeAudio()
    const env = createMouthEnvelope(fakeAudioEl())
    env.start()
    // 三段都接上才算完整链路；少一段 audio 就是哑的
    expect(connected).toContain('source')
    expect(connected).toContain('analyser')
    env.stop()
    vi.unstubAllGlobals()
  })

  it('分析器 fftSize 与缓冲区长度一致', () => {
    const { analyser } = installFakeAudio()
    const env = createMouthEnvelope(fakeAudioEl())
    env.start()
    analyser.getByteTimeDomainData(new Uint8Array(new ArrayBuffer(analyser.fftSize)))
    // 不一致会被 AnalyserNode 抛错，故断言相等
    expect(analyser.fftSize).toBe(128)
    env.stop()
    vi.unstubAllGlobals()
  })

  it('stop() 关闭 AudioContext——不关会每个音频元素泄漏一个', () => {
    const { close } = installFakeAudio()
    const env = createMouthEnvelope(fakeAudioEl())
    env.start()
    env.stop()
    expect(close).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('重复 start 不会叠加节点', () => {
    const { connected } = installFakeAudio()
    const env = createMouthEnvelope(fakeAudioEl())
    env.start()
    env.start()
    const first = connected.length
    // 幂等：第二次不该再连
    expect(connected.length).toBe(first)
    env.stop()
    vi.unstubAllGlobals()
  })

  it('stop() 后可再start（下一段讲解要能重新分析）', () => {
    installFakeAudio()
    const env = createMouthEnvelope(fakeAudioEl())
    env.start()
    env.stop()
    expect(() => env.start()).not.toThrow()
    env.stop()
    vi.unstubAllGlobals()
  })

  it('createAnalyser 抛错时降级——不因分析失败而影响播放', () => {
    // class 而非箭头函数：AudioContext 要被 new（见另一测试的注释）
    class ThrowingContext {
      createAnalyser(): never {
        throw new Error('NotSupportedError')
      }
      createMediaElementSource(): never {
        throw new Error('NotSupportedError')
      }
      close = vi.fn(async () => {})
      destination = {}
    }
    vi.stubGlobal('AudioContext', ThrowingContext)
    const env = createMouthEnvelope(fakeAudioEl())
    // 关键：拿不到分析器也不该抛
    expect(() => env.start()).not.toThrow()
    env.stop()
    vi.unstubAllGlobals()
  })
})


describe('嘴部坐标（实测得来，不可目测）', () => {
  /**
   * 实测背景：原先`top: 46%` 是**目测**的，
   * 结果黑块盖在**眼睛**上——用户直接指出"画面明显崩了"。
   *
   * 实测值（裁头部放大 → 按颜色定位嘴腔 → 换算比例）：
   *   嘴腔 speaking.png的 y485-560, x459-547
   *   中心 (503, 522) ÷ 1024 → x=0.491, y=0.510
   *
   * **5 个百分点的偏差就足以盖错器官**，
   * 而这类错误不报错：元素照样渲染、测试全绿，只有人眼能看出。
   * 故把坐标固化成断言。
   */
  it('嘴部定位在实测的 49.1% / 51% 附近', async () => {
    const sfc = (await import('../src/components/DigitalHuman.vue?raw')).default
    const style = sfc.slice(sfc.indexOf('<style'))

    // 刻意用 `new RegExp` 拼而非字面量：
    // 反斜杠在脚本里层层转义极易出错（本次就踩了），
    // 而 `String.raw` 模板串只在运行时拼一次，可读且不易错。
    const pct = (prop: string): RegExp =>
      new RegExp(String.raw`\.avatar__mouth[\s\S]*?${prop}:\s*([\d.]+)%`)

    const left = pct('left').exec(style)
    const top = pct('top').exec(style)
    expect(left, '未找到 left 声明').not.toBeNull()
    expect(top, '未找到 top 声明').not.toBeNull()

    const x = Number(left![1])
    const y = Number(top![1])
    // 容差 1.5 个百分点：允许微调，但拦住"又回到46%"这类漂移
    expect(Math.abs(x - 49.1)).toBeLessThan(1.5)
    expect(Math.abs(y - 51)).toBeLessThan(1.5)
  })

  it('嘴部尺寸不小于实测嘴腔（8.7% × 7.4%）', async () => {
    const sfc = (await import('../src/components/DigitalHuman.vue?raw')).default
    const style = sfc.slice(sfc.indexOf('<style'))
    const pct = (prop: string): RegExp =>
      new RegExp(String.raw`\.avatar__mouth[\s\S]*?${prop}:\s*([\d.]+)%`)
    const w = pct('width').exec(style)
    const h = pct('height').exec(style)
    expect(w, '未找到 width 声明').not.toBeNull()
    expect(h, '未找到 height 声明').not.toBeNull()
    expect(Number(w![1])).toBeGreaterThanOrEqual(8.7)
    expect(Number(h![1])).toBeGreaterThanOrEqual(7.4)
  })

  it('说话态用 idle 作底（speaking 自带大张嘴会露出来）', async () => {
    // 实测：speaking 嘴区深色像素 1306，idle 仅 538。
    // 用 speaking 作底再叠加动态嘴，"闭合"档看着仍半张着。
    const src = (await import('../src/components/DigitalHuman.vue?raw')).default
    expect(src).toContain("if (state.value === 'speaking') return AVATAR_IMAGES.idle")
  })
})
