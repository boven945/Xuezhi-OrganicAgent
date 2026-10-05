/**
 * 音量包络驱动的口型开合。
 *
 * ## 为什么需要它
 *
 * **实测结论（2026-10-05）**：Fay 的口型数据 `Lips` **仅在 Windows 生成**
 * （`core/fay_core.py:2270` 的 `if platform.system() == "Windows":`，
 * 靠 `ProcessWAV.exe` 从 WAV 离线分析）。
 * 本项目跑在 Linux 容器里，`Lips` 恒为 `null`——
 * 实测收到的消息里确实如此。
 *
 * 所以我们不能声称"音素同步口型"。但**完全不动嘴**也不好：
 * 之前是定时在待/说话两态间切换，看起来像随机闪烁而非说话。
 *
 * ## 用音量包络替代音素
 *
 * 不依赖 Fay 任何私有能力，只用浏览器给的：
 * - `AudioContext` + `AnalyserNode` 拿实时音量（RMS）
 * - 音量映射到嘴张开度
 *
 * **这是近似，但不是瞎猜**——音量包络本身就随音节起伏，
 * 中文朗读的音量峰谷与音节边界大致同步，观感上比定时切换自然得多。
 *
 * ## 为什么不直接用 Audio 元素 + `volumechange`
 *
 * `volumechange` 只在用户手动改音量时触发，不是音量分析。
 *
 * ## 性能
 *
 * 每帧只读一个 `getByteTimeDomainData`（128 字节），
 * 开销可忽略。`requestAnimationFrame` 在页面隐藏时自动暂停，
 * 故后台标签页不耗 CPU。
 */

/** 采样点个数。128 是 AnalyserNode 的常见下限，够用。 */
const FFT_SIZE = 128

/**
 * 音量→张嘴度的映射区间。
 *
 * 下限不用 0：静音时嘴也应微闭而非全闭，否则"停顿时嘴在动"很怪。
 * 上限 1.0： louder 也不外扩，避免嘴张到不像人。
 */
const MOUTH_MIN = 0.06
const MOUTH_MAX = 0.85

/**
 * 平滑系数（每秒衰减比例）。
 *
 * **为什么要平滑**：原始 RMS 抖动剧烈（每个音素的起止都是尖峰），
 * 直接驱动会看到嘴在抽搐。用指数平滑后是"张→收"而非"跳变"。
 *
 * 0.35 表示每 100ms 衰减约 35%——实测偏慢则调小、偏抖则调大。
 */
const SMOOTHING = 0.35

export interface MouthEnvelope {
  /** 当前张嘴度，0~1。 */
  readonly value: number
  /** 开始分析。 */
  start(): void
  /** 停止分析并释放资源。 */
  stop(): void
}

/**
 * 创建口型分析器。
 *
 * @param audioEl 要分析的音频元素（通常是 Fay 推来的那个 WAV）。
 *
 * **只接一个元素**：我们只有一路讲解音频，接多个毫无意义。
 * 若将来要同时分析，改成接受数组并按最大值合并。
 */
