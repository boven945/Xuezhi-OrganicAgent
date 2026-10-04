/**
 * 语音播放。
 *
 * ## 为什么自己管 Blob URL 而不是直接给 `<audio src="...">`
 *
 * 后端已给出 `audio_url`（见 `SpeechResponse`），理论上可以直接塞进
 * `<audio src>`。但那样每次播放都要**重新下载**同一个音频，
 * 而学生往往会反复听同一段讲解。
 *
 * 改为「一次合成 → 多次播放」：
 * 合成后把音频抓成 Blob 并 `createObjectURL`，
 * 之后复用同一个 URL，**不重复请求**。
 *
 * ## 必须 revoke，否则内存持续增长
 *
 * `URL.createObjectURL` 创建的 URL **不会被浏览器自动回收**
 * （实测踩过：连续合成 10 次就有 10 份音频驻留内存）。
 * 换新音频前必须先 revoke 旧的，否则泄漏。
 *
 * 另有一条实测结论影响了本实现的错误处理：
 * **音频过期时后端返回的是 JSON 错误体，不是音频**。
 * 若直接把 URL 塞进 `<audio>`，浏览器会报
 * `MEDIA_ELEMENT_ERROR: Format error` —— 学生看到的是
 * "播放失败"这种技术文案，而不是"语音已失效，请重新生成"。
 * 故这里**先 fetch 校验 Content-Type**，再决定是播音频还是提示。
 */

import { getCurrentInstance, onBeforeUnmount, ref, shallowRef } from 'vue'

import { API_BASE, ApiRequestError, readErrorBody, speak } from '../api/client'
import type { SpeechResponse, SpeechStage } from '../types/api'

/** 播放状态。`idle` 是初始态，`loading` 只出现在合成中。 */
export type PlaybackState = 'idle' | 'loading' | 'playing' | 'ended' | 'error'

/** 语音合成请求的默认超时（毫秒）。 */
const SYNTH_TIMEOUT_MS = 60_000

/**
 * 把后端给的路径补成绝对 URL。
 *
 * 后端返回的 `audio_url` 是**绝对路径**（由 `request.url_for` 生成），
 * 但开发时前端在 5173、后端在 8000，故须补上 `API_BASE`。
 * 用 `new URL(path, base)` 而非字符串拼接——后者在 base 有子路径时会拼错。
 */
function resolveAudioUrl(path: string): string {
  return new URL(path, `${API_BASE || window.location.origin}`).toString()
}

/**
 * 语音播放控制器。
 *
 * 典型用法见 `SpeechButton.vue`。
 */
