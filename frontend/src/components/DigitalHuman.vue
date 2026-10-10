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

/**
 * 当前是否**真的连上了 Fay**（而非仅仅配置为启用）。
 *
 * ## 声明位置不可下移（实测踩过）
 *
 * 它必须在 `connect()` **之前**声明：`watch(fayEnabled, ..., {immediate:true})`
 * 会在 setup 期间立即执行并调用 `connect()`，而 `connect()` 写这个 ref。
 * 若声明在后面，`const` 的**暂时性死区**会让那一刻读到`undefined`。
 *
 * ## 为什么不能直接用 props.fayEnabled（实测踩过，2026-10-10）
 *
 * `fayEnabled` 来自 `/health` 的 `caps.fay`，而后端 `probe()` 里是
 * `{"fay": self._settings.fay_enabled}`——**只看配置布尔值，不探测服务**。
 * 于是「配置写着启用、实际没装 Fay」时`caps.fay` 仍是 `true`。
 *
 * 曾经的写法是「`fayEnabled` 为 true 就交给 Fay 驱动口型，不做兜底」，
 * 结果本机 Fay 未运行时**口型25 秒全程不动**：
 * 前端根据一个**配置值**主动关闭了降级路径。
 *
 * > **配置就绪 ≠ 服务可用。** 判据必须是连接状态。
 *
 * `socket.onopen` 置 `true`，`onerror`/`onclose`/构造失败置回 `false`。
 */
const fayConnected = ref(false)

/**
 * 口型由谁驱动。
 *
 * 只有**Fay 真的连上**才交给它——它有真音素，是更准确的信号源。
 * 未启用、或已启用但连不上，一律用本地兜底近似。
 */
const fayDrives = computed(() => props.fayEnabled && fayConnected.value)

let socket: WebSocket | null = null
/** 切回待机的定时器句柄。 */
let idleTimer: number | null = null
/** 无Fay 时的口型开合定时器。 */
let mouthTimer: number | null = null
/** 防止组件卸载后回调仍改状态。 */
let disposed = false

/**
 * 底图。
 *
 * **说话与思考时用 idle.png 作底**（实测决定）：
 * speaking.png 自带大张嘴（嘴区深色像素 1306 vs idle 的 538），
 * 若用它作底再叠加动态嘴，"闭合"那档看着仍半张着——像没闭上。
 * 改用嘴最小的 idle 作底，叠加的开合变化才干净。
 *
 * **思考态仍用 thinking.png**：它的嘴（532）与 idle 几乎一样，
 * 且闭眼歪头的表情本身就有信息量，不该被 idle 覆盖。
 */
const image = computed(() => {
  if (state.value === 'speaking') return AVATAR_IMAGES.idle
  return AVATAR_IMAGES[state.value]
})

/**
 * 一行说明，让学生知道"老师在做什么"。
 *
 * ## 断线时为什么不能说「未连接数字人服务」（实测改动）
 *
 * 原实现在断线时显示 `待机（未连接数字人服务）`。这句把**内部服务状态**
 * 当成了主交付文案：Fay 是纯增强能力，连不上时口型由本地兜底接管
 * （见 fayDrives），**功能完全正常**，却会被学生/ 观众读成"出了故障"。
 *
 * 现改为「待机（简化口型）」—— 仍如实说明当前形态
 * （没有音素数据，用本地近似），但不暴露服务地址与连接状态。
 *
 * **降级应当是看不见的**（`architecture.md` §6：语音是可降级能力）。
 *
 * > 原测试断言 `toContain('未连接')`，本意是证明
 * > 「连不上时组件不白屏、不抛异常」——
 * > **判据选错了**：它把「降级友好」等同于「显示连接错误」。
 * > 现断言「渲染出来且说明当前为简化形态」，**这才是原本要证的**。
 */
