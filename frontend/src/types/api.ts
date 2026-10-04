/**
 * 后端接口契约的 TypeScript 镜像。
 *
 * ## 这里的每个字段都必须与后端一致
 *
 * 权威来源是 `backend/app/api/models.py`，本文件由
 * `docs/interface-contract-verification.md` 记录的实测OpenAPI 导出核对而来。
 * **不得凭印象增删字段**——后端加字段而此处不加，TypeScript 不会报错，
 * 只会静默丢数据；此处加而后端没有，则运行时得到 `undefined`。
 *
 * 核对方式（后端可跑时）：
 *
 * ```bash
 * PYTHONPATH=backend python -c "
 *   import json
 *   from app.api.app import create_app
 *   print(json.dumps(create_app().openapi()['components']['schemas'], ensure_ascii=False))
 * "
 * ```
 */

/** 请求状态。枚举值取自 `app.api.models.RequestStatus`。 */
export type RequestStatus = 'processing' | 'completed' | 'partial' | 'failed'

/**
 * 来源定位类型。
 *
 * 用枚举而非自由文本是后端的设计意图（见 `SourceLocator` 的 docstring）：
 * 前端可据此决定渲染方式——页码与章节号的排版不同。
 */
export type SourceLocatorKind = 'section' | 'page' | 'chapter' | 'unknown'

/** 单条知识来源。 */
export interface SourceItem {
  source_id: string
  title: string
  /** 章节号、页码等。定位方式随来源而异，故为自由文本。 */
  locator: string
  locator_kind: SourceLocatorKind
  version: string
  /**
   * 审核状态。**恒为 `unknown` 而非 `approved`**——
   * 后端刻意不写审核结论，因为那是人的判断而非系统能断言的事实。
   * 前端据此提示「内容待审核」，不得渲染成「已认证」。
   */
  review_status: string
}

/** 一次工具调用的记录。 */
export interface ToolInvocation {
  /** 工具名，取值受后端工具白名单限制。 */
  tool: string
  ok: boolean
  /** 失败时的错误码；`null` 表示无错误。 */
  error_code: string | null
}

/** 统一错误响应中的错误体。 */
export interface ApiError {
  code: string
  /** 面向学生的文案。**已由后端脱敏**，不含内部路径与上游原文。 */
  message: string
  retryable: boolean
  request_id: string
}

/** 组件健康状态。 */
export interface ComponentStatus {
  /** `chem` / `llm` / `rag` / `speech`。 */
  name: string
  ready: boolean
  /** 简短说明。**不含异常堆栈**（后端刻意只给异常类名）。 */
  detail: string
  /**
   * 子能力开关。**判断能力可用性请用这个，不要解析 `detail`**。
   *
   * 实测踩过：`speech` 的 detail 形如 `"tts=就绪 fay=未启用"`，
   * 判断"数字人是否可用"就得 `detail.includes('fay=就绪')`
   * —— 那把展示文案变成了契约，文案一改前端就静默失效。
   *
   * 已知键：`tts`（语音合成）、`fay`（数字人）。
   */
  caps?: Record<string, boolean>
}

/** `GET /health` 响应。 */
export interface HealthResponse {
  /** `healthy` / `degraded` / `unhealthy`。 */
  status: string
  ready: boolean
  components: ComponentStatus[]
  schema_version: string
}

/** 同步问答响应。 */
export interface AnswerResponse {
  request_id: string
  status: RequestStatus
  /** 学生可读的答复正文。这是主交付（`architecture.md` §6）。 */
  explanation: string
  sources: SourceItem[]
  /**
   * 可视化提示。当前恒为空数组——`frontend-viz` 模块未实现。
   * 契约保留以免将来破坏性变更。
   */
  visualization: VisualizationHint[]
  tool_invocations: ToolInvocation[]
  steps: number
  elapsed_seconds: number
  error: ApiError | null
  schema_version: string
}

/** 可视化提示。当前后端不产出。 */
export interface VisualizationHint {
  kind: string
  data: Record<string, unknown>
  source: string | null
}

/**
 * 语音合成的状态。与后端 `SpeechResult.stage` 一一对应。
 *
 * **四态而非布尔**：前端要区分「用户主动关闭」（正常选择，
 * 不该显示错误样式）与「服务故障」（该提示）。合并成布尔会丢掉这个区分。
 *
 * - `ready`：有音频，可播放
 * - `disabled`：已关闭——**正常状态，不提示错误**
 * - `not_configured`：缺配置，可提示但不报错
 * - `unavailable`：服务故障
 */
export type SpeechStage = 'ready' | 'disabled' | 'not_configured' | 'unavailable'

