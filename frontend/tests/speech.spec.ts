/**
 * 语音播放的行为测试。
 *
 * ## 重点测"看起来能跑但实际会坏"的地方
 *
 * 1. **Blob URL 必须 revoke** —— 不revoke 会持续泄漏内存，
 *    而这类问题在开发时完全看不出来（只是内存涨得慢）。
 * 2. **音频过期时后端返回 JSON 而非音频** —— 若直接把 URL 塞进
 *    `<audio>`，浏览器报 "Format error"，学生看到的是技术文案。
 *    故必须校验 `Content-Type`。
 * 3. **自动播放策略** —— `play()` 可能被拒，
 *    那是预期行为，界面应提示"再点一次"而非报错。
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSpeechPlayback } from '../src/composables/useSpeechPlayback'

/** 造一个最小可用的 Response 替身。 */
function makeResponse(opts: {
  ok?: boolean
  status?: number
  contentType?: string
  body?: string
}): Response {
  const status = opts.status ?? 200
  return {
    ok: opts.ok ?? status < 400,
    status,
    headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? (opts.contentType ?? 'audio/mpeg') : null) },
    blob: async () => new Blob([opts.body ?? 'x'], { type: opts.contentType ?? 'audio/mpeg' }),
    json: async () => JSON.parse(opts.body ?? '{}'),
  } as unknown as Response
}

