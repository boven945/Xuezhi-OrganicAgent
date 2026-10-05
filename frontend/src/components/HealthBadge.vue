<script setup lang="ts">
/**
 * 后端健康状态条。
 *
 * ## 为什么值得单独一个组件
 *
 * 本机实测发现 RDKit 被应用控制策略拦截，此时服务仍能启动但
 * `chem: ready=false`。**如实展示这一点比让用户以为"全好"更重要**——
 * 否则学生会发现分子解析不能用，却不知道原因。
 */
import { computed } from 'vue'

import type { useHealthStore } from '../stores'

const props = defineProps<{ health: ReturnType<typeof useHealthStore> }>()

const badge = computed(() => {
  if (props.health.reachable === false) return { text: '后端未连接', cls: 'err' }
  switch (props.health.status) {
    // **取值必须与后端实际返回的一致**（见 types/api.ts HealthResponse）。
    // 后端产出 `ok` / `degraded` / `not_ready`。
    // 实测踩过：原先这里写 `healthy`，而后端从不返回它——
    // 一切正常时反而落到 default 显示「检查中」，状态灯永远不熄。
    case 'ok':
      return { text: '服务正常', cls: 'ok' }
    case 'degraded':
      return { text: '部分降级', cls: 'warn' }
    case 'not_ready':
    case 'unhealthy':
      return { text: '服务异常', cls: 'err' }
    default:
      // **未知取值不静默显示「检查中」**：那会被误读成"还在加载"，
      // 于是真的坏了也不报警。改为显式暴露原值，便于一眼看出契约又变了。
      return { text: `未知状态(${props.health.status})`, cls: 'err' }
  }
})
</script>

<template>
  <div class="health" :data-status="badge.cls">
    <span class="health__dot" aria-hidden="true" />
    <span class="health__text">{{ badge.text }}</span>

    <details v-if="health.components.length > 0" class="health__detail">
      <summary class="health__summary">组件</summary>
      <ul class="health__list">
        <li v-for="c in health.components" :key="c.name" class="health__item">
          <span class="health__name">{{ c.name }}</span>
          <span class="health__state" :class="c.ready ? 'ok' : 'err'">
            {{ c.ready ? '可用' : '不可用' }}
          </span>
          <span v-if="!c.ready" class="health__reason">{{ c.detail }}</span>
        </li>
      </ul>
    </details>
  </div>
</template>

<style scoped>
.health {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: var(--text-secondary);
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  background: var(--surface-2);
}

.health__dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}

.health[data-status='ok'] .health__dot {
  background: var(--success);
}
.health[data-status='warn'] .health__dot {
  background: var(--warning);
}
.health[data-status='err'] .health__dot {
  background: var(--danger);
}

.health__detail {
  margin-left: 0.25rem;
}

.health__summary {
  cursor: pointer;
  color: var(--text-muted);
  font-size: 0.6875rem;
}

.health__list {
  list-style: none;
  margin: 0.375rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1875rem;
  position: absolute;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  padding: 0.5rem;
  min-width: 14rem;
  z-index: 20;
  box-shadow: 0 4px 12px rgb(0 0 0 / 12%);
}

.health__item {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.6875rem;
}

.health__name {
  font-family: var(--font-mono);
  color: var(--text);
  min-width: 2.5rem;
}

.health__state.ok {
  color: var(--success);
}
.health__state.err {
  color: var(--danger);
}

.health__reason {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 0.625rem;
}
</style>
