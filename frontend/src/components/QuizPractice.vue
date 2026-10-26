<script setup lang="ts">
/**
 * 习题练习视图（feat/quiz-practice）。
 *
 * ## 与方案 C 的对应
 *
 * - **判分确定性**：提交后由后端确定性比对，前端只负责展示结果与讲解，
 *   不参与"谁对谁错"的判断（避免前端越权）。
 * - **讲解可降级**：错题讲解优先用模型，模型不可用时后端回退到
 *   题库解析 + 教材片段；前端据 `llm_available` 如实提示，不假装智能。
 * - **错题本不跨设备**：仅存 localStorage，且携带题目快照，
 *   故「重练错题」离线可用。
 */
import { computed, onMounted } from 'vue'

import { useQuizStore } from '../stores'
import type { QuizQuestion, QuizQuestionType } from '../types/api'

const quiz = useQuizStore()

/** 题型中文标签。 */
const TYPE_LABEL: Record<QuizQuestionType, string> = {
  choice: '选择题',
  fill: '填空题',
  truefalse: '判断题',
}

/** 选项字母表（与后端、store 保持一致）。 */
const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F'] as const

/** 当前筛选条件下的题目（重练错题时即错题本里的快照）。 */
const visibleQuestions = computed<QuizQuestion[]>(() => quiz.questions)

const isGraded = computed(() => quiz.phase === 'graded')
const canSubmit = computed(() => quiz.canSubmit)
const scorePercent = computed(() =>
  quiz.totalCount > 0 ? Math.round((quiz.correctCount / quiz.totalCount) * 100) : 0,
)

/** 判断题选项的展示值：学生提交 true/false，界面显示"正确/错误"。 */
function trueFalseLabel(v: string): string {
  if (v === 'true') return '正确'
  if (v === 'false') return '错误'
  return v
}

/** 标准答案的展示文本。 */
function correctAnswerText(q: QuizQuestion): string {
  if (q.type === 'choice' && q.options) {
    const idx = LETTERS.indexOf(q.answer as (typeof LETTERS)[number])
    return idx >= 0 ? `${q.answer}. ${q.options[idx]}` : q.answer
  }
  if (q.type === 'truefalse') return trueFalseLabel(q.answer)
  // 填空：多个可接受答案用分隔符展示
  return q.answer.split('|').join(' / ')
}

/** 学生作答的展示文本（已提交的作答）。 */
function studentAnswerText(q: QuizQuestion): string {
  const v = quiz.answerOf(q.id)
  if (!v) return '（未作答）'
  if (q.type === 'choice' && q.options) {
    const idx = LETTERS.indexOf(v as (typeof LETTERS)[number])
    return idx >= 0 ? `${v}. ${q.options[idx]}` : v
  }
  if (q.type === 'truefalse') return trueFalseLabel(v)
  return v
}

function loadWithFilters(): void {
  void quiz.load({
    topic: quiz.topicFilter || undefined,
    type: quiz.typeFilter || undefined,
  })
}

function onTopicChange(e: Event): void {
  quiz.setTopicFilter((e.target as HTMLSelectElement).value)
}
function onTypeChange(e: Event): void {
  quiz.setTypeFilter((e.target as HTMLSelectElement).value)
}

onMounted(async () => {
  if (quiz.phase === 'idle') await quiz.load()
})
</script>

