<script setup lang="ts">
/**
 * 数字人形象窗口。
 *
 * ## 定位：它是「增强」，不是「依赖」
 *
 * `architecture.md` §6 要求语音是**可降级能力**。本组件遵循同一条：
 * **Fay 未启用、未运行、连接失败——形象一律回落到待机图**，
 * 绝不显示空白占位或错误弹窗。文字交付不依赖它。
 *
 * ## 为什么不接Fay 的渲染端协议
 *
 * 本项目用**静态三态图 + Fay 的音素/动作语义**驱动，
 * 而非 Live2D 骨骼动画。理由是团队只有两人（决策约束），
 * 且静态图的演示效果已足够，Live2D 会引入模型资产与运行时两重依赖。
 *
 * ## 状态来源
 *
 * - `fayEnabled` 为 false → **恒为待机**（用户明确要求的行为）
 * - 为 true 且已连上 10002 → 由 Fay 推送的消息驱动
 * - 为 true 但连不上 → 退回待机，并提示一次（不反复刷）
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  AVATAR_IMAGES,
  lipsDurationMs,
  resolveAvatarState,
  type AvatarState,
  type FayHumanData,
} from '../viz/avatar'

const props = withDefaults(
  defineProps<{
    /** Fay 是否已启用（来自 /health 的结构化字段）。 */
    fayEnabled?: boolean
    /** Fay 10002 WebSocket 地址。仅在 fayEnabled 为 true 时使用。 */
    endpoint?: string
    /** 当前正在播放讲解文本——用于无Fay 时的口型开合。 */
    speakingText?: string | null
  }>(),
  { fayEnabled: false, endpoint: 'ws://127.0.0.1:10002', speakingText: null },
)

/**
 * 形象状态。
 *
 * **默认待机**——这是Fay 未启用时的常态，
 * 也是一切异常的首选回落。
 */
const state = ref<AvatarState>('idle')

/** 最近一条 Fay 消息的 Data，供状态推导使用。 */
const lastFayData = ref<FayHumanData | null>(null)

/** 当前是否在播放音频。 */
const isSpeaking = ref(false)

/** 断线提示。**只提示一次**，不反复刷屏。 */
const disconnected = ref(false)

let socket: WebSocket | null = null
/** 切回待机的定时器句柄。 */
let idleTimer: number | null = null
/** 无Fay 时的口型开合定时器。 */
let mouthTimer: number | null = null
/** 防止组件卸载后回调仍改状态。 */
let disposed = false

const image = computed(() => AVATAR_IMAGES[state.value])

/** 有动作语义时展示一行说明，让学生知道"老师在做什么"。 */
const actionHint = computed(() => {
  if (!props.fayEnabled) return '待机'
  if (disconnected.value) return '待机（未连接数字人服务）'
  const behavior = lastFayData.value?.Action?.behavior
  if (!behavior) return state.value === 'speaking' ? '讲解中' : '待机'
  return `动作：${behavior}`
})

function clearIdleTimer(): void {
  if (idleTimer !== null) {
    window.clearTimeout(idleTimer)
    idleTimer = null
  }
}

/** 排定「讲完就回待机」。 */
function scheduleIdle(ms: number): void {
  clearIdleTimer()
  // 上限 8 秒：Fay 的音素时长偶有异常值，
  // 无上限会让形象卡在说话态，看起来像卡住不动。
  const delay = Math.min(Math.max(ms, 200), 8000)
  idleTimer = window.setTimeout(() => {
    if (disposed) return
    isSpeaking.value = false
    state.value = 'idle'
  }, delay)
}

/**
 * 无 Fay 时的兜底口型：说话期间在待机和说话之间来回切。
 *
 * **为什么需要它**：Fay 未启用时音频由 edge-tts 播，
 * 形象若始终待机会显得"音频在响但老师没动"。
 * 这里用固定节奏切换——**没有音素数据可用**，
 * 所以是近似，不是真口型。这是有意接受的降级。
 */
