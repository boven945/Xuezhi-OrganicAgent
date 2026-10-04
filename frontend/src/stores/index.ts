/**
 * 问答状态机。
 *
 * ## 为什么用集中 store 而不是组件内状态
 *
 * 三个视图（提问区、答复区、来源区）需要共享同一次问答的状态。
 * 放在组件里会出现「来源区不知道答复区是否已完成」这类同步问题。
 *
 * ## 状态机的形状
 *
 * `idle → streaming → (done | error | cancelled)`
 *
 * **必须显式建模**：`idle` 与 `streaming` 的区别不是布尔标志，
 * 而是「可否发起新请求」——`streaming` 期间必须禁用输入，
 * 否则会出现两个流同时写同一个 `text` 的问题。
 */

import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

import { ApiRequestError, ask, fetchHealth, parseSmiles } from '../api/client'
import { streamAsk } from '../api/stream'
import type {
  ComponentStatus,
  FunctionalGroup,
  HealthResponse,
  MoleculeResponse,
  SourceItem,
  ToolInvocation,
} from '../types/api'

/** 问答的阶段。 */
export type AskPhase = 'idle' | 'streaming' | 'done' | 'error' | 'cancelled'

/** 问题文本的长度上限，与后端 `AskRequest.question` 的 maxLength 一致。 */
export const QUESTION_MAX_LENGTH = 4096

export const useAskStore = defineStore('ask', () => {
  // --- 问答状态 ---
  const phase = ref<AskPhase>('idle')
  const question = ref('')
  /** 逐 token 累积的答复正文。 */
  const explanation = ref('')
  const sources = ref<SourceItem[]>([])
  const invocations = ref<ToolInvocation[]>([])
  const requestId = ref('')
  const elapsedSeconds = ref(0)
  const steps = ref(0)
  /** 当前阶段提示，如「正在检索教材」。 */
  const stageMessage = ref('')
  const error = ref<{ code: string; message: string; retryable: boolean } | null>(null)
  /** 历史上限：防止长会话把内存撑爆。 */
  const history = ref<{ question: string; explanation: string }[]>([])

  /** 用于取消进行中的流。存在即表示有请求在跑。 */
  const controller = shallowRef<AbortController | null>(null)

  const isStreaming = computed(() => phase.value === 'streaming')
  const canAsk = computed(
    () => !isStreaming.value && question.value.trim().length > 0 && question.value.length <= QUESTION_MAX_LENGTH,
  )
  /** 是否有可展示的来源。**空数组与「未检索」是两种状态，界面须区分。 */
  const hasSources = computed(() => sources.value.length > 0)
  const hasContent = computed(() => explanation.value.trim().length > 0)

  function reset(): void {
    explanation.value = ''
    sources.value = []
    invocations.value = []
    requestId.value = ''
    elapsedSeconds.value = 0
    steps.value = 0
    stageMessage.value = ''
    error.value = null
    phase.value = 'idle'
  }

  function pushHistory(): void {
    const q = question.value.trim()
    const a = explanation.value.trim()
    if (q && a) {
      history.value.push({ question: q, explanation: a })
      // 保留最近 20 条
      if (history.value.length > 20) history.value.shift()
    }
  }

  /** 发起流式问答。 */
  async function submit(): Promise<void> {
    if (!canAsk.value) return
    reset()
    phase.value = 'streaming'
    const ctl = new AbortController()
    controller.value = ctl

    try {
      const result = await streamAsk(
        question.value,
        {
          onMeta: (rid) => {
            requestId.value = rid
          },
          onStage: (_stage, message) => {
            if (message) stageMessage.value = message
          },
          onTool: (index, tool, ok) => {
            // 增量追加而非整体替换：工具在流中逐个到达
            invocations.value = [
              ...invocations.value,
              { tool, ok, error_code: null } as ToolInvocation,
            ]
            void index
          },
          onSources: (list) => {
            sources.value = list
          },
          onDelta: (text) => {
            // 关键：追加而非覆盖
            explanation.value += text
          },
          onDone: (r) => {
            requestId.value = r.requestId
            explanation.value = r.explanation || explanation.value
            sources.value = r.sources
            invocations.value = r.toolInvocations
            steps.value = r.steps
            elapsedSeconds.value = r.elapsedSeconds
            phase.value = 'done'
          },
          onError: (e) => {
            error.value = { code: e.code, message: e.message, retryable: e.retryable }
            phase.value = 'error'
          },
        },
        ctl.signal,
      )

      if (result.cancelled) {
        phase.value = 'cancelled'
        // 取消时已收到的部分文本是有价值的，不丢弃
        stageMessage.value = '已停止'
      } else if (phase.value === 'streaming') {
        // 流结束了但既没收到 done 也没收到 error——异常断开。
        // 不假装成功：那会让用户以为答完了。
        error.value = {
          code: 'stream_incomplete',
          message: '连接意外中断，答复可能不完整。',
          retryable: true,
        }
        phase.value = 'error'
      }
      pushHistory()
    } catch (err) {
      if (err instanceof ApiRequestError) {
        error.value = { code: err.code, message: err.message, retryable: err.retryable }
      } else {
        error.value = {
          code: 'ui_unexpected',
          message: '发生了未预期的错误，请重试。',
          retryable: true,
        }
      }
      phase.value = 'error'
    } finally {
      controller.value = null
    }
  }

  /** 用户主动停止。 */
  function stop(): void {
    controller.value?.abort()
  }

  /** 改用同步接口。用于对照验证 SSE 是否真的在工作。 */
  async function submitSync(): Promise<void> {
    if (!canAsk.value) return
    reset()
    phase.value = 'streaming'
    stageMessage.value = '正在分析问题（同步模式）'
    try {
      const r: Awaited<ReturnType<typeof ask>> = await ask(question.value)
      requestId.value = r.request_id
      explanation.value = r.explanation
      sources.value = r.sources
      invocations.value = r.tool_invocations
      steps.value = r.steps
      elapsedSeconds.value = r.elapsed_seconds
      if (r.error) {
        error.value = {
          code: r.error.code,
          message: r.error.message,
          retryable: r.error.retryable,
        }
        phase.value = 'error'
      } else {
        phase.value = 'done'
      }
      pushHistory()
    } catch (err) {
      error.value =
        err instanceof ApiRequestError
          ? { code: err.code, message: err.message, retryable: err.retryable }
          : { code: 'ui_unexpected', message: '发生了未预期的错误。', retryable: true }
      phase.value = 'error'
    }
  }

  return {
    phase,
    question,
    explanation,
    sources,
    invocations,
    requestId,
    elapsedSeconds,
    steps,
    stageMessage,
    error,
    history,
    isStreaming,
    canAsk,
    hasSources,
    hasContent,
    submit,
    submitSync,
    stop,
    reset,
  }
})