<template>
  <div class="quiz-view">
    <header class="quiz-view__head">
      <h2 class="quiz-view__title">习题练习</h2>
      <p class="quiz-view__hint">巩固知识点 · 判分由题库确定，错题本仅保存在本机</p>
    </header>

    <!-- 筛选与操作 -->
    <div class="filters">
      <label class="filters__field">
        <span class="filters__label">知识点</span>
        <select class="filters__select" :value="quiz.topicFilter" @change="onTopicChange">
          <option value="">全部</option>
          <option v-for="t in quiz.topics" :key="t" :value="t">{{ t }}</option>
        </select>
      </label>

      <label class="filters__field">
        <span class="filters__label">题型</span>
        <select class="filters__select" :value="quiz.typeFilter" @change="onTypeChange">
          <option value="">全部</option>
          <option v-for="(label, key) in TYPE_LABEL" :key="key" :value="key">{{ label }}</option>
        </select>
      </label>

      <button type="button" class="btn btn--ghost" :disabled="quiz.loading" @click="loadWithFilters">
        {{ quiz.loading ? '加载中…' : '加载习题' }}
      </button>

      <button
        v-if="quiz.wrongBook.length > 0"
        type="button"
        class="btn btn--ghost"
        @click="quiz.rePracticeWrong()"
      >
        重练错题（{{ quiz.wrongBook.length }}）
      </button>
    </div>

    <!-- 加载态 -->
    <p v-if="quiz.phase === 'loading'" class="stage" role="status">
      <span class="stage__spinner" aria-hidden="true" />
      正在加载习题…
    </p>

    <!-- 错误态 -->
    <div v-else-if="quiz.phase === 'error' && quiz.loadError" class="alert alert--err" role="alert">
      <span class="alert__msg">{{ quiz.loadError.message }}</span>
      <code class="alert__code">{{ quiz.loadError.code }}</code>
    </div>

    <!-- 题目区 -->
    <div v-else-if="visibleQuestions.length > 0" class="quiz">
      <!-- 成绩摘要（判分后） -->
      <div v-if="isGraded" class="score" :class="{ 'score--full': quiz.correctCount === quiz.totalCount }">
        <span class="score__num">{{ quiz.correctCount }} / {{ quiz.totalCount }}</span>
        <span class="score__pct">正确率 {{ scorePercent }}%</span>
        <button type="button" class="btn btn--ghost score__retry" @click="quiz.resetAnswers()">
          再答一次
        </button>
      </div>

      <ol class="qlist">
        <li v-for="(q, i) in visibleQuestions" :key="q.id" class="qcard">
          <div class="qcard__head">
            <span class="qcard__no">{{ i + 1 }}</span>
            <span class="qcard__meta">{{ q.topic }} · {{ TYPE_LABEL[q.type] }}</span>
            <!-- 判分后显示对错徽标 -->
            <span
              v-if="isGraded && quiz.results[q.id]"
              class="badge"
              :class="quiz.results[q.id].ok ? 'badge--ok' : 'badge--no'"
            >
              {{ quiz.results[q.id].ok ? '答对' : '答错' }}
            </span>
          </div>

          <p class="qcard__stem">{{ q.stem }}</p>

          <!-- 选择题 -->
          <div v-if="q.type === 'choice'" class="opts">
            <label v-for="(opt, idx) in (q.options ?? [])" :key="idx" class="opt">
              <input
                type="radio"
                :name="`q-${q.id}`"
                :value="LETTERS[idx]"
                :checked="quiz.answerOf(q.id) === LETTERS[idx]"
                :disabled="isGraded"
                @change="quiz.setAnswer(q.id, LETTERS[idx])"
              />
              <span class="opt__mark">{{ LETTERS[idx] }}</span>
              <span class="opt__text">{{ opt }}</span>
            </label>
          </div>

          <!-- 判断题 -->
          <div v-else-if="q.type === 'truefalse'" class="opts opts--inline">
            <label class="opt">
              <input
                type="radio"
                :name="`q-${q.id}`"
                value="true"
                :checked="quiz.answerOf(q.id) === 'true'"
                :disabled="isGraded"
                @change="quiz.setAnswer(q.id, 'true')"
              />
              <span class="opt__text">正确</span>
            </label>
            <label class="opt">
              <input
                type="radio"
                :name="`q-${q.id}`"
                value="false"
                :checked="quiz.answerOf(q.id) === 'false'"
                :disabled="isGraded"
                @change="quiz.setAnswer(q.id, 'false')"
              />
              <span class="opt__text">错误</span>
            </label>
          </div>

          <!-- 填空题 -->
          <div v-else class="fill">
            <input
              type="text"
              class="fill__input"
              :value="quiz.answerOf(q.id)"
              :disabled="isGraded"
              placeholder="输入答案"
              @input="quiz.setAnswer(q.id, ($event.target as HTMLInputElement).value)"
            />
          </div>

          <!-- 判分后：标准答案 + 解析/讲解 -->
          <div v-if="isGraded && quiz.results[q.id]" class="feedback">
            <p class="feedback__row">
              <span class="feedback__key">你的答案：</span>
              <span :class="quiz.results[q.id].ok ? 'txt--ok' : 'txt--no'">
                {{ studentAnswerText(q) }}
              </span>
            </p>
            <p v-if="!quiz.results[q.id].ok" class="feedback__row">
              <span class="feedback__key">正确答案：</span>
              <span class="txt--ok">{{ correctAnswerText(q) }}</span>
            </p>

            <!-- 讲解按钮（仅答错时提供） -->
            <button
              v-if="!quiz.results[q.id].ok"
              type="button"
              class="btn btn--ghost feedback__explain-btn"
              :disabled="quiz.explaining[q.id]"
              @click="quiz.explain(q.id)"
            >
              {{ quiz.explaining[q.id] ? '讲解生成中…' : '查看讲解' }}
            </button>

            <!-- 题库自带解析（始终展示） -->
            <div v-if="quiz.explanations[q.id] || quiz.results[q.id].explanation" class="explain">
              <p v-if="quiz.results[q.id].explanation" class="explain__line">
                <span class="explain__tag">解析</span>{{ quiz.results[q.id].explanation }}
              </p>
              <template v-if="quiz.explanations[q.id]">
                <p
                  v-for="(txt, ti) in quiz.explanations[q.id].remedy_texts"
                  :key="`r-${ti}`"
                  class="explain__line explain__line--remedy"
                >
                  <span class="explain__tag explain__tag--remedy">教材</span>{{ txt }}
                </p>
                <p v-if="quiz.explanations[q.id].llm_explanation" class="explain__line">
                  <span class="explain__tag explain__tag--llm">老师讲</span
                  >{{ quiz.explanations[q.id].llm_explanation }}
                </p>
                <p v-else class="explain__note">
                  智能讲解暂不可用，已展示题库解析与教材片段。
                </p>
              </template>
              <p v-if="quiz.explainErrors[q.id]" class="alert alert--err explain__err">
                <span class="alert__msg">{{ quiz.explainErrors[q.id]!.message }}</span>
              </p>
            </div>
          </div>
        </li>
      </ol>

      <!-- 提交 -->
      <div v-if="!isGraded" class="quiz__submit">
        <span class="quiz__progress">已答 {{ quiz.answeredCount }} / {{ visibleQuestions.length }}</span>
        <button type="button" class="btn btn--primary" :disabled="!canSubmit" @click="quiz.submit()">
          {{ quiz.phase === 'grading' ? '判分中…' : '提交并判分' }}
        </button>
      </div>
      <p v-else class="quiz__saved">
        本次答错的题已保存到本机错题本（仅本设备可见）。
      </p>
    </div>

    <!-- 空态 -->
    <p v-else class="empty">暂无习题。请调整筛选条件后点击「加载习题」。</p>

    <!-- 错题本面板 -->
    <section v-if="quiz.wrongBook.length > 0" class="wrongbook">
      <div class="wrongbook__head">
        <h3 class="wrongbook__title">本机错题本（{{ quiz.wrongBook.length }}）</h3>
        <button type="button" class="btn btn--ghost" @click="quiz.clearWrongBook()">清空</button>
      </div>
      <ul class="wrongbook__list">
        <li v-for="e in quiz.wrongBook" :key="e.id" class="wrongbook__item">
          <div class="wrongbook__body">
            <span class="wrongbook__meta">{{ e.question.topic }} · {{ TYPE_LABEL[e.question.type] }}</span>
            <p class="wrongbook__stem">{{ e.question.stem }}</p>
          </div>
          <div class="wrongbook__actions">
            <button type="button" class="btn btn--ghost" @click="quiz.rePracticeWrong()">重练</button>
            <button type="button" class="btn btn--ghost" @click="quiz.removeFromWrongBook(e.id)">移除</button>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.quiz-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.quiz-view__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.quiz-view__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
}

