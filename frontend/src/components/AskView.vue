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
import { renderMarkdown, stripMarkdown } from '../api/markdown'
import DigitalHuman from '../components/DigitalHuman.vue'
import SpeechButton from '../components/SpeechButton.vue'
import SourceList from '../components/SourceList.vue'
import { QUESTION_MAX_LENGTH, useAskStore, useHealthStore } from '../stores'

const ask = useAskStore()
const health = useHealthStore()

/**
 * 语音是否正在朗读——数字人的口型靠它。
 *
 * **刻意用 boolean 而非文本**：数字人只关心"现在在不在说话"，
 * 传文本会让它持有答案的引用，而它并不需要内容。
 */
const speakingNow = ref(false)

/**
 * 本会话的 Fay 标识。
 *
 * **必须与后端推 Fay 时传的 `user` 完全一致**（实测）：
 * Fay 的 `get_client_output(user)` 按 username 精确匹配，
 * 不一致则推送被**静默过滤**——不报错，只是收不到。
 *
 * 生成一次后固定：中途变化会让已登记的连接失效。
 */
const sessionUser = `stu-${Math.random().toString(36).slice(2, 10)}`

/**
 * 渲染后的 HTML。
 *
 * 用 computed 而非在模板里调函数：函数每次重渲都跑一次，
 * 而 marked.parse 不是免费的。
 */
const renderedExplanation = computed(() => renderMarkdown(ask.explanation ?? ''))

/**
 * 供朗读的纯文本。
 *
 * **必须去标记**：朗读 `**羟基**` 会把星号念出来。
 */
const spokenText = computed(() => stripMarkdown(ask.explanation ?? ''))

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
    <div class="teacher">
      <DigitalHuman
        class="teacher__avatar"
        :fay-enabled="health.fayEnabled"
        :speaking-text="speakingNow ? (ask.explanation ?? null) : null"
        :user="sessionUser"
      />

      <div class="teacher__answer">
      <!-- 无答复时的引导。形象已在左侧待机，
           这里顺带告诉学生能问什么。 -->
      <p v-if="!ask.hasContent" class="teacher__waiting">
        向老师提问吧——可以问物质性质、反应条件或官能团区别。
      </p>

      <article v-if="ask.hasContent" class="answer">
      <!-- 答复正文。**必须渲染 Markdown**——实测模型返回
           `**羟基**` 这类标记，纯文本插值会让主交付显示原始符号。
           renderMarkdown 内部已消毒，可安全用于 v-html。 -->
      <!-- eslint-disable-next-line vue/no-v-html -- 已消毒 -->
      <div class="answer__body" v-html="renderedExplanation" />

      <!-- 语音朗读。放在正文之后、来源之前：它是**增强**，
           不该抢主交付（正文）的注意力。 -->
      <SpeechButton
        :text="spokenText"
        :health="health"
        @speaking-change="speakingNow = $event"
      />

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
    </div>
  </div>
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

    <!-- 分屏：左侧数字人常驻，右侧为答复区。
        窄屏时由 CSS 塌成单列（见 .stage 的 grid-template-areas）。

        **数字人不受 ask.hasContent 控制**（实测踩过）：
        原先把它与答复一起放进 `v-if="ask.hasContent"`，
        于是学生刚打开页面、还没提问时**整个形象不显示**——
        一个"老师"在学生举手前就消失了，不符合直觉。

        正确形态是**老师一直站在讲台上**，只是没在说话
        （形象为待机态）。故左列常驻，右列才按需出现。
        -->

</template>

<style scoped>
.ask-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

/**
 * 分屏布局：左数字人、右答复。
 *
 * ## 为什么用 grid 而非 flex
 *
 * 需要在窄屏时把「数字人」整块挪到上方。用 flex 得靠
 * `order` + `flex-wrap`，而 order 视觉顺序与 DOM 顺序不一致，
 * 键盘 Tab 走的却是 DOM 顺序——会让人Tab 顺序与视觉顺序错位。
 * grid 的 `grid-template-areas` 没有这个问题。
 *
 * ## 断点 640px 的依据
 *
 * 数字人最窄也要 140px 才不至于把表情挤扁；加上答复区至少
 * 320px（中文一行约 20 字），640 是二者之和的下界。
 */