// ---------------------------------------------------------------------------
// 后端健康状态
// ---------------------------------------------------------------------------

export const useHealthStore = defineStore('health', () => {
  const status = ref<string>('unknown')
  const ready = ref(false)
  const components = ref<ComponentStatus[]>([])
  const lastCheckedAt = ref<number | null>(null)
  const reachable = ref<boolean | null>(null)

  /** 整体可用性判定。 */
  const isHealthy = computed(() => status.value === 'healthy')
  const isDegraded = computed(() => status.value === 'degraded')
  /**
   * 能否提问。`ready` 为真即表示 LLM 与知识库可用——
   * 化学组件不可用时服务仍可问答（后端已实现该降级）。
   */
  const canAsk = computed(() => ready.value)

  async function refresh(signal?: AbortSignal): Promise<void> {
    try {
      const r: HealthResponse = await fetchHealth(signal)
      status.value = r.status
      ready.value = r.ready
      components.value = r.components
      reachable.value = true
      lastCheckedAt.value = Date.now()
    } catch {
      // 探针失败本身就是结论：不可达。
      // 不抛错——健康检查是展示性功能，不该让整个页面白屏。
      status.value = 'unreachable'
      ready.value = false
      reachable.value = false
      components.value = []
      lastCheckedAt.value = Date.now()
    }
  }

  /** 按组件名查状态，供界面显示「化学引擎：不可用」。 */
  function component(name: string): ComponentStatus | undefined {
    return components.value.find((c) => c.name === name)
  }

  /**
   * Fay 数字人是否可用。
   *
   * 读结构化的 `caps.fay` 而**不是** `detail` 字符串——
   * detail 是给人看的文案，改字不应让功能静默失效。
   *
   * 组件缺失（如旧后端未提供 caps）时返回 false，
   * 即"宁可显示待机也不冒险连一个未知的地址"。
   */
  const fayEnabled = computed(() => component('speech')?.caps?.fay === true)

  return {
    status,
    ready,
    components,
    lastCheckedAt,
    reachable,
    isHealthy,
    isDegraded,
    canAsk,
    fayEnabled,
    refresh,
    component,
  }
})

// ---------------------------------------------------------------------------
// 分子式解析
// ---------------------------------------------------------------------------

export const useMoleculeStore = defineStore('molecule', () => {
  const smiles = ref('')
  const result = ref<MoleculeResponse | null>(null)
  const loading = ref(false)
  const error = ref<{ code: string; message: string } | null>(null)

  const groups = computed<FunctionalGroup[]>(() => result.value?.functional_groups ?? [])
  const hasResult = computed(() => result.value?.ok === true)

  /**
   * 分子式。
   *
   * 位于 `properties` 层而**不在** `structure.viz_data`——
   * 实测踩过：最初按后者取，取不到。
   */
  const formula = computed<string>(() => {
    const props = result.value?.properties as Record<string, unknown> | null | undefined
    const v = props?.molecular_formula
    return typeof v === 'string' ? v : ''
  })

  async function parse(): Promise<void> {
    const s = smiles.value.trim()
    if (!s || loading.value) return
    loading.value = true
    error.value = null
    try {
      result.value = await parseSmiles(s)
    } catch (err) {
      error.value =
        err instanceof ApiRequestError
          ? { code: err.code, message: err.message }
          : { code: 'ui_unexpected', message: '解析时发生未预期错误。' }
      result.value = null
    } finally {
      loading.value = false
    }
  }

  return { smiles, result, loading, error, groups, hasResult, formula, parse }
})