.quiz-view__hint {
  font-size: 0.6875rem;
  color: var(--text-muted);
  margin: 0;
}

/* --- 筛选 --- */
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.5rem;
}

.filters__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.filters__label {
  font-size: 0.6875rem;
  color: var(--text-muted);
}

.filters__select {
  font: inherit;
  font-size: 0.8125rem;
  padding: 0.375rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  background: var(--surface-2);
  color: var(--text);
}

/* --- 按钮（与 AskView 同款） --- */
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

.btn--ghost:hover:not(:disabled) {
  background: var(--surface-2);
}

.btn--ghost:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* --- 成绩 --- */
.score {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--surface);
}

.score--full {
  border-color: var(--accent);
}

.score__num {
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--text);
}

.score__pct {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.score__retry {
  margin-left: auto;
}

/* --- 题目列表 --- */
.qlist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  counter-reset: none;
}

.qcard {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--surface);
  padding: 0.75rem;
}

.qcard__head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.qcard__no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.375rem;
  height: 1.375rem;
  border-radius: 50%;
  background: var(--surface-2);
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
}

.qcard__meta {
  font-size: 0.6875rem;
  color: var(--text-muted);
}

.qcard__stem {
  margin: 0 0 0.625rem;
  font-size: 0.9375rem;
  line-height: 1.7;
  color: var(--text);
}