const actionHint = computed(() => {
  if (!props.fayEnabled) return '待机'
  // 断线：功能正常（本地兜底口型），只是没有真音素，故说「简化口型」。
  if (disconnected.value) {
    return state.value === 'speaking' ? '讲解中（简化口型）' : '待机（简化口型）'
  }
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
    fayConnected.value = false
    return
  }
  socket.onopen = () => {
    disconnected.value = false
    // **连接建立才置 true**——口型驱动权据此让给 Fay。
    // 仅有配置（caps.fay=true）不足以让出兜底，见 fayConnected 的注释。
    fayConnected.value = true
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
    // 握手失败：把驱动权交回本地兜底，
    // 否则「配置启用但连不上」时形象会静止不动（实测踩过）。
    fayConnected.value = false
  }
  socket.onclose = () => {
    disconnected.value = true
    fayConnected.value = false
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
      fayConnected.value = false
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
      // **Fay 真连上时**由它的消息驱动口型；否则用本地兜底。
      // 判据是 fayDrives（连接态）而非 fayEnabled（配置态）。
      if (fayDrives.value) stopMouthFallback()
      else startMouthFallback()
    } else {
      isSpeaking.value = false
      stopMouthFallback()
      if (!fayDrives.value) {
        state.value = 'idle'
      }
    }
  },
  // **immediate: true**：讲解开始时若 Fay 已连上，fayDrives 从 false 变true，
  // 兜底此时刚启动又会被这个 watch 的组合逻辑接管；
  // 但若讲解**先于**连接建立（或组件已挂载而speakingText 未变），
  // 这个 watch 不会重跑。故连接状态变化时需另行处理，见fayDrives watch。
  { immediate: true },
)

// 连接状态变化时，**重新评估正在进行的讲解由谁驱动**。
// 没有这条，场景「讲解开始 → Fay 稍后才连上/断开」会留下错误的口型驱动方。
watch(fayDrives, (nowDrives) => {
  // 讲解进行中（speakingText 非空）才需要处理，
  // 否则上面对speakingText 的 watch 已经覆盖了。
  if (!props.speakingText) return
  if (nowDrives) {
    // Fay 接管：停掉本地兜底。**不强制改state**——
    // Fay 消息一到就会自己设状态，这里抢设反而可能覆盖它。
    stopMouthFallback()
  } else {
    // Fay 不可用：必须兜底，否则形象静止不动
    startMouthFallback()
  }
})

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
    <p class="avatar__hint">
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
 * ## 坐标是**实测**的，不是目测的
 *
 * 原先写的是 `top: 46%`（目测），结果黑块**盖在眼睛上**——
 * 用户一眼看出画面崩了。重新用像素分析定位：
 *
 * ```python
 * # 裁头部放大 2倍肉眼确认 → 找到嘴 → 再按颜色精确定位
 * # 嘴腔（暗红/粉红）在 speaking.png 的 y485-560, x459-547
 * # 中心 (503, 522)÷ 1024 → x=0.491, y=0.510
 * ```
 *
 * **教训**：目测百分比定位在 1024px 图上偏 5% 就足以盖错器官。
 * 凡是"贴一个元素到图上某处"，都必须量坐标。
 *
 * ## 为什么叠加而不是换图
 *
 * 三张 PNG **各自都画了嘴**（idle一条线 / speaking 大O / thinking 小O）。
 * 实测嘴区深色像素：idle 538、thinking 532、**speaking 1306**——
 * speaking 自带的嘴最明显。
 *
 * 所以黑块的作用是**把底图已有的嘴统一开合**：
 * 用哪张底图，同一张图上叠不同开合度，观感连续；
 * 若靠换图实现，则只有"闭/半开/大O"三档，无法连续变化。
 *
 * ## 底图该选哪张（实测后改的）
 *
 * 原先说话时用 speaking.png 作底——但它**自带大张嘴**，
 * 于是"闭合 0.06"那档看着仍半张着，像没闭上。
 * 改用 idle.png 作底（它的嘴最小），
 * 再叠加动态黑块，开合变化才干净。
 *
 * `mix-blend-mode: multiply` 保留底图明暗，只改变张开程度。
 *
 * ## 尺寸
 *
 * 宽 8.7%、高 7.4%（实测嘴腔占图比例）。
 * 比嘴腔略大一点，让闭合时能完全盖住原有的嘴。
 */
.avatar__mouth {
  position: absolute;
  /* 实测值：嘴中心 x=0.491 y=0.510（见上方说明） */
  left: 49.1%;
  top: 51%;
  width: 9.5%;
  height: 8%;
  /*
   * scaleY 直接控制开合。
   * 下限 0.12：完全闭合时仍留一条细缝——
   * 压到 0 会让嘴变成一条线，看起来像没有嘴。
   */
  transform: translate(-50%, -50%) scaleY(calc(0.12 + var(--mouth-open) * 1.05));
  background: #3d2018;
  border-radius: 46% 46% 50% 50% / 38% 38% 62% 62%;
  mix-blend-mode: multiply;
  transition: transform 60ms linear;
  pointer-events: none;
}

.avatar__hint {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-tertiary, #888);
  text-align: center;
}

/* 警告样式（--warn）已随「断线不显示内部状态」一并移除：
   断线时口型由本地兜底接管，功能正常，无须用警告色提示。 */
</style>