.teacher {
  display: grid;
  grid-template-areas: 'avatar answer';
  grid-template-columns: minmax(140px, 200px) 1fr;
  gap: 1rem;
  align-items: start;
}

.teacher__avatar {
  grid-area: avatar;
  /* 粘住：学生滚动长答案时形象仍在视野内，
     讲解时不会因滚下去而"消失"。 */
  position: sticky;
  top: 1rem;
}

/**
 * 矮视口时缩小形象。
 *
 * **实测**：1024px 的图按 max-width 220 缩放后仍 220px 高，
 * 而笔记本视口约 700px 高——形象一出现就把提问区顶出首屏，
 * 学生得滚动才能看到输入框。
 */
@media (max-height: 800px) {
  .teacher__avatar :deep(.avatar__img) {
    max-width: 168px;
  }
}

.teacher__answer {
  grid-area: answer;
  min-width: 0; /* 允许内部长内容收缩，否则会撑破 grid */
  /* 上内边距：形象列有"待机"提示行，右列不留白会让它
     紧贴提问框上沿（实测截图里两者几乎相连）。 */
  padding-top: 0.25rem;
}

/**
 * 无答复时的引导语。
 *
 * **为什么需要**：左列形象常驻，若右列完全空白，
 * 页面看起来像"加载失败"。一行提示既说明状态，
 * 也顺带告诉学生能问什么。
 *
 * 刻意用弱化色与较小字号——它不该抢提问框的注意力。
 */
.teacher__waiting {
  margin: 0;
  padding: 0.5rem 0;
  color: var(--text-muted, #888);
  font-size: 0.875rem;
}

@media (max-width: 560px) {
  .teacher {
    grid-template-areas:
      'avatar'
      'answer';
    grid-template-columns: 1fr;
  }

  .teacher__avatar {
    position: static;
  }
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
  /* **不要用 pre-wrap**（实测踩过，见 docs/frontend-verification.md）：
     marked 会在块级标签之间输出源码换行（`<ol>\n<li>…</li>\n<li>…`），
     而 pre-wrap 把这些换行**保留成真实行盒**——
     每个列表项之间凭空多出整整一行空白（实测 24px，正好一个行高）。
     对照实测：pre-wrap 间隙 24px，normal 间隙 0px。
     模型输出里的换行本就该由 Markdown 结构（段落/列表/`<br>`）表达，
     保留源码缩进只会制造噪声，故用默认的 normal。 */
  word-break: break-word;
}

.answer__body :deep(p) {
  /* 0.5em 而非 0.75em**：line-height 已是 1.8，
     再加 0.75em 外边距会让段落看起来像分了两次空行。 */
  margin: 0 0 0.5em;
}

.answer__body :deep(p:last-child) {
  margin-bottom: 0;
}

.answer__body :deep(h3),
.answer__body :deep(h4) {
  margin: 0.9em 0 0.4em;
  font-size: 1rem;
  font-weight: 500;
  color: var(--text);
}

.answer__body :deep(ul),
.answer__body :deep(ol) {
  /* **实测间距主因在这里**：浏览器给 ul 的默认 margin 很大，
     叠加 p 的 15px 下边距后，列表与上文之间空了整整两行。
     列表元素之间不需要那么松。 */
  margin: 0.3em 0 0.5em;
  padding-left: 1.4em;
}

.answer__body :deep(li) {
  margin: 0.2em 0;
}

.answer__body :deep(strong) {
  font-weight: 500;
  /* 不用纯黑：与正文同色，靠加粗本身已足够区分 */
  color: var(--text);
}

.answer__body :deep(blockquote) {
  margin: 0.6em 0;
  padding: 0.35em 0.8em;
  border-left: 3px solid var(--color-border-secondary, #d3d1c7);
  color: var(--text-secondary, #5f5e5a);
  font-size: 0.875rem;
}

.answer__body :deep(blockquote p) {
  margin: 0;
}

.answer__body :deep(code) {
  padding: 0.1em 0.3em;
  border-radius: 3px;
  background: var(--color-background-secondary, #f1efe8);
  font-size: 0.9em;
  font-family: var(--font-mono, monospace);
}

.answer__body :deep(a) {
  color: var(--color-text-info, #185fa5);
  text-decoration: underline;
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