/* --- 选项 --- */
.opts {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.opts--inline {
  flex-direction: row;
  gap: 1rem;
}

.opt {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--text);
  cursor: pointer;
  padding: 0.25rem 0.375rem;
  border-radius: 0.375rem;
}

.opt:hover {
  background: var(--surface-2);
}

.opt__mark {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-secondary);
}

/* --- 填空 --- */
.fill__input {
  width: 100%;
  font: inherit;
  font-size: 0.875rem;
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  background: var(--surface-2);
  color: var(--text);
}

.fill__input:focus {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
  border-color: transparent;
}

/* --- 反馈 --- */
.feedback {
  margin-top: 0.625rem;
  padding-top: 0.625rem;
  border-top: 1px dashed var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.feedback__row {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.feedback__key {
  color: var(--text-muted);
}

.feedback__explain-btn {
  align-self: flex-start;
  margin-top: 0.125rem;
}

.txt--ok {
  color: var(--accent);
  font-weight: 600;
}

.txt--no {
  color: var(--danger);
  font-weight: 600;
}

/* --- 讲解 --- */
.explain {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  margin-top: 0.25rem;
}

.explain__line {
  margin: 0;
  font-size: 0.8125rem;
  line-height: 1.7;
  color: var(--text-secondary);
}

.explain__line--remedy {
  color: var(--text);
}

.explain__tag {
  display: inline-block;
  margin-right: 0.375rem;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
  font-size: 0.625rem;
  background: var(--surface-3);
  color: var(--text-secondary);
}

.explain__tag--remedy {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  color: var(--text);
}

.explain__tag--llm {
  background: color-mix(in srgb, var(--warning, #b8860b) 20%, transparent);
  color: var(--text);
}

.explain__note {
  margin: 0;
  font-size: 0.75rem;
  color: var(--text-muted);
}

.explain__err {
  margin: 0;
}

/* --- 提交区 --- */
.quiz__submit {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.quiz__progress {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.quiz__saved {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0;
}

.empty {
  font-size: 0.875rem;
  color: var(--text-muted);
}

/* --- 错题本 --- */
.wrongbook {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--surface);
  padding: 0.75rem;
}

.wrongbook__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.wrongbook__title {
  font-size: 0.875rem;
  font-weight: 600;
  margin: 0;
}

.wrongbook__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.wrongbook__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  background: var(--surface-2);
}

.wrongbook__meta {
  font-size: 0.6875rem;
  color: var(--text-muted);
}

.wrongbook__stem {
  margin: 0.125rem 0 0;
  font-size: 0.8125rem;
  color: var(--text);
}

.wrongbook__actions {
  display: flex;
  gap: 0.375rem;
  flex-shrink: 0;
}

/* --- 徽标 --- */
.badge {
  margin-left: auto;
  font-size: 0.6875rem;
  padding: 0.0625rem 0.4375rem;
  border-radius: 0.75rem;
  font-weight: 600;
}

.badge--ok {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  color: var(--accent);
}

.badge--no {
  background: color-mix(in srgb, var(--danger) 14%, transparent);
  color: var(--danger);
}

/* --- 加载态 --- */
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

@media (prefers-reduced-motion: reduce) {
  .stage__spinner {
    animation: none;
    border-top-color: var(--border-strong);
  }
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

.alert__code {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  opacity: 0.8;
}
</style>
