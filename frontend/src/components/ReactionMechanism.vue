<script setup lang="ts">
/**
 * 反应机理 3D 动画组件。
 *
 * ## 复用而非重写
 *
 * 三维渲染交给 `viz/ReactionPlayer.ts`（基于 `viewer.ts` 的 Three.js 基元）。
 * 本组件只负责**界面编排**：取数据、处理信封/降级、驱动播放控件、
 * 展示教材口径的断键/成键讲解。
 *
 * ## 两种触发来源
 *
 * - **学生主动浏览**：用 `<select>` 从 `REACTION_LIST` 选反应（P3 的落地入口）；
 * - **Agent 联动**（P4）：父组件传入 `reaction-id` 属性，自动加载对应反应。
 *
 * ## 降级（与分子端点一致）
 *
 * - WebGL 不可用 → 文字提示，页面其余部分照常；
 * - 后端 503（`api_component_unavailable`，RDKit 缺失）→ 提示"化学引擎暂不可用"；
 * - 未收录（`reaction_not_found`）→ 提示"暂无机理动画"，退回文本讲解。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { ApiRequestError, fetchReactionMechanism } from '../api/client'
import type { ReactionMechanismData } from '../types/api'
import { ReactionPlayer } from '../viz/ReactionPlayer'

const props = defineProps<{
  /** Agent 联动时直接指定反应 id（P4）。为空则走学生自选。 */
  reactionId?: string | null
}>()

/**
 * 可浏览的反应清单。
 *
 * **临时在前端列一份**：后端尚未提供 `/api/v1/reaction/list`（不在本轮范围，
 * 见 `docs/reaction-mechanism-viz-update-plan.md` 待办）。反应库由教师编审、
 * 数量少且稳定，前端列一份可接受；将来接 list 端点后此处改为请求所得。
 */
const REACTION_LIST: { id: string; name: string }[] = [
  { id: 'addition-ethene-bromine', name: '乙烯与溴的加成' },
  { id: 'substitution-sn2-bromoethane-hydroxide', name: '溴乙烷的亲核取代（SN2）' },
  { id: 'esterification-acetic-acid-ethanol', name: '乙酸与乙醇的酯化' },
]

const container = ref<HTMLDivElement | null>(null)
/** shallowRef：Three.js 实例不应被 Vue 深度代理。 */
const player = shallowRef<ReactionPlayer | null>(null)
const selected = ref<string>(props.reactionId ?? REACTION_LIST[0]?.id ?? '')

type Status = 'idle' | 'loading' | 'ready' | 'error'
const status = ref<Status>('idle')
const errorMsg = ref('')
const data = ref<ReactionMechanismData | null>(null)

const playing = ref(false)
const speed = ref(1)
const loop = ref(false)
const progress = ref(0) // 归一化 0~1
const frameIndex = ref(0)

let abort: AbortController | null = null

function onTick(p01: number, fi: number): void {
  progress.value = p01
  frameIndex.value = fi
}
function onEnded(): void {
  playing.value = false
}

async function load(id: string): Promise<void> {
  if (!id || !player.value) return
  abort?.abort()
  abort = new AbortController()
  status.value = 'loading'
  errorMsg.value = ''
  try {
    const resp = await fetchReactionMechanism(id, { frameCount: 24, fps: 12 }, abort.signal)
    if (!resp.ok || !resp.data) {
      // HTTP 200 + ok=false（如 reaction_not_found）：正常 envelope
      status.value = 'error'
      errorMsg.value = resp.error?.message ?? '该反应暂无机理动画。'
      data.value = null
      return
    }
    data.value = resp.data
    status.value = 'ready'
    playing.value = false
    progress.value = 0
    frameIndex.value = 0
    player.value.setLoop(loop.value)
    player.value.load(resp.data)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return
    if (err instanceof ApiRequestError) {
      // 503 api_component_unavailable 等：request() 抛而非返回信封
      status.value = 'error'
      errorMsg.value = err.message
      return
    }
    status.value = 'error'
    errorMsg.value = '加载机理动画失败，请稍后重试。'
  }
}

function togglePlay(): void {
  const p = player.value
  if (!p || status.value !== 'ready') return
  p.toggle()
  playing.value = !playing.value
}

function restart(): void {
  player.value?.restart()
  playing.value = true
}

function changeSpeed(m: number): void {
  speed.value = m
  player.value?.setSpeed(m)
}

function changeLoop(): void {
  loop.value = !loop.value
  player.value?.setLoop(loop.value)
}

function seekTo(e: Event): void {
  const v = Number((e.target as HTMLInputElement).value) / 100
  player.value?.seek(v)
  progress.value = v
  playing.value = false
}

function onSelect(e: Event): void {
  selected.value = (e.target as HTMLSelectElement).value
  void load(selected.value)
}

/**
 * Agent 联动：父组件把 reactionId 从空切到某个 id 时，自动加载。
 * 学生手动选的不走这里（避免覆盖手动选择）。
 */
watch(
  () => props.reactionId,
  (id) => {
    if (id) {
      selected.value = id
      void load(id)
    }
  },
)

