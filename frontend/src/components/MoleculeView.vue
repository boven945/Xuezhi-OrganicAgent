<script setup lang="ts">
/**
 * 分子结构解析视图。
 *
 * ## 为什么要单独一个视图
 *
 * `POST /api/v1/molecule` **不需要模型**，只靠 RDKit。
 * 这使它成为本机最好的演示入口：
 * - 无需 MaaS 密钥即可看到真实结果；
 * - 响应是毫秒级，不必等流式。
 *
 * 同时它也是「化学引擎是否可用」的直接探针——
 * 本机实测 RDKit 被应用控制策略拦截时，此处会明确报错。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'

import { fetchHealth } from '../api/client'
import type { VizData } from '../viz/types'
import { useMoleculeStore } from '../stores'

/**
 * 异步组件：three.js 只在真正打开 3D 视图时才下载。
 *
 * 实测依据：静态引入让主包从 **92 KB 涨到 634 KB**
 *（gzip 36→ 173 KB）。而多数会话只查官能团，
 * 不看 3D——让所有人先下载 540 KB 只为一个可能不看的功能不合理。
 *
 * 代价是首次打开有加载延迟，用组件内的提示如实告知。
 */
const MoleculeViewer3D = defineAsyncComponent(() => import('../viz/MoleculeViewer.vue'))

const mol = useMoleculeStore()

/**
 * 后端返回的 `viz_data`，收窄为 viz 模块的契约。
 *
 * **运行时校验在 `MoleculeViewer3D` 内部做**（`validateVizData`），
 * 这里只做编译期收窄，故用 `as` + `?? null`。
 */
const vizData = computed(() => {
  const structure = mol.result?.structure as { viz_data?: unknown } | null | undefined
  return (structure?.viz_data as VizData | undefined) ?? null
})

/**
 * 化学引擎是否可用。
 *
 * ## 为什么要先探再试
 *
 * 本机实测：RDKit 的 C++ 扩展被应用控制策略拦截时，
 * 后端 `/api/v1/molecule` 返回 **500「服务内部错误」**——
 * 这个文案是误导性的：不是服务内部崩了，是**一个可选组件不可用**。
 *
 * 若直接让用户点按钮，他会得到「服务内部错误」，
 * 既不知道是化学引擎的问题，也不知道自己该做什么。
 *
 * 故先查 `/health` 的 `chem` 分量：不可用就**提前说明原因与影响范围**。
 * 这不是掩盖错误——是给出可行动的判断。
 *
 * **已知的后端缺陷**（应在上游修，见决策登记表 I6）：
 * 后端把 RDKit 的 `ImportError` 归类为 `api_internal_error`（500），
 * 而更贴切的是 `api_not_ready`（503），或新增专门的组件不可用码。
 */
const chemAvailable = ref<boolean | null>(null)
const chemDetail = ref('')

onMounted(async () => {
  try {
    const h = await fetchHealth()
    const chem = h.components.find((c) => c.name === 'chem')
    chemAvailable.value = chem?.ready ?? null
    chemDetail.value = chem?.detail ?? ''
  } catch {
    // 探针本身失败：不去阻断功能，让用户实际试一次
    chemAvailable.value = null
  }
})

const examples = [
  { smiles: 'CCO', label: '乙醇' },
  { smiles: 'c1ccccc1O', label: '苯酚' },
  { smiles: 'CC(=O)O', label: '乙酸' },
  { smiles: 'CC(=O)OC', label: '乙酸乙酯' },
  { smiles: 'c1ccccc1CC', label: '乙苯' },
]

/**
 * 解析层级的说明。
 *
 * 后端如实返回它能达到的最深层级，前端据此告诉用户
 * 「这个结果能信到什么程度」——比直接给个结果更诚实。
 */
const verificationLabel = computed(() => {
  switch (mol.result?.verification) {
    case 'groups':
      return '已识别官能团'
    case 'properties':
      return '已计算分子性质'
    case 'validity':
      return '已通过结构合法性检查'
    case 'syntax':
      return '仅通过语法检查，化学上可能无效'
    default:
      return ''
  }
})

/**
 * 取性质字段的显示值。
 *
 * 取不到时显示占位而非 0——**0 是有效值**（如氢原子数为 0 是可能的），
 * 用 0 占位会让学生误读。
 */
function property(key: string, unit = ''): string {
  const props = mol.result?.properties as Record<string, unknown> | null | undefined
  const v = props?.[key]
  if (v === null || v === undefined || v === '') return '—'
  return `${String(v)}${unit}`
}
</script>

