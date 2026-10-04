<script setup lang="ts">
/**
 * 应用外壳与标签切换。
 *
 * 刻意不引入 vue-router：**当前只有两个视图且互斥显示**，
 * 用路由反而要处理「刷新后落在哪个视图」这类无意义状态。
 * 等真有多页面需求时再引入（决策登记表的对应项保持待决策）。
 */
import { ref } from 'vue'

import AskView from './components/AskView.vue'
import MoleculeView from './components/MoleculeView.vue'

type Tab = 'ask' | 'molecule'

const tab = ref<Tab>('ask')

const tabs: { id: Tab; label: string; hint: string }[] = [
  { id: 'ask', label: '问答', hint: '检索讲义并作答' },
  { id: 'molecule', label: '分子解析', hint: 'SMILES 结构与官能团' },
]
</script>

<template>
  <div class="app">
    <header class="app__header">
      <div class="app__brand">
        <span class="app__name">学智有机</span>
        <span class="app__full">有机化学智能诊断</span>
      </div>
    </header>

    <nav class="tabs" role="tablist">
      <button
        v-for="t in tabs"
        :key="t.id"
        type="button"
        role="tab"
        class="tabs__btn"
        :class="{ 'tabs__btn--active': tab === t.id }"
        :aria-selected="tab === t.id"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </nav>

    <main class="app__main">
      <AskView v-if="tab === 'ask'" />
      <MoleculeView v-else />
    </main>

    <footer class="app__footer">
      <p>
        答复由大模型生成，可能存在错误。关键知识点请以教材为准。
        <span class="app__disclaimer">内容待化学教师审核</span>
      </p>
    </footer>
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  max-width: 52rem;
  margin: 0 auto;
  padding: 1rem;
  gap: 1rem;
}

.app__header {
  padding-top: 0.5rem;
}

.app__brand {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.app__name {
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.05em;
}

.app__full {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--border);
}

.tabs__btn {
  font: inherit;
  font-size: 0.8125rem;
  padding: 0.4375rem 0.75rem;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  cursor: pointer;
  margin-bottom: -1px;
}

.tabs__btn--active {
  color: var(--text);
  border-bottom-color: var(--accent);
  font-weight: 600;
}

.app__main {
  flex: 1;
}

.app__footer {
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
  line-height: 1.6;
}

.app__footer p {
  margin: 0;
}

.app__disclaimer {
  display: inline-block;
  margin-left: 0.25rem;
  padding: 0.0625rem 0.375rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
}
</style>