/**
 * 语音合成响应（`POST /api/v1/speak`）。
 *
 * 契约见 `docs/speech-module-verification.md`。要点：
 * - **两阶段**：本接口只返回 `audio_id` 与 `audio_url`，
 *   音频须另请求 `audio_url` 取。后端刻意不base64 内联
 *   （体积 +33%、浏览器无法单独缓存）。
 * - `audio_url` 由服务端给出，**前端不拼路径**。
 * - 失败时**仍返回 200**，状态在 `stage` 里——语音是纯增强能力，
 *   不可用时文本答复照常交付。
 */
export interface SpeechResponse {
  request_id: string
  stage: SpeechStage
  available: boolean
  audio_id: string | null
  audio_url: string | null
  /** 面向学生的简短说明，可直接展示。 */
  reason: string
  /** 文本是否被截断——学生应知道「还有内容」。 */
  truncated: boolean
  char_count: number
  digital_human_delivered: boolean
}

/** 分子式解析响应。 */
export interface MoleculeResponse {
  request_id: string
  ok: boolean
  /**
   * 分子性质。形状随 `verification` 层级而变——
   * 语法级解析拿不到性质，故**不可假设某字段必然存在**。
   */
  properties: Record<string, unknown> | null
  structure: Record<string, unknown> | null
  functional_groups: FunctionalGroup[]
  /**
   * 解析层级，取值形如 `syntax` / `validity` / `properties` / `groups`。
   * 前端据此提示「能信到什么程度」——这比直接显示结果更诚实。
   */
  verification: string
  notes: string[]
  warnings: string[]
  error: ApiError | null
  schema_version: string
}

/**
 * 官能团命中项。
 *
 * 注意后端只返回 `matched=true` 的项（已修缺陷，见验证文档 §5.2），
 * 故 `matched` 恒为 `true`；保留字段是为了将来可能返回未命中项。
 */
export interface FunctionalGroup {
  name: string
  smarts: string
  atom_indices: number[]
  matched: boolean
}

// ---------------------------------------------------------------------------
// SSE 事件契约
// ---------------------------------------------------------------------------

/**
 * SSE 事件名。
 *
 * 取值实测自后端 `AgentLoop.EVENT_*` 常量，经API 层转发后
 * 与此处逐一对应。**新增事件必须同步此处**，否则前端会静默忽略。
 */
export type StreamEventName = 'meta' | 'stage' | 'tool' | 'source' | 'delta' | 'result' | 'error' | 'done'

/**
 * 建流首事件。
 *
 * **它不触碰任何模型或向量库**，故后端完全不可用时也能发出——
 * 这保证「连接成功但无响应」这种最坏情况不会发生，前端能立刻
 * 拿到 `request_id` 并渲染加载态。
 */
export interface MetaEvent {
  request_id: string
  schema_version: string
  note?: string
}

/** 阶段进展。 */
export interface StageEvent {
  /** `thinking` / `tool` / 后续可能的阶段名。 */
  stage: string
  message?: string
  step?: number
}

/** 工具完成事件。 */
export interface ToolEvent {
  index: number
  tool: string
  ok: boolean
  error_code: string | null
}

/**
 * 知识来源事件。
 *
 * 在检索工具成功后发出，故前端可**增量**显示来源，
 * 不必等最终 `result`。
 */
export interface SourceEvent {
  sources: RawSource[]
}

/**
 * 后端 Agent 层产出的原始来源。
 *
 * 与 `SourceItem` 的差异：Agent 层用 `edition`（版次）而
 * API 契约用 `version`；Agent 层还有 `scope`（课标层级）而
 * API 契约无此字段。前端**不应直接消费本类型**——
 * 请用 `api/client.ts` 的转换函数。
 */
export interface RawSource {
  source_id: string
  title: string
  edition?: string
  version?: string
  locator?: string
  scope?: string
}

/**
 * 文本增量。
 *
 * **须追加而非覆盖**——每个 delta 只是一小片。
 */
export interface DeltaEvent {
  text: string
}

/** 最终结果。字段与 `AnswerResponse` 的对应部分一致。 */
export type ResultEvent = Pick<
  AnswerResponse,
  'request_id' | 'status' | 'explanation' | 'sources' | 'tool_invocations' | 'steps' | 'elapsed_seconds'
> & { schema_version: string }

/** 流内错误。 */
export interface ErrorEvent {
  request_id: string
  code: string
  message: string
  retryable: boolean
}

/** 流结束标记。 */
export interface DoneEvent {
  request_id: string
}

/** 事件名到载荷的映射。用于让 `parseSseChunk` 获得类型安全。 */
export interface StreamEventPayloads {
  meta: MetaEvent
  stage: StageEvent
  tool: ToolEvent
  source: SourceEvent
  delta: DeltaEvent
  result: ResultEvent
  error: ErrorEvent
  done: DoneEvent
}