<template>
  <div class="mol-view">
    <header class="mol-view__head">
      <h2 class="mol-view__title">分子结构解析</h2>
      <span class="mol-view__note">无需模型，仅用 RDKit 推理</span>
    </header>

    <!-- 化学引擎不可用：提前说明，而不是让用户撞 500 -->
    <div v-if="chemAvailable === false" class="notice notice--warn" role="status">
      <strong>化学引擎当前不可用</strong>
      <p class="notice__body">
        结构式解析依赖 RDKit，本机未能加载
        <code v-if="chemDetail">{{ chemDetail }}</code>
        <code v-else>未知原因</code>。
        <strong>问答功能不受影响</strong>——检索与作答走的是另一条链路。
      </p>
    </div>

    <form class="smiles-form" @submit.prevent="mol.parse()">
      <label class="smiles-form__label" for="smiles">SMILES 结构式</label>
      <div class="smiles-form__row">
        <input
          id="smiles"
          v-model="mol.smiles"
          class="smiles-form__input"
          placeholder="CCO"
          autocomplete="off"
          spellcheck="false"
          :disabled="chemAvailable === false"
        />
        <button
          type="submit"
          class="btn btn--primary"
          :disabled="mol.loading || !mol.smiles.trim() || chemAvailable === false"
        >
          {{ mol.loading ? '解析中…' : '解析' }}
        </button>
      </div>
    </form>

    <div v-if="chemAvailable !== false" class="examples">
      <button
        v-for="e in examples"
        :key="e.smiles"
        type="button"
        class="examples__chip"
        :title="e.smiles"
        @click="((mol.smiles = e.smiles), mol.parse())"
      >
        {{ e.label }}
      </button>
    </div>

    <div v-if="mol.error" class="alert alert--err" role="alert">
      <span>{{ mol.error.message }}</span>
      <code class="alert__code">{{ mol.error.code }}</code>
    </div>

    <div v-else-if="mol.hasResult" class="result">
      <div class="result__head">
        <span class="result__smiles">{{ mol.result?.structure ? String((mol.result.structure as Record<string, unknown>).canonical_smiles ?? '') : mol.smiles }}</span>
        <span class="result__level">{{ verificationLabel }}</span>
      </div>

      <!-- 3D 视图。**放在性质之前**：先看结构，再看数据。
           组件内部自行处理 WebGL 不可用与坐标缺失，
           失败时只在此处降级，不影响下方性质与官能团。 -->
      <MoleculeViewer3D :viz="vizData" />

      <dl class="props">
        <div class="props__row">
          <dt>分子式</dt>
          <dd>{{ mol.formula || '—' }}</dd>
        </div>
        <div class="props__row">
          <dt>分子量</dt>
          <dd>{{ property('molecular_weight') }}</dd>
        </div>
        <div class="props__row">
          <dt>精确质量</dt>
          <dd>{{ property('exact_mass') }}</dd>
        </div>
      </dl>

      <div v-if="mol.groups.length > 0" class="groups">
        <p class="groups__title">识别到的官能团</p>
        <ul class="groups__list">
          <li v-for="g in mol.groups" :key="g.name" class="group">
            <span class="group__name">{{ g.name }}</span>
            <code class="group__smarts">{{ g.smarts }}</code>
            <span class="group__atoms">原子 {{ g.atom_indices.join(', ') }}</span>
          </li>
        </ul>
      </div>
      <p v-else class="result__empty">未识别到受支持的功能基团。</p>

      <ul v-if="(mol.result?.warnings ?? []).length > 0" class="warnings">
        <li v-for="(w, i) in mol.result?.warnings ?? []" :key="i" class="warning">{{ w }}</li>
      </ul>
    </div>

    <p v-else-if="!mol.loading" class="hint">
      输入 SMILES 或点上方示例。解析在本机完成，不调用模型。
    </p>
  </div>
</template>

<style scoped>
.mol-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.mol-view__head {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
}

.mol-view__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
}

.mol-view__note {
  font-size: 0.6875rem;
  color: var(--text-muted);
}

.smiles-form__label {
  display: block;
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 0.375rem;
}

.smiles-form__row {
  display: flex;
  gap: 0.5rem;
}

.smiles-form__input {
  flex: 1;
  font-family: var(--font-mono);
  font-size: 0.875rem;
  padding: 0.4375rem 0.625rem;
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  background: var(--surface-2);
  color: var(--text);
}

.smiles-form__input:focus {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.examples__chip {
  font: inherit;
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.75rem;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-secondary);
  cursor: pointer;
}

.examples__chip:hover {
  border-color: var(--accent);
  color: var(--text);
}

.result {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.75rem;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.result__head {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
  flex-wrap: wrap;
}

.result__smiles {
  font-family: var(--font-mono);
  font-size: 0.875rem;
  color: var(--text);
  font-weight: 600;
}

.result__level {
  font-size: 0.6875rem;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 0.0625rem 0.5rem;
}

.props {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0;
}

.props__row {
  display: flex;
  gap: 0.75rem;
  font-size: 0.8125rem;
}

.props__row dt {
  color: var(--text-muted);
  min-width: 4rem;
}

.props__row dd {
  margin: 0;
  font-family: var(--font-mono);
  color: var(--text);
}

.groups__title {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin: 0 0 0.375rem;
}

.groups__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.group {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.75rem;
  background: var(--surface-2);
  border-radius: 0.25rem;
  padding: 0.25rem 0.5rem;
}

.group__name {
  font-weight: 600;
  color: var(--text);
}

.group__smarts {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-muted);
}

.group__atoms {
  font-size: 0.6875rem;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.notice {
  border: 1px solid;
  border-radius: 0.375rem;
  padding: 0.625rem 0.75rem;
  font-size: 0.8125rem;
}

.notice--warn {
  border-color: color-mix(in srgb, var(--warning) 40%, transparent);
  background: color-mix(in srgb, var(--warning) 8%, transparent);
  color: var(--warning);
}

.notice__body {
  margin: 0.25rem 0 0;
  font-size: 0.75rem;
  line-height: 1.6;
  color: var(--text-secondary);
}

.notice__body code {
  font-size: 0.6875rem;
  color: var(--text-muted);
}

.result__empty,
.hint {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0;
}

.warnings {
  list-style: none;
  margin: 0;
  padding: 0.625rem;
  background: color-mix(in srgb, var(--warning) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--warning) 30%, transparent);
  border-radius: 0.375rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.warning {
  font-size: 0.75rem;
  color: var(--warning);
}
</style>