export function createMouthEnvelope(audioEl: HTMLAudioElement): MouthEnvelope {
  // AudioContext 必须在用户手势后创建，否则被自动播放策略挂起——
  // 故调用方需在 play() 之后立刻 start()。
  //
  // **用函数而非立即取值**（实测踩到）：早前写成
  // `const Ctor = window.AudioContext ?? ...`，那是在**构造时**求值。
  // 而"此刻浏览器是否提供 Web Audio"只有在**真正 start 时**问才有意义——
  // 环境可能变化（测试桩、策略放开、页面从后台恢复），
  // 提前求值会把"暂时没有"固化成"一直没有"。
  const resolveCtor = (): typeof AudioContext | undefined =>
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext

  let ctx: AudioContext | null = null
  let analyser: AnalyserNode | null = null
  let source: MediaElementAudioSourceNode | null = null
  // 显式指定 ArrayBuffer 而非 ArrayBufferLike：
  // TS 5.7 起 Uint8Array 带泛型参数，AnalyserNode 的签名要求
  // 恰好是 Uint8Array<ArrayBuffer>，而 new Uint8Array(n) 推出的是
  // ArrayBufferLike —— 不标注就报TS2345。
  let buf: Uint8Array<ArrayBuffer> = new Uint8Array(new ArrayBuffer(FFT_SIZE))
  let raf = 0
  let last = 0
  let smoothed = 0
  let running = false

  function tick(now: number): void {
    if (!running || !analyser) return
    // 首帧没有上一帧时间，delta 记 0
    const delta = last === 0 ? 0 : (now - last) / 1000
    last = now
    // 上限 0.1s：切回标签页时 first delta 极大，会让嘴突然大张
    const dt = Math.min(delta, 0.1)

    analyser.getByteTimeDomainData(buf)
    // RMS：先减去 128（无信号时的直流偏置），再求平方平均
    let sum = 0
    for (let i = 0; i < buf.length; i += 1) {
      const v = (buf[i] ?? 128) - 128
      sum += v * v
    }
    const rms = Math.sqrt(sum / buf.length) / 128

    // 映射到嘴张开度
    // **放大系数 8.0 是三步实测定的**（每次都改常数再跑真实音频）：
    //   1.6 → 峰值 0.18（几乎看不出在动）
    //   4.2 → 峰值 0.37（仍偏小）
    //   8.0 → 峰值约 0.7（观感合适）
    // 真实 Fay音频的原始 RMS 只到 0.05~0.1，故需要放大。
    // **换音色/语速可能需重调——这个值不普适**，
    // 故上面三档都记在注释里，便于下次直接跳到合适的档位。
    const target = MOUTH_MIN + rms * (MOUTH_MAX - MOUTH_MIN) * 8.0
    const clamped = Math.max(MOUTH_MIN, Math.min(MOUTH_MAX, target))
    // 指数平滑：开口快、闭合稍慢，观感更自然
    const k = 1 - Math.exp(-dt * SMOOTHING * 10)
    smoothed += (clamped - smoothed) * k

    raf = requestAnimationFrame(tick)
  }

  /**
   * 停止并释放。
   *
   * **刻意用具名函数而非在 `start` 的 catch 里调`stop()`**——
   * 对象字面量里的方法互相引用时，定义顺序决定可用性：
   * `start` 在 `stop` 之前定义，构造后立即调用 `start()`
   * 会拿到 `stop is not a function`（实测踩到）。
   */
  function stopAll(): void {
    running = false
    if (raf) cancelAnimationFrame(raf)
    raf = 0
    try {
      source?.disconnect()
      analyser?.disconnect()
      void ctx?.close()
    } catch {
      // 已关闭的 context 再 close 会抛，忽略
    }
    source = null
    analyser = null
    ctx = null
    smoothed = 0
  }

  return {
    get value(): number {
      return smoothed
    },
    start(): void {
      if (running) return
      const Ctor = resolveCtor()
      if (!Ctor) {
        // 无 Web Audio：静默降级，嘴保持微闭。
        // **不抛错**——口型是增强，不能因它让数字人不可用。
        return
      }
      try {
        ctx = new Ctor()
        analyser = ctx.createAnalyser()
        // 采样点不足 fftSize 时 AnalyserNode 会抛，落到下面的 catch
        analyser.fftSize = FFT_SIZE
        // 必须接destination，否则音频不出声
        source = ctx.createMediaElementSource(audioEl)
        source.connect(analyser)
        analyser.connect(ctx.destination)
        running = true
        last = 0
        smoothed = MOUTH_MIN
        raf = requestAnimationFrame(tick)
      } catch {
        // 拿不到音频上下文就放弃分析，**不影响播放**
        stopAll()
      }
    },
    stop: stopAll,
  }
}