export function useSpeechPlayback() {
  const state = ref<PlaybackState>('idle')
  const stage = ref<SpeechStage>('disabled')
  /** 面向学生的说明，可直接展示。 */
  const reason = ref('')
  const truncated = ref(false)
  /** 当前音频的 Blob URL。**非空时必须 revoke**。 */
  const objectUrl = shallowRef<string | null>(null)

  const audio = new Audio()
  audio.preload = 'auto'

  /** 上一次的合成是否被截断——界面据此提示"还有内容"。 */
  function revokeObjectUrl(): void {
    if (objectUrl.value) {
      // 失败也不该让流程中断——泄漏一个 URL 好过整个播放流程崩掉
      try {
        URL.revokeObjectURL(objectUrl.value)
      } catch {
        /* 忽略：释放失败不影响后续播放 */
      }
      objectUrl.value = null
    }
  }

  /**
   * 合成并播放。
   *
   * @param text 要朗读的文本。
   * @param opts.pushDigitalHuman 是否同时驱动数字人。
   * @param opts.user Fay 侧会话标识——**多学生共用默认值会互相打断**。
   */
  async function speakAndPlay(
    text: string,
    opts: { pushDigitalHuman?: boolean; user?: string } = {},
  ): Promise<void> {
    const trimmed = text.trim()
    if (!trimmed) return

    state.value = 'loading'
    reason.value = ''

    let result: SpeechResponse
    try {
      result = await speak(
        trimmed,
        { pushDigitalHuman: opts.pushDigitalHuman ?? false, user: opts.user ?? 'User' },
        AbortSignal.timeout(SYNTH_TIMEOUT_MS),
      )
    } catch (err) {
      // 只有**网络层/ HTTP 层失败**才会到这里
      // （后端合成失败仍返回 200 + stage）
      state.value = 'error'
      reason.value =
        err instanceof ApiRequestError ? err.message : '语音请求失败，请稍后重试。'
      return
    }

    stage.value = result.stage
    reason.value = result.reason
    truncated.value = result.truncated

    if (!result.available || !result.audio_url) {
      // 四态里只有 unavailable / not_configured / disabled 会到这里
      state.value = 'error'
      return
    }

    // 换新音频前先释放旧的（否则连续合成会累积，见文件头说明）
    revokeObjectUrl()

    try {
      const url = resolveAudioUrl(result.audio_url)
      const response = await fetch(url, { signal: AbortSignal.timeout(SYNTH_TIMEOUT_MS) })

      if (!response.ok) {
        // 典型场景：音频已过期（404 + speech_audio_gone）。
        // 必须走 readErrorBody 才能拿到后端的中文文案——
        // 直接读 response.status 只能得到 "HTTP 404" 这种技术表述。
        throw await readErrorBody(response)
      }

      const contentType = response.headers.get('content-type') ?? ''
      if (!contentType.startsWith('audio/')) {
        // 后端本不该返回非音频（已用 404 + JSON 表达失效），
        // 但代理或网关可能插入 HTML 错误页。**明确拒绝**，
        // 否则 <audio> 会报 "Format error"，学生看到的是技术文案。
        state.value = 'error'
        reason.value = '语音服务返回了非音频内容。'
        return
      }

      const blob = await response.blob()
      objectUrl.value = URL.createObjectURL(blob)
    } catch (err) {
      state.value = 'error'
      reason.value =
        err instanceof ApiRequestError
          ? err.message
          : '语音加载失败，请重新生成。'
      return
    }

    audio.src = objectUrl.value
    try {
      await audio.play()
      state.value = 'playing'
    } catch (err) {
      // 浏览器自动播放策略会拒绝未经用户手势触发的 play()，
      // 抛出 `DOMException{name: "NotAllowedError"}`（MDN 确认）。
      //
      // **按 name 判断而非 instanceof**——实测踩过：
      // happy-dom 提供了**自己的** DOMException，
      // 与全局的`DOMException` 不是同一个构造函数，
      // 故 `err instanceof DOMException` 在测试环境里为 false，
      // 会把"用户尚未点击"误判成"播放失败"。
      // MDN 的示例也是读 `error.name`。
      const name = err instanceof Error || (err && typeof err === 'object') ? (err as { name?: string }).name : ''
      if (name === 'NotAllowedError') {
        // **不是错误**：用户再点一次就能播。故回到 idle 而非 error，
        // 按钮也保持可用（见 SpeechButton 的 label逻辑）。
        state.value = 'idle'
        reason.value = '浏览器要求先点击才能播放，请再次点击播放按钮。'
        return
      }
      state.value = 'error'
      reason.value = '播放失败，请重试。'
    }
  }

  /** 停止播放并回到初始态。 */
  function stop(): void {
    audio.pause()
    audio.currentTime = 0
    if (state.value === 'playing') state.value = 'idle'
  }

  /**
   * 组件卸载时释放资源。**必须有**——否则 Blob URL 泄漏到页面关闭。
   *
   * 用 `getCurrentInstance()` 守卫：`onBeforeUnmount` 在**无活动组件实例**
   * 时会打 Vue 警告（实测：单测里直接调 composable 就会触发）。
   * 守卫让它在组件外调用时静默跳过——单测正是要这么用。
   */
  if (getCurrentInstance()) {
    onBeforeUnmount(() => {
      audio.pause()
      audio.src = ''
      revokeObjectUrl()
    })
  }

  return { state, stage, reason, truncated, speakAndPlay, stop }
}