function startMouthFallback(): void {
  stopMouthFallback()
  mouthTimer = window.setInterval(() => {
    if (disposed) return
    if (isSpeaking.value) {
      state.value = state.value === 'speaking' ? 'idle' : 'speaking'
    }
  }, 260)
}

function stopMouthFallback(): void {
  if (mouthTimer !== null) {
    window.clearInterval(mouthTimer)
    mouthTimer = null
  }
}

/** 处理一条 Fay 消息。 */
function onFayMessage(data: FayHumanData): void {
  if (disposed) return
  lastFayData.value = data
  isSpeaking.value = true
  state.value = resolveAvatarState(data, true)
  const dur = lipsDurationMs(data.Lips)
  if (dur > 0) scheduleIdle(dur)
}

function connect(): void {
  if (!props.fayEnabled || disposed) return
  // 无 WebSocket 实现（老旧环境）时静默降级——不抛错
  if (typeof WebSocket === 'undefined') return
  try {
    socket = new WebSocket(props.endpoint)
  } catch {
    // 构造即失败（地址非法等）也走降级
    disconnected.value = true
    return
  }
  socket.onopen = () => {
    disconnected.value = false
  }
  socket.onmessage = (ev: MessageEvent<string>) => {
    // **不解析失败就崩**：Fay 推送的内容我们只认一部分，
    // 遇到不认识的结构应保持当前状态，而非让整个窗口白屏。
    let parsed: unknown
    try {
      parsed = JSON.parse(ev.data)
    } catch {
      return
    }
    const data = (parsed as { Data?: FayHumanData })?.Data
    if (data && typeof data === 'object') onFayMessage(data)
  }
  socket.onerror = () => {
    disconnected.value = true
  }
  socket.onclose = () => {
    disconnected.value = true
    socket = null
  }
}

function disconnect(): void {
  if (socket) {
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    try {
      socket.close()
    } catch {
      // 关闭失败无需处理——组件即将卸载
    }
    socket = null
  }
}

watch(
  () => props.fayEnabled,
  (on) => {
    // **Fay 从启用切到停用时必须回到待机**：
    // 保留"说话"状态会让用户看到一个不会动的老师
    if (!on) {
      disconnect()
      lastFayData.value = null
      state.value = 'idle'
      isSpeaking.value = false
    } else {
      connect()
    }
  },
  { immediate: true },
)

watch(
  () => props.speakingText,
  (text) => {
    // 文本变化说明开始了新的讲解
    if (text) {
      isSpeaking.value = true
      // Fay 已启用时由它的消息驱动口型，不做兜底切换
      if (!props.fayEnabled) startMouthFallback()
    } else {
      isSpeaking.value = false
      stopMouthFallback()
      if (!props.fayEnabled) {
        state.value = 'idle'
      }
    }
  },
)

onMounted(connect)

onBeforeUnmount(() => {
  disposed = true
  clearIdleTimer()
  stopMouthFallback()
  disconnect()
})
</script>

<template>
  <aside class="avatar" :aria-label="'化学老师形象，当前状态：' + state">
    <img
      class="avatar__img"
      :src="image"
      :data-state="state"
      alt="化学老师形象"
      draggable="false"
    />
    <p class="avatar__hint" :class="{ 'avatar__hint--warn': disconnected && fayEnabled }">
      {{ actionHint }}
    </p>
  </aside>
</template>

<style scoped>
.avatar {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  /* 刻意不加背景色与边框：形象本身是透明 PNG，
     加容器底色会在浅色主题下出现一块色斑。 */
}

.avatar__img {
  width: 100%;
  max-width: 220px;
  height: auto;
  transition: opacity 0.25s ease;
  /* 待机时退到背景里，不与分子抢注意力。
     用 data-state 而非 :only-of-type 之类——
     后者只反映"是不是唯一元素"，与状态无关。 */
  opacity: 0.72;
}

.avatar__img[data-state='speaking'],
.avatar__img[data-state='thinking'] {
  opacity: 1;
}

.avatar__hint {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-tertiary, #888);
  text-align: center;
}

.avatar__hint--warn {
  color: var(--color-text-warning, #b45309);
}
</style>
