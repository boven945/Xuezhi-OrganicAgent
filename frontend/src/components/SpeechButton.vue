<script setup lang="ts">
/**
 * 语音朗读按钮。
 *
 * ## 为什么单独一个组件
 *
 * 语音是**纯增强能力**（`architecture.md` §6）：不可用时文本答复
 * 照常交付。故它必须能被**独立降级**——不能因为语音出问题
 * 就让整个答案区报错。
 *
 * ## 四态的界面处理是本组件的重点
 *
 * 后端返回 `stage` 有四种，界面对待方式**刻意不同**：
 *
 * | stage | 界面 | 理由 |
 * | --- | --- | --- |
 * | `disabled` | 按钮隐藏 | 用户主动关的，显示按钮等于给人"可以点但没反应"的错觉 |
 * | `not_configured` | 提示"尚未配置" | 缺配置是可修的，值得说一声 |
 * | `unavailable` | 提示"暂时不可用"+ 保留重试 | 服务故障，重试可能成功 |
 * | `ready` | 正常播放 | — |
 *
 * 统一显示"播放失败"是**误导**：学生无法据此判断该重试、
 * 该找老师、还是该接受现实。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { useSpeechPlayback } from '../composables/useSpeechPlayback'
import type { useHealthStore } from '../stores'

const props = defineProps<{
  /** 要朗读的文本。通常是答复正文。 */
  text: string
  /** 后端健康状态——用于在服务未连接时提前隐藏按钮。 */
  health?: ReturnType<typeof useHealthStore>
}>()

/**
 * 播放状态变化事件，供数字人形象同步口型。
 *
 * **为什么用 emit 而不是共享 store**：语音播放是**组件局部状态**
 * （谁在播、播什么），不该污染全局 store。把状态往上抛，
 * 由父组件决定谁需要知道（当前是数字人）。
 */
const emit = defineEmits<{
  /** `true` = 开始朗读，`false` = 停止或播完。 */
  (e: 'speaking-change', speaking: boolean): void
}>()

const { state, stage, reason, truncated, speakAndPlay, stop } = useSpeechPlayback()

// 播放状态变化 → 通知父组件。
// watch 而非在 click 里 emit：播完也会回到 idle，
// 只有 watch 能覆盖「自然播完」这条路径。
watch(
  () => state.value === 'playing',
  (playing) => emit('speaking-change', playing),
)

/** 本次会话内用户是否主动关过语音。关过后不再打扰。 */
const dismissed = ref(false)

const reachable = computed(() => props.health?.reachable !== false)

/**
 * 按钮是否该出现。
 *
 * **`disabled` 时隐藏**而非禁用——见文件头表格。
 * `unavailable` 时**保留**（可重试）。
 */
const visible = computed(() => {
  if (dismissed.value) return false
  if (!reachable.value) return false
  if (state.value === 'error' && stage.value === 'disabled') return false
  return true
})

const label = computed(() => {
  switch (state.value) {
    case 'loading':
      return '合成中…'
    case 'playing':
      return '停止播放'
    case 'ended':
      return '重新朗读'
    default:
      return '朗读'
  }
})

/** 提示文案。**空字符串表示不显示**——避免空占位影响布局。 */
const hint = computed(() => {
  if (truncated.value) return '文字较长，语音只朗读了开头部分。'
  return reason.value
})

async function toggle(): Promise<void> {
  if (state.value === 'playing') {
    stop()
    return
  }
  await speakAndPlay(props.text)
}

/** 关闭提示。不隐藏按钮本身——学生可能想再试。 */
function dismiss(): void {
  dismissed.value = true
}

onBeforeUnmount(() => {
  // 停掉播放。composable 内部会 revoke Blob URL，
  // 这里只需确保不再有声音（否则切走页面后语音还在念）。
  stop()
})
</script>

<template>
  <div v-if="visible" class="speech">
    <button
      type="button"
      class="speech__btn"
      :data-state="state"
      :disabled="state === 'loading'"
      @click="toggle"
    >
      <span v-if="state === 'playing'" class="speech__icon" aria-hidden="true">■</span>
      <span v-else class="speech__icon" aria-hidden="true">▶</span>
      <span>{{ label }}</span>
    </button>

    <p v-if="hint" class="speech__hint" :data-kind="stage">
      {{ hint }}
      <button
        v-if="state === 'error'"
        type="button"
        class="speech__close"
        aria-label="关闭提示"
        @click="dismiss"
      >
        ×
      </button>
    </p>
  </div>
</template>

<style scoped>
.speech {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  align-items: flex-start;
}

.speech__btn {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  padding: 0.25rem 0.625rem;
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  background: var(--surface-2);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.12s ease;
}

.speech__btn:hover:not(:disabled) {
  background: var(--surface-3);
}

.speech__btn:disabled {
  cursor: progress;
  opacity: 0.7;
}

.speech__btn[data-state='playing'] {
  border-color: var(--success);
  color: var(--success);
}

.speech__icon {
  font-size: 0.625rem;
  line-height: 1;
}

.speech__hint {
  margin: 0;
  font-size: 0.6875rem;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

/* `not_configured` 是可修的（找管理员配），用 warning 而非 danger */
.speech__hint[data-kind='not_configured'] {
  color: var(--warning);
}
.speech__hint[data-kind='unavailable'],
.speech__hint[data-kind='error'] {
  color: var(--danger);
}

.speech__close {
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  font-size: 0.875rem;
  line-height: 1;
  padding: 0 0.125rem;
}
</style>
