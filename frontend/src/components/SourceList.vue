<script setup lang="ts">
/**
 * 知识来源展示区。
 *
 * ## 关键设计：区分「没有来源」与「未检索」
 *
 * 后端在三种情况下都返回 `sources: []`：
 * 1. 检索工具成功但没命中任何段落；
 * 2. 根本没调用检索工具（纯模型知识回答）；
 * 3. 检索失败。
 *
 * **三者对学生意味着完全不同的结果**，故不能一律显示「无来源」。
 * 本组件据`tool_invocations` 中是否有 `search_knowledge`
 * 及其 `ok` 状态来区分。
 */
import { computed } from 'vue'

import type { SourceItem, ToolInvocation } from '../types/api'

const props = defineProps<{
  sources: SourceItem[]
  invocations: ToolInvocation[]
  /** 检索工具是否被调用过。 */
  retrieved: boolean
}>()

/** 检索是否失败。 */
const retrievalFailed = computed(
  () => props.retrieved && props.invocations.some((i) => i.tool === 'search_knowledge' && !i.ok),
)

/**
 * 审核状态的中文说明。
 *
 * 后端恒返回 `unknown`——它刻意不写「已审核」，因为那是人的判断。
 * 前端必须如实呈现，**不得渲染成「已认证」**。
 */
function reviewLabel(status: string): string {
  switch (status) {
    case 'approved':
      return '已审核'
    case 'pending_review':
      return '待审核'
    case 'rejected':
      return '未通过'
    default:
      return '内容待化学教师审核'
  }
}

/** 定位类型的中文名。 */
function locatorLabel(kind: string): string {
  switch (kind) {
    case 'page':
      return '页码'
    case 'chapter':
      return '章节'
    case 'section':
      return '小节'
    default:
      return '位置'
  }
}
</script>

<template>
  <section class="sources" aria-labelledby="sources-title">
    <h3 id="sources-title" class="sources__title">
      依据来源
      <span v-if="sources.length > 0" class="sources__count">{{ sources.length }} 条</span>
    </h3>

    <!-- 情况 1：检索失败 -->
    <p v-if="retrievalFailed" class="sources__notice sources__notice--warn">
      知识库检索未成功，下面的答复可能不完整。
    </p>

    <!-- 情况 2：调用了检索但没命中 -->
    <p v-else-if="retrieved && sources.length === 0" class="sources__notice">
      已检索知识库，但未找到相关段落。答复来自模型自身知识，<strong>未经讲义核实</strong>。
    </p>

    <!-- 情况 3：根本没调用检索 -->
    <p v-else-if="!retrieved" class="sources__notice">
      本次未使用知识库检索，答复来自模型自身知识。
    </p>

    <!-- 正常情况：列出来源 -->
    <ul v-if="sources.length > 0" class="sources__list">
      <li v-for="(s, i) in sources" :key="`${s.source_id}-${i}`" class="source">
        <div class="source__head">
          <span class="source__title">{{ s.title }}</span>
          <span v-if="s.locator" class="source__locator">
            <span class="source__locator-kind">{{ locatorLabel(s.locator_kind) }}</span>
            {{ s.locator }}
          </span>
        </div>
        <div class="source__meta">
          <span class="source__id">{{ s.source_id }}</span>
          <span v-if="s.version" class="source__version">{{ s.version }}</span>
          <span class="source__review" :data-status="s.review_status">{{ reviewLabel(s.review_status) }}</span>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.sources {
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
}

.sources__title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0 0 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sources__count {
  font-weight: 400;
  font-size: 0.75rem;
  color: var(--text-muted);
  background: var(--surface-2);
  padding: 0.0625rem 0.375rem;
  border-radius: 0.75rem;
}

.sources__notice {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0;
  line-height: 1.6;
}

.sources__notice--warn {
  color: var(--warning);
}

.sources__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.source {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  padding: 0.5rem 0.625rem;
}

.source__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
}

.source__title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text);
}

.source__locator {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.source__locator-kind {
  color: var(--text-muted);
  margin-right: 0.25rem;
}

.source__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.25rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.source__review {
  font-family: var(--font-sans);
}

/* 待审核状态用中性色而非绿色——避免学生误认为已通过审核 */
.source__review[data-status='unknown'],
.source__review[data-status='pending_review'] {
  color: var(--text-muted);
}

.source__review[data-status='approved'] {
  color: var(--success);
}
</style>