/** 记录 URL.createObjectURL / revokeObjectURL 的调用。 */
function trackObjectUrls() {
  const created: string[] = []
  const revoked: string[] = []
  let n = 0
  const origCreate = URL.createObjectURL
  const origRevoke = URL.revokeObjectURL
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:mock/${++n}`
    created.push(url)
    return url
  })
  URL.revokeObjectURL = vi.fn((u: string) => {
    revoked.push(u)
  })
  return {
    created,
    revoked,
    restore() {
      URL.createObjectURL = origCreate
      URL.revokeObjectURL = origRevoke
    },
  }
}

/** 替换全局 Audio，记录 play/pause 调用并允许控制其成败。 */
class FakeAudio {
  static instances: FakeAudio[] = []
  src = ''
  currentTime = 0
  paused = true
  preload = ''
  playResult: { ok: boolean; name?: string } = { ok: true }
  playCalls = 0

  constructor() {
    FakeAudio.instances.push(this)
  }
  async play(): Promise<void> {
    this.playCalls += 1
    if (!this.playResult.ok) {
      const err = new Error('blocked')
      err.name = this.playResult.name ?? 'NotAllowedError'
      throw err
    }
    this.paused = false
  }
  pause(): void {
    this.paused = true
  }
}

// 用 beforeAll/afterAll 而非模块顶层的 vi.stubGlobal：
// 后者没有 mockRestore 方法（实测踩过：`audioStub.mockRestore is not a function`），
// 顶层调用还会让本文件在收集阶段就依赖全局替身已就位。
beforeAll(() => {
  vi.stubGlobal('Audio', FakeAudio)
})

afterAll(() => {
  vi.unstubAllGlobals()
})

/** mock fetch 与后端 respond。 */
function mockFetch(handlers: {
  speak?: () => Promise<Response>
  audio?: () => Promise<Response>
}) {
  const calls: string[] = []
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (url.includes('/api/v1/speak') && !url.includes('/speak/')) {
      return handlers.speak!()
    }
    if (url.includes('/api/v1/speak/')) {
      return handlers.audio!()
    }
    throw new Error(`未预期的请求：${url}`)
  }) as typeof fetch
  return calls
}

const READY = JSON.stringify({
  request_id: 'r1',
  stage: 'ready',
  available: true,
  audio_id: 'a'.repeat(32),
  audio_url: 'http://127.0.0.1:8000/api/v1/speak/' + 'a'.repeat(32),
  reason: '',
  truncated: false,
  char_count: 11,
  digital_human_delivered: false,
})

beforeEach(() => {
  FakeAudio.instances = []
  vi.restoreAllMocks()
})

describe('useSpeechPlayback', () => {
  it('合成成功后进入播放态', async () => {
    mockFetch({
      speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
      audio: async () => makeResponse({ contentType: 'audio/mpeg' }),
    })
    const { state, speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('乙醇的结构是 CCO。')
    expect(state.value).toBe('playing')
  })

  it('后端返回 200 但 stage=unavailable 时不播放', async () => {
    mockFetch({
      speak: async () =>
        makeResponse({
          contentType: 'application/json',
          body: JSON.stringify({
            request_id: 'r', stage: 'unavailable', available: false,
            audio_id: null, audio_url: null, reason: '语音服务暂时不可用。',
            truncated: false, char_count: 0, digital_human_delivered: false,
          }),
        }),
      audio: async () => makeResponse({}),
    })
    const { state, reason, speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('测试')
    // 关键：不可用时**不该**尝试播放，但要给出可展示的说明
    expect(state.value).toBe('error')
    expect(reason.value).toBe('语音服务暂时不可用。')
  })

  it('音频过期时用后端的中文文案，不用技术表述', async () => {
    // 后端过期返回 404 + speech_audio_gone + 中文文案
    const errBody = JSON.stringify({
      error: {
        code: 'speech_audio_gone',
        message: '语音已失效，请重新生成。',
        retryable: false,
        request_id: 'r2',
      },
    })
    mockFetch({
      speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
      audio: async () =>
        makeResponse({ ok: false, status: 404, contentType: 'application/json', body: errBody }),
    })
    const { state, reason, speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('测试')
    expect(state.value).toBe('error')
    // 必须是后端的中文文案，而不是 "HTTP 404"
    expect(reason.value).toBe('语音已失效，请重新生成。')
    expect(reason.value).not.toContain('404')
  })

  it('非音频响应被明确拒绝（否则 <audio> 会报 Format error）', async () => {
    mockFetch({
      speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
      // 代理返回 HTML 错误页
      audio: async () => makeResponse({ contentType: 'text/html', body: '<html>502</html>' }),
    })
    const { state, reason, speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('测试')
    expect(state.value).toBe('error')
    expect(reason.value).toContain('非音频')
  })

  it('自动播放被拒时提示再点一次，而不是报错', async () => {
    //让 play() 抛 NotAllowedError——这是浏览器自动播放策略的**预期行为**。
    // **必须设原型而非实例**：playResult 是实例字段（见上），
    // 设 prototype 不生效——实测踩过，测试表现为"state 仍是 playing"。
    const origPlay = FakeAudio.prototype.play
    FakeAudio.prototype.play = async function play(this: FakeAudio) {
      const err = new Error('blocked')
      err.name = 'NotAllowedError'
      throw err
    }
    try {
      mockFetch({
        speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
        audio: async () => makeResponse({ contentType: 'audio/mpeg' }),
      })
      const { state, reason, speakAndPlay } = useSpeechPlayback()
      await speakAndPlay('测试')

      // 关键：state 回到 idle（可再次点击），且提示用户再点一次。
      // 若当成错误处理（state='error'），按钮会变成"播放失败"，
      // 而实际上用户再点一次就好了。
      expect(state.value).toBe('idle')
      expect(reason.value).toContain('再次点击')
      expect(reason.value).not.toContain('失败')
    } finally {
      FakeAudio.prototype.play = origPlay
    }
  })

  it('**换新音频前 revoke 旧的**（防内存泄漏）', async () => {
    const urls = trackObjectUrls()
    try {
      mockFetch({
        speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
        audio: async () => makeResponse({ contentType: 'audio/mpeg' }),
      })
      const { speakAndPlay } = useSpeechPlayback()
      await speakAndPlay('第一段')
      await speakAndPlay('第二段')
      // 两次合成 -> 两个 Blob URL，但第一个必须已被 revoke
      expect(urls.created.length).toBe(2)
      expect(urls.revoked).toContain(urls.created[0])
    } finally {
      urls.restore()
    }
  })

  it('stop() 停止播放', async () => {
    mockFetch({
      speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
      audio: async () => makeResponse({ contentType: 'audio/mpeg' }),
    })
    const { state, speakAndPlay, stop } = useSpeechPlayback()
    await speakAndPlay('测试')
    expect(state.value).toBe('playing')
    stop()
    expect(state.value).toBe('idle')
  })

  it('空文本不发请求', async () => {
    const calls = mockFetch({
      speak: async () => makeResponse({ contentType: 'application/json', body: READY }),
      audio: async () => makeResponse({}),
    })
    const { speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('   ')
    expect(calls.length).toBe(0)
  })

  it('网络失败时给出可读文案', async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    const { state, reason, speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('测试')
    expect(state.value).toBe('error')
    expect(reason.value).toBeTruthy()
  })

  it('truncated 会被透出，供界面提示"还有内容"', async () => {
    mockFetch({
      speak: async () =>
        makeResponse({
          contentType: 'application/json',
          body: READY.replace('"truncated":false', '"truncated":true'),
        }),
      audio: async () => makeResponse({ contentType: 'audio/mpeg' }),
    })
    const { truncated, speakAndPlay } = useSpeechPlayback()
    await speakAndPlay('很长的文本')
    expect(truncated.value).toBe(true)
  })
})

