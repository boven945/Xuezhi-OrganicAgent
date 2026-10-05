<script setup lang="ts">
/**
 * 3D 分子视图组件。
 *
 * ## 生命周期是这个组件最容易出错的地方
 *
 * Three.js 在 `onMounted` 创建、`onBeforeUnmount` 释放。
 * 若忘记释放，切了几次分子后显存会持续泄漏——
 * 表现为「画面越来越卡」，而根因在代码里看不出来。
 *
 * ## 渲染失败不抛异常
 *
 * WebGL 不可用（老旧设备、虚拟机、无驱动）在教学场景下
 * 是真实可能。此时**页面其余部分必须照常工作**，
 * 故只在此组件内降级为文字提示。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { BALL_SCALE, elementName } from './elements'
import type { RenderOptions } from './viewer'
import { MoleculeViewer } from './viewer'
import type { RenderAtomV2, RenderBondV2 } from './types'
import { validateVizData } from './types'

const props = defineProps<{
  viz: unknown
}>()

const container = ref<HTMLDivElement | null>(null)
/** 用 shallowRef：Three.js 实例不该被 Vue 深度代理。 */
const viewer = shallowRef<MoleculeViewer | null>(null)
const error = ref<string | null>(null)
const mode = ref<RenderOptions['mode']>('ball-stick')
const showHydrogens = ref(true)
const autoRotate = ref(false)

/** 被选中的原子（点选后显示信息）。 */
const selected = ref<{ element: string; index: number } | null>(null)

/** 场景内原子总数（供统计显示）。 */
const atomCount = ref(0)

/**
 * 校验并取数据。
 *
 * **只调用一次 `validateVizData`**：TypeScript 的收窄
 * 不能跨函数调用保持，早期版本调用两次导致收窄失败。
 * 返回判别联合（`{error}` | `{atoms,...}`）让调用处
 * 用 `'error' in r` 一次判定。
 */
function currentData():
  | { atoms: RenderAtomV2[]; bonds: RenderBondV2[]; coords: [number, number, number][] }
  | { error: string }
  | null {
  if (!viewer.value) return null
  return validateVizData(props.viz)
}

function render(): void {
  const v = viewer.value
  if (!v) return
  const data = currentData()
  if (!data || 'error' in data) {
    error.value = data && 'error' in data ? data.error : '数据不可用'
    return
  }
  error.value = null
  atomCount.value = data.atoms.length
  v.setMolecule(data.atoms, data.bonds, data.coords)
  v.setOptions({ mode: mode.value, showHydrogens: showHydrogens.value, autoRotate: autoRotate.value })
}

onMounted(() => {
  if (!container.value) return
  const v = new MoleculeViewer(container.value)
  if (v.unavailableReason) {
    // WebGL 不可用：只提示，页面其余部分照常
    error.value = `三维渲染不可用：${v.unavailableReason}。结构式与性质仍可正常查看。`
    return
  }
  viewer.value = v
  render()
})

onBeforeUnmount(() => {
  // **必须释放**——显存不由 GC 管
  viewer.value?.dispose()
  viewer.value = null
})

watch(() => props.viz, render)
watch([mode, showHydrogens, autoRotate], () => {
  viewer.value?.setOptions({
    mode: mode.value,
    showHydrogens: showHydrogens.value,
    autoRotate: autoRotate.value,
  })
})

/** 球半径缩放的说明文本（教学：学生应知道球不是原子真实大小）。 */
const scaleNote = `球的半径已按 ${Math.round(BALL_SCALE * 100)}% 缩放，便于看清键`
</script>

<template>
  <div class="viz">
    <div class="viz__stage">
      <!-- 容器不能有 padding，否则 renderer 尺寸与容器不匹配 -->
      <div ref="container" class="viz__canvas" />

      <p v-if="error" class="viz__error" role="status">{{ error }}</p>

      <!-- 操作提示：拖拽/缩放是**新加的**能力（2026-10-05 接OrbitControls），
           没有提示学生不知道白模可以转动。放在左下角、不遮挡分子。 -->
      <p v-if="!error" class="viz__hint">拖动旋转 · 滚轮缩放</p>

      <div v-if="!error && selected" class="viz__picked">
        选中：{{ elementName(selected.element) }}
        <button type="button" class="viz__picked-close" @click="selected = null">×</button>
      </div>
    </div>

    <div class="viz__controls">
      <div class="viz__group" role="radiogroup" aria-label="显示方式">
        <button
          v-for="m in (['ball-stick', 'space-filling', 'line'] as const)"
          :key="m"
          type="button"
          class="viz__btn"
          :class="{ 'viz__btn--on': mode === m }"
          :aria-pressed="mode === m"
          @click="mode = m"
        >
          {{ m === 'ball-stick' ? '球棍' : m === 'space-filling' ? '填充' : '线框' }}
        </button>
      </div>

      <label class="viz__check">
        <input v-model="showHydrogens" type="checkbox" />
        <span>显示氢原子</span>
      </label>

      <label class="viz__check">
        <input v-model="autoRotate" type="checkbox" />
        <span>自动旋转</span>
      </label>

      <button type="button" class="viz__btn" @click="viewer?.resetView()">重置视角</button>
    </div>

    <p class="viz__note">
      {{ atomCount }} 个原子（含显式氢）·{{ scaleNote }}。
      坐标为一种可能构象，同一分子的实际构象可以不同。
    </p>
  </div>
</template>

<style scoped>
.viz {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.viz__stage {
  position: relative;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
}

.viz__canvas {
  /* 高度必须显式给——Three.js 读 clientHeight 来定视口 */
  height: 22rem;
  width: 100%;
  cursor: grab;
  /* 底色由 `viewer.ts` 的 `scene.background` 设（跟随系统主题）。
   * **这里再设 background 无效**——实测踩过：canvas 的绘制结果会
   * 覆盖元素背景，透明背景下该区域渲染为纯黑（浅色主题下刺眼）。
   * 保留一行注释说明原因，避免后来人重复踩。 */
}

.viz__canvas:active {
  cursor: grabbing;
}

.viz__error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 1.5rem;
  text-align: center;
  font-size: 0.8125rem;
  color: var(--warning);
  background: var(--surface-2);
}

/* 操作提示：贴左下角、不遮挡分子中央。
 * 用 `pointer-events: none`——它只是文字说明，
 * **不能挡住拖拽**（挡住的话用户会发现"提示处拖不动"）。 */
.viz__hint {
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

.viz__picked {
  position: absolute;
  top: 0.5rem;
  left: 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  padding: 0.25rem 0.5rem;
}

.viz__picked-close {
  font: inherit;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0 0.125rem;
}

.viz__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.75rem;
}

.viz__group {
  display: flex;
  gap: 0.125rem;
  border: 1px solid var(--border);
  border-radius: 0.25rem;
  overflow: hidden;
}

.viz__btn {
  font: inherit;
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  background: var(--surface-2);
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
}

.viz__btn--on {
  background: var(--accent);
  color: #fff;
}

.viz__check {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--text-secondary);
  cursor: pointer;
}

.viz__note {
  margin: 0;
  font-size: 0.6875rem;
  color: var(--text-muted);
  line-height: 1.6;
}
</style>
