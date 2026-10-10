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
   * Fay 是否**被配置为启用**（意图）。
   *
   * 读结构化的 `caps.fay` 而**不是** `detail` 字符串——
   * detail 是给人看的文案，改字不应让功能静默失效。
   *
   * ⚠️ **这不等于「Fay 真的可用」**。后端探针早期只读配置布尔值，
   * 于是「配置写着启用、实际没装/没跑」时这里仍是 true，
   * 而前端据此**主动关闭了数字人的兜底口型**——
   * 危害不只是显示错，是把降级路径关掉了。
   *
   * @deprecated判断「能否依赖 Fay」请用 {@link fayVerified}。
   * 本字段只用于「是否打算启用 Fay」这类语义。
   */
  const fayConfigured = computed(() => component('speech')?.caps?.fay === true)

  /**
   * Fay 是否**实测可用**。
   *
   * 读`caps.fay_verified`——后端会真做一次 TCP 连接探测。
   * **关降级路径必须用这个，不能用 `fayConfigured`**。
   *
   * 后端未提供该字段（旧版本）时返回 false，
   * 即"拿不到实测证据就不依赖它"——保守方向与原有约定一致。
   */
  const fayVerified = computed(() => component('speech')?.caps?.fay_verified === true)

  /**
   * 语音合成是否**实测可用**（后端真合成过一句短文本）。
   *
   * 同样：`caps.tts` 只是配置意图，`caps.tts_verified` 才是实测。
   */
  const ttsVerified = computed(() => component('speech')?.caps?.tts_verified === true)

  /**
   * 知识库检索是否**实测可用**（后端真检索过一次并命中）。
   *
   * `ready=true` 只说明 store 构造成功，**不保证检索有结果**——
   * 本项目出现过「构造成功但路径传错、检索必失败」而探针仍报就绪的情况。
   */
  const ragVerified = computed(() => component('rag')?.caps?.rag_verified === true)

  /**
   * 有任何实测未通过的组件——界面应提示而不是假装健康。
   *
   * 判据只认`*_verified`（实测层），不认 `caps.*`（配置层）。
   */
  const hasUnverifiedCapability = computed(
    () =>
      component('speech')?.caps?.tts === true && !ttsVerified.value ||
      (component('speech')?.caps?.fay === true && !fayVerified.value) ||
      (component('rag') !== undefined && !ragVerified.value),
  )

  return {
    status,
    ready,
    components,
    lastCheckedAt,
    reachable,
    isHealthy,
    isDegraded,
    canAsk,
    /** @deprecated 用 fayVerified 判断能否依赖；本字段仅表示配置意图。 */
    fayEnabled: fayConfigured,
    fayConfigured,
    fayVerified,
    ttsVerified,
    ragVerified,
    hasUnverifiedCapability,
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
