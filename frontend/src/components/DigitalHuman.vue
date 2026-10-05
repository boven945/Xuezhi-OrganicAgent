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
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  AVATAR_IMAGES,
  lipsDurationMs,
  resolveAvatarState,
  type AvatarState,
  type FayHumanData,
} from '../viz/avatar'
import { createMouthEnvelope, type MouthEnvelope } from '../viz/mouth'

const props = withDefaults(
  defineProps<{
    /** Fay 是否已启用（来自 /health 的结构化字段）。 */
    fayEnabled?: boolean
    /** Fay 10002 WebSocket 地址。仅在 fayEnabled 为 true 时使用。 */
    endpoint?: string
    /** 当前正在播放讲解文本——用于无Fay 时的口型开合。 */
    speakingText?: string | null
    /**
     * 会话标识。**必须与后端推Fay 时传的 `user` 一致**，
     * 否则 Fay 的 `get_client_output(user)` 匹配不到本客户端，
     * 推送会被静默丢弃（实测踩过）。
     *
     * 多人同时使用时**必须区分**——共用一个值会导致互相打断。
     */
    user?: string
  }>(),
  { fayEnabled: false, endpoint: 'ws://127.0.0.1:10002', speakingText: null, user: 'User' },
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

/**
 * 正在播放的音频元素。
 *
 * **必须显式持有引用**，否则无法在卸载时停止播放。
 */
let audioEl: HTMLAudioElement | null = null

/** 音量包络口型分析器。无 Web Audio 时为 null（不影响播放）。 */
let mouth: MouthEnvelope | null = null

/** 播完或出错时清理。 */
function finishAudio(): void {
  isSpeaking.value = false
  state.value = 'idle'
  mouth?.stop()
  mouth = null
  audioEl = null
}

/** 图层样式：把嘴张开度写进 CSS 变量。 */
const figureStyle = computed(() => ({
  '--mouth-open': String(mouth?.value ?? 0),
}))

/**
 * 播放 Fay 推来的音频。
 *
 * ## 为什么用 Fay 的音频而不是我们自己的 TTS
 *
 * Fay 推的`HttpValue` 指向它自己的 WAV（实测237KB）。
 * 既然它已经合成了，就播它那份——**否则会出现两个声音重叠**
 * （我们的 edge-tts + Fay 的 edge-tts 同时播同一句话）。
 *
 * ## 为什么不用 Lips 判断时长
 *
 * Linux 容器内 `Lips`恒为空（实测）。故用 `Audio.duration`，
 * 它是真实音频长度，播放完触发 `onended` 自然回到待机。
 */
function playFayAudio(url: string): void {
  if (!url) return
  // 上一段还没播完就被打断（Fay 非队列模式会清空前序音频）
  audioEl?.pause()
  clearIdleTimer()
  const el = new Audio(url)
  audioEl = el
  // 音量包络驱动口型。**必须先挂 analyser 再 play**——
  // AudioContext 在无用户手势时会被挂起，而这里已在点击链路内。
  mouth?.stop()
  mouth = createMouthEnvelope(el)
  mouth.start()
  el.onended = finishAudio
  el.onerror = () => {
    // 播不出来也要回到待机，否则形象会卡在说话态
    finishAudio()
  }
  el.play().catch(() => {
    // 浏览器会自动播放策略拦截，此时降级为"仅显示状态"
    // 而不是无声卡住
    isSpeaking.value = true
    state.value = 'speaking'
    scheduleIdle(4000)
  })
}

/** 处理一条 Fay 消息。 */
function onFayMessage(data: FayHumanData): void {
  if (disposed) return
  lastFayData.value = data
  // Key=text 是流式/结束标记，没有音频，**不改变状态**——
  // 否则每收到一个标记就会把正在播的音频打断。
  if (data.Key === 'text') return

  isSpeaking.value = true
  state.value = resolveAvatarState(data, true)

  // 有Lip 音素（仅 Windows）时用音素时长定时；否则靠音频的 onended
  const dur = lipsDurationMs(data.Lips)
  if (dur > 0) scheduleIdle(dur)

  if (data.HttpValue) {
    playFayAudio(data.HttpValue)
  } else if (dur === 0) {
    // 既无音频也无音素：给一个有限时长的兜底，
    // 否则可能永远停在说话态
    scheduleIdle(4000)
  }
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
    // **握手必须主动发**（实测自`core/wsa_server.py:28-45`）。
    //
    // 连接建立不等于被登记为接收端——服务端在握手消息里
    // 读取 `Username` 与 `Output`，据此标记该客户端"要音频"。
    // **不发的后果是静默的**：连接正常、200 正常，
    // 但推送永远不来（我为此白查了两轮）。
    //
    // `Output: true` 表示"我是输出端，要音频"；
    // 服务端 `get_client_output()` 据此决定是否推音频。
    //
    // `Username` 必须与后端 `/transparent-pass` 传的 `user` **完全一致**，
    // 否则 `get_client_output(user)` 匹配不到，推送被静默过滤。
    if (socket) {
      socket.send(JSON.stringify({ Username: props.user, Output: true }))
    }
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

// **刻意不写 onMounted(connect)**（实测踩到）：
// 上面的 watch 带 `immediate: true`，挂载时已经 connect 过一次；
// 再写onMounted(connect) 就会**开两个 WebSocket**，
// 两者都会被Fay 登记为接收端，且各自独立——
// 表现为重复播放、状态互相覆盖。
//
// watch 兼顾「初始为 true」与「运行时切换」两种情况，一个就够。

onBeforeUnmount(() => {
  disposed = true
  // 停掉正在播的音频，否则组件没了声音还在响
  audioEl?.pause()
  audioEl = null
  mouth?.stop()
  mouth = null
  clearIdleTimer()
  stopMouthFallback()
  disconnect()
})
</script>

<template>
  <aside class="avatar" :aria-label="'化学老师形象，当前状态：' + state">
    <!-- 嘴部形变层：叠在形象图上，Y 轴压扁模拟开合。
         **刻意用 CSS 变量而非直接改图**：形象是静态 PNG，
         改图需要重新生成三态，而开合是连续量，图做不到。 -->
    <div class="avatar__figure" :style="figureStyle">
      <img
        class="avatar__img"
        :src="image"
        :data-state="state"
        alt="化学老师形象"
        draggable="false"
      />
      <!-- 嘴部高亮块：仅在说话态可见 -->
      <span v-if="isSpeaking" class="avatar__mouth" aria-hidden="true" />
    </div>
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

/**
 * 形象图层。position: relative 是嘴部定位的前提。
 *
 * **不用 transform 缩放整张图**：那会让整个形象（含文字、眼镜）
 * 一起变形，看起来像被压扁的人偶，而不是张嘴。
 */
.avatar__figure {
  position: relative;
  width: 100%;
  max-width: 220px;
  line-height: 0;
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

/**
 * 嘴部开合标记。
 *
 * ## 位置与尺寸是目测调的
 *
 * 形象图里的嘴约在**下方 46%** 处（生成图时的构图），
 * 宽约 12%、高约 5%。这两个值不精确，但**没人看得出来**——
 * 人在看整体效果时不会量嘴的像素位置。
 *
 * ## 为什么用椭圆而非矩形
 *
 * 矩形看起来像贴了块胶；椭圆在缩放后接近嘴形。
 *
 * ## 为什么 mix-blend-mode: multiply
 *
 * 让它与底下的图**相乘**而非叠加纯色——
 * 纯色块会盖住底图细节（牙齿/唇线），相乘则保留明暗关系。
 */
.avatar__mouth {
  position: absolute;
  left: 44%;
  top: 46%;
  width: 12%;
  height: 5%;
  transform: translate(-50%, -50%) scaleY(calc(0.35 + var(--mouth-open) * 1.15));
  background: #4a2c20;
  border-radius: 50%;
  mix-blend-mode: multiply;
  /* 变化要快于其他元素，否则嘴跟不上语音 */
  transition: transform 60ms linear;
  pointer-events: none;
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
