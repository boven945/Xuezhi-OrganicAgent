<script setup lang="ts">
/**
 * 问答主视图。
 *
 * ## 流式渲染的关键
 *
 * 文本靠 `delta` 事件**逐片追加**，故答复会"渐渐长出来"。
 * 但同时 `result` 事件会给出完整文本——**以它为准**覆盖，
 * 因为它包含了非流式路径也一致的全量内容（实测：某些情况下
 * 逐 token 增量之和与最终文本可能有细微差异，以最终为准更可靠）。
 */
import { computed, onMounted, ref } from 'vue'

import HealthBadge from '../components/HealthBadge.vue'
import SourceList from '../components/SourceList.vue'
import { QUESTION_MAX_LENGTH, useAskStore, useHealthStore } from '../stores'

const ask = useAskStore()
const health = useHealthStore()

/** 是否用了同步模式（对照验证用）。 */
const useSync = ref(false)
const inputRef = ref<HTMLTextAreaElement | null>(null)

/** 检索工具是否被调用过。 */
const retrieved = computed(() => ask.invocations.some((i) => i.tool === 'search_knowledge'))

/** 字符计数（超限时禁用提交）。 */
const charCount = computed(() => ask.question.length)
const overLimit = computed(() => charCount.value > QUESTION_MAX_LENGTH)

const examples = [
  '苯酚的酸性为什么比碳酸弱？',
  '苯使溴水褪色和苯酚使溴水褪色有什么本质区别？',
  '乙醇能不能和碳酸钠反应放二氧化碳？',
]

async function submit(): Promise<void> {
  if (useSync.value) await ask.submitSync()
  else await ask.submit()
}

function useExample(q: string): void {
  ask.question = q
  inputRef.value?.focus()
}

onMounted(async () => {
  await health.refresh()
})
</script>

<template>
  <div class="ask-view">
    <header class="ask-view__head">
      <h2 class="ask-view__title">有机化学问答</h2>
      <HealthBadge :health="health" />
    </header>

    <!-- 提问区 -->
    <form class="composer" @submit.prevent="submit">
      <label class="composer__label" for="question">你的问题</label>
      <textarea
        id="question"
        ref="inputRef"
        v-model="ask.question"
        class="composer__input"
        :class="{ 'composer__input--over': overLimit }"
        :placeholder="examples[0]"
        rows="3"
        :maxlength="QUESTION_MAX_LENGTH + 200"
        :disabled="ask.isStreaming"
        @keydown.ctrl.enter.prevent="submit"
        @keydown.meta.enter.prevent="submit"
      />
      <div class="composer__bar">
        <div class="composer__left">
          <span class="composer__count" :class="{ 'composer__count--over': overLimit }">
            {{ charCount }} / {{ QUESTION_MAX_LENGTH }}
          </span>
          <label class="composer__mode" title="同步接口用于对照验证 SSE 是否真的在逐字输出">
            <input v-model="useSync" type="checkbox" :disabled="ask.isStreaming" />
            <span>同步模式</span>
          </label>
        </div>

        <div class="composer__actions">
          <button
            v-if="ask.isStreaming"
            type="button"
            class="btn btn--ghost"
            @click="ask.stop()"
          >
            停止
          </button>
          <button
            v-else
            type="submit"
            class="btn btn--primary"
            :disabled="!ask.canAsk || overLimit"
          >
            提问
          </button>
        </div>
      </div>
      <p class="composer__hint">Ctrl + Enter 发送</p>
    </form>

    <!-- 示例问题 -->
    <div v-if="!ask.hasContent && !ask.isStreaming" class="examples">
      <p class="examples__title">试试这些</p>
      <button
        v-for="q in examples"
        :key="q"
        type="button"
        class="examples__item"
        @click="useExample(q)"
      >
        {{ q }}
      </button>
    </div>

    <!-- 阶段提示 -->
    <p v-if="ask.isStreaming && ask.stageMessage" class="stage" role="status" aria-live="polite">
      <span class="stage__spinner" aria-hidden="true" />
      {{ ask.stageMessage }}
      <span v-if="ask.requestId" class="stage__rid">{{ ask.requestId.slice(0, 8) }}</span>
    </p>

    <!-- 错误 -->
    <div v-if="ask.phase === 'error' && ask.error" class="alert alert--err" role="alert">
      <span class="alert__msg">{{ ask.error.message }}</span>
      <code class="alert__code">{{ ask.error.code }}</code>
    </div>

    <p v-else-if="ask.phase === 'cancelled'" class="alert alert--warn">
      已停止。{{ ask.explanation ? '下方保留了已收到的部分内容。' : '' }}
    </p>

    <!-- 答复 -->
    <article v-if="ask.hasContent" class="answer">
      <div class="answer__body">{{ ask.explanation }}</div>

      <footer v-if="ask.steps > 0 || ask.elapsedSeconds > 0" class="answer__stats">
        <span v-if="ask.steps > 0">{{ ask.steps }} 轮推理</span>
        <span v-if="ask.elapsedSeconds > 0">{{ ask.elapsedSeconds.toFixed(1) }} 秒</span>
        <span v-if="ask.invocations.length > 0">
          调用 {{ ask.invocations.map((i) => i.tool).join('、') }}
        </span>
      </footer>

      <SourceList
        :sources="ask.sources"
        :invocations="ask.invocations"
        :retrieved="retrieved"
      />
    </article>
  </div>