onMounted(() => {
  if (!container.value) return
  const p = new ReactionPlayer(container.value)
  if (p.unavailableReason) {
    // WebGL 不可用：只提示，页面其余部分照常
    status.value = 'error'
    errorMsg.value = `三维渲染不可用：${p.unavailableReason}。可查看文字讲解。`
    return
  }
  p.onTick = onTick
  p.onEnded = onEnded
  player.value = p
  if (props.reactionId) selected.value = props.reactionId
  void load(selected.value)
})

onBeforeUnmount(() => {
  abort?.abort()
  // 必须释放——显存不由 GC 管
  player.value?.dispose()
  player.value = null
})
</script>

<template>
  <div class="rx">
    <div class="rx__bar">
      <label class="rx__pick">
        <span class="rx__pick-label">反应</span>
        <select class="rx__select" :value="selected" @change="onSelect">
          <option v-for="r in REACTION_LIST" :key="r.id" :value="r.id">{{ r.name }}</option>
        </select>
      </label>

      <div class="rx__controls">
        <button type="button" class="rx__btn" :disabled="status !== 'ready'" @click="togglePlay">
          {{ playing ? '暂停' : '播放' }}
        </button>
        <button type="button" class="rx__btn" :disabled="status !== 'ready'" @click="restart">
          重播
        </button>

        <label class="rx__speed">
          <span>速度</span>
          <select
            class="rx__select rx__select--sm"
            :value="speed"
            @change="changeSpeed(Number(($event.target as HTMLSelectElement).value))"
          >
            <option :value="0.5">0.5×</option>
            <option :value="1">1×</option>
            <option :value="2">2×</option>
          </select>
        </label>

        <label class="rx__check">
          <input type="checkbox" :checked="loop" @change="changeLoop" />
          <span>循环</span>
        </label>
      </div>
    </div>

    <div class="rx__stage">
      <div ref="container" class="rx__canvas" />

      <p v-if="status === 'loading'" class="rx__status" role="status">生成机理动画中…</p>
      <p v-else-if="status === 'error'" class="rx__error" role="alert">{{ errorMsg }}</p>

      <p v-if="status === 'ready'" class="rx__hint">拖动旋转 · 滚轮缩放</p>
    </div>

    <!-- 进度条：仅在已就绪时显示，定位到当前帧 -->
    <input
      v-if="status === 'ready'"
      class="rx__seek"
      type="range"
      min="0"
      max="100"
      :value="Math.round(progress * 100)"
      :aria-label="`动画进度，第 ${frameIndex + 1} / ${data?.frame_count ?? 0} 帧`"
      @input="seekTo"
    />

    <div v-if="status === 'ready' && data" class="rx__notes">
      <h4 class="rx__name">{{ data.name }}</h4>
      <p v-if="data.transition_note" class="rx__note">
        <span class="rx__note-label">断键成键</span>{{ data.transition_note }}
      </p>
      <p v-if="data.highlight_note" class="rx__note rx__note--hl">
        <span class="rx__note-label">看这里</span>{{ data.highlight_note }}
      </p>
      <p class="rx__legend">
        琥珀色高亮的原子正在发生断键/成键；淡入淡出的键为正在形成或断裂的键。
        坐标为一种可能构象，仅作风格化示意。
      </p>
    </div>
  </div>
</template>

<style scoped>
.rx {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.rx__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.rx__pick {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.rx__pick-label {
  color: var(--text-muted);
}

.rx__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}

.rx__select {
  font: inherit;
  font-size: 0.8125rem;
  padding: 0.25rem 0.5rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  color: var(--text);
}

.rx__select--sm {
  font-size: 0.75rem;
  padding: 0.1875rem 0.375rem;
}

.rx__btn {
  font: inherit;
  font-size: 0.75rem;
  padding: 0.25rem 0.625rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  border-radius: 0.25rem;
  cursor: pointer;
}

.rx__btn--on {
  background: var(--accent);
  color: #fff;
}

.rx__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.rx__speed {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--text-muted);
}

.rx__check {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.rx__stage {
  position: relative;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
}

.rx__canvas {
  height: 22rem;
  width: 100%;
  cursor: grab;
}

.rx__canvas:active {
  cursor: grabbing;
}

.rx__status,
.rx__error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 1.5rem;
  text-align: center;
  font-size: 0.8125rem;
}

.rx__status {
  color: var(--text-secondary);
  background: var(--surface-2);
}

.rx__error {
  color: var(--warning);
  background: var(--surface-2);
}

.rx__hint {
  position: absolute;
  left: 0.5rem;
  bottom: 0.5rem;
  margin: 0;
  padding: 0.125rem 0.375rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  border-radius: 0.25rem;
  pointer-events: none;
}

.rx__seek {
  width: 100%;
  accent-color: var(--accent);
}

.rx__notes {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  font-size: 0.8125rem;
}

.rx__name {
  margin: 0;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text);
}

.rx__note {
  margin: 0;
  line-height: 1.7;
  color: var(--text-secondary);
}

.rx__note-label {
  display: inline-block;
  margin-right: 0.375rem;
  padding: 0.0625rem 0.375rem;
  font-size: 0.6875rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  color: var(--text-muted);
}

.rx__note--hl .rx__note-label {
  color: #8a5a00;
  border-color: color-mix(in srgb, #ffb020 60%, var(--border));
}

.rx__legend {
  margin: 0.25rem 0 0;
  font-size: 0.6875rem;
  color: var(--text-muted);
  line-height: 1.6;
}
</style>