</template>

<style scoped>
.ask-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.ask-view__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.ask-view__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
}

/* --- 提问区 --- */
.composer {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--surface);
  padding: 0.75rem;
}

.composer__label {
  display: block;
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 0.375rem;
}

.composer__input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  background: var(--surface-2);
  color: var(--text);
  font: inherit;
  font-size: 0.875rem;
  line-height: 1.6;
  padding: 0.5rem 0.625rem;
  resize: vertical;
  min-height: 4.5rem;
}

.composer__input:focus {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
  border-color: transparent;
}

.composer__input--over {
  border-color: var(--danger);
}

.composer__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 0.5rem;
  gap: 0.75rem;
}

.composer__left {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.composer__count {
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.composer__count--over {
  color: var(--danger);
  font-weight: 600;
}

.composer__mode {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
  cursor: pointer;
}

.composer__actions {
  display: flex;
  gap: 0.5rem;
}

.composer__hint {
  margin: 0.375rem 0 0;
  font-size: 0.625rem;
  color: var(--text-muted);
}

/* --- 按钮 --- */
.btn {
  font: inherit;
  font-size: 0.8125rem;
  padding: 0.375rem 0.875rem;
  border-radius: 0.375rem;
  border: 1px solid transparent;
  cursor: pointer;
  transition: background-color 0.12s;
}

.btn--primary {
  background: var(--accent);
  color: #fff;
}

.btn--primary:disabled {
  background: var(--surface-3);
  color: var(--text-muted);
  cursor: not-allowed;
}

.btn--ghost {
  background: transparent;
  border-color: var(--border);
  color: var(--text-secondary);
}

.btn--ghost:hover {
  background: var(--surface-2);
}

/* --- 示例 --- */
.examples {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.examples__title {
  font-size: 0.6875rem;
  color: var(--text-muted);
  margin: 0;
}

.examples__item {
  font: inherit;
  font-size: 0.75rem;
  text-align: left;
  padding: 0.4375rem 0.625rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.examples__item:hover {
  border-color: var(--accent);
  color: var(--text);
}

/* --- 阶段提示 --- */
.stage {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  color: var(--text-secondary);
  margin: 0;
}

.stage__spinner {
  width: 0.75rem;
  height: 0.75rem;
  border: 2px solid var(--border-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* 尊重用户的动效偏好设置—— spinning 元素可能引发前庭不适 */
@media (prefers-reduced-motion: reduce) {
  .stage__spinner {
    animation: none;
    border-top-color: var(--border-strong);
  }
}

.stage__rid {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--text-muted);
}

/* --- 提示条 --- */
.alert {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.8125rem;
  padding: 0.5rem 0.625rem;
  border-radius: 0.375rem;
  border: 1px solid;
  margin: 0;
}

.alert--err {
  color: var(--danger);
  border-color: var(--danger);
  background: color-mix(in srgb, var(--danger) 8%, transparent);
}

.alert--warn {
  color: var(--warning);
  border-color: var(--warning);
  background: color-mix(in srgb, var(--warning) 8%, transparent);
}

.alert__code {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  opacity: 0.8;
}

/* --- 答复 --- */
.answer {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.answer__body {
  font-size: 0.9375rem;
  line-height: 1.8;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}

.answer__stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}
</style>
