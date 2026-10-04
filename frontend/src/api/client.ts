/**
 * 后端 HTTP 客户端。
 *
 * 职责边界：**只做协议转换**，不含任何界面状态。
 * 状态由 `stores/` 管理，渲染由组件负责。
 */

import type {
  AnswerResponse,
  HealthResponse,
  MoleculeResponse,
  RawSource,
  SourceItem,
  SourceLocatorKind,
  StreamEventName,
} from '../types/api'

/**
 * 后端基址。
 *
 * 优先读 `VITE_API_BASE_URL`（Vite 只把`VITE_` 前缀的变量注入客户端，
 * 这是安全边界——不这样限制的话任何环境变量都会暴露到浏览器）。
 *
 * 默认为**同源相对路径**而非 `localhost:8000`：开发时 Vite 把
 * `/api` 代理到后端，生产时前后端同源部署。用相对路径可以让
 * 同一份构建产物在两种场景下都工作，也避免了「前端跑在 8000、
 * 后端在 8000、用户改了其中一个端口」这类问题。
 */
export const API_BASE: string = (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? ''

/** 单次请求超时（毫秒）。 */
const REQUEST_TIMEOUT_MS = 120_000

/**
 * 把「用户取消」与「超时」组合成一个信号。
 *
 * 用标准的 `AbortSignal.any`（MDN 标注 Baseline Widely Available，
 * 2024-03 起所有主流浏览器均支持）而非手写组合：
 * 手写需要一个真实的 `AbortSignal` 实例，而 `AbortController.signal`
 * 是只读的，**代理一个信号对象会丢掉 `throwIfAborted` 等方法**——
 * 实测踩过这个坑，故直接用标准 API。
 *
 * 两者的区分靠 `err.name`：`AbortError` 是用户主动取消（静默处理），
 * `TimeoutError` 是超时（值得提示）。故这里用 `AbortSignal.timeout`
 * 而非手动 `setTimeout` + `abort()`——**后者 reason 是自定义的，
 * `name` 不会变成 `TimeoutError`**，会把超时误报成用户取消。
 */
function withTimeout(signal?: AbortSignal, ms: number = REQUEST_TIMEOUT_MS): AbortSignal {
  const timeout = AbortSignal.timeout(ms)
  return signal ? AbortSignal.any([signal, timeout]) : timeout
}

/** 统一的客户端错误。保留后端错误码，供界面据此决定动作。 */
export class ApiRequestError extends Error {
  readonly code: string
  readonly status: number
  readonly retryable: boolean
  readonly requestId: string

  constructor(
    message: string,
    opts: { code: string; status: number; retryable: boolean; requestId: string },
  ) {
    super(message)
    this.name = 'ApiRequestError'
    this.code = opts.code
    this.status = opts.status
    this.retryable = opts.retryable
    this.requestId = opts.requestId
  }
}

/**
 * 从错误响应中提取错误体。
 *
 * 后端有两种错误形态（实测）：
 * 1. `{error: {...}}` —— 业务错误（`api_*` 码），处理器统一构造；
 * 2. `{detail: [...]}` —— FastAPI 原生校验错误。
 *
 * 后者**已被后端重写为中文且丢弃 `input` 字段**（实测确认
 * `text/plain` 请求原先会回显用户输入，属安全漏洞）。
 * 但仍须容错——代理层或网关可能返回非 JSON。
 */
export async function readErrorBody(response: Response): Promise<ApiRequestError> {
  let code = `http_${response.status}`
  let message = `请求失败（HTTP ${response.status}）`
  let retryable = response.status >= 500 || response.status === 429
  let requestId = ''

  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; retryable?: boolean; request_id?: string }
      detail?: unknown
    }
    if (body.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
      retryable = body.error.retryable ?? retryable
      requestId = body.error.request_id ?? ''
    } else if (body.detail) {
      // FastAPI 校验错误：detail 可能是数组或字符串
      const first = Array.isArray(body.detail) ? body.detail[0] : body.detail
      const msg =
        typeof first === 'object' && first !== null && 'msg' in first
          ? String((first as { msg: unknown }).msg)
          : String(first)
      code = 'api_invalid_input'
      message = msg
      retryable = false
    }
  } catch {
    // 响应体不是 JSON（如反向代理的 HTML 错误页）——保留默认文案。
    // **不把响应体原文透出**：可能含内部路径或栈信息。
  }

  return new ApiRequestError(message, { code, status: response.status, retryable, requestId })
}

async function request<T>(path: string, init: RequestInit, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: withTimeout(signal),
      headers: {
        // **必须显式带 Content-Type**：后端 FastAPI 0.132+ 严格校验，
        // 缺 JSON 头返回 422（实测，非 415）。
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    // 区分「连不上」与「服务端报错」——前者提示检查后端是否启动。
    throw new ApiRequestError('无法连接后端服务，请确认它已启动。', {
      code: 'network_unreachable',
      status: 0,
      retryable: true,
      requestId: '',
    })
  }

  if (!response.ok) throw await readErrorBody(response)
  return (await response.json()) as T
}

/** 健康检查。永不抛错——它本身就是探针。 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { method: 'GET' }, signal)
}

/** SMILES 解析。 */
export async function parseSmiles(
  smiles: string,
  signal?: AbortSignal,
): Promise<MoleculeResponse> {
  return request<MoleculeResponse>(
    '/api/v1/molecule',
    { method: 'POST', body: JSON.stringify({ smiles }) },
    signal,
  )
}

/** 同步问答。 */
export async function ask(question: string, signal?: AbortSignal): Promise<AnswerResponse> {
  return request<AnswerResponse>(
    '/api/v1/ask',
    { method: 'POST', body: JSON.stringify({ question }) },
    signal,
  )
}

/**
 * 从定位文本推断类型。
 *
 * **只认同可识别的模式，其余一律 `unknown`。**
 * 曾经的写法是「非空即 `section`」，实测那样写会让
 * `unknown` 成为永远走不到的死分支——而 `unknown` 恰恰是
 * 唯一诚实的答案：「见附录」既不是章节也不是页码。
 *
 * 错标页码比不标更糟：学生会去找一个不存在的页，
 * 找不到就以为整份讲义不可靠。**宁可承认不知道。**
 */
function inferLocatorKind(locator: string): SourceLocatorKind {
  const t = locator.trim()
  if (!t) return 'unknown'
  // 页码：第 12 页 / p.12 / page 12
  if (/第?\s*[\d一二三四五六七八九十百]+\s*页/.test(t) || /^\s*(p\.|page\s*)/i.test(t)) {
    return 'page'
  }
  // 章节：第三章 / 第 3 章 / 第二章
  if (/第\s*[\d一二三四五六七八九十百]+\s*[章篇]/.test(t)) return 'chapter'
  // 小节：3.2 节 / 第二节
  if (/(^|\s)[\d.]+\s*节/.test(t) || /第\s*[一二三四五六七八九十\d]+\s*节/.test(t)) {
    return 'section'
  }
  return 'unknown'
}

/**
 * 把 Agent 层原始来源转换为 API 契约的 `SourceItem`。
 *
 * **两层的字段名不同，这是实测发现的**：
 * Agent 层用 `edition`（版次），API 契约用 `version`；
 * Agent 层有 `scope`（课标层级），API 契约没有该字段。
 *
 * `locator_kind` 需从自由文本推断——后端 Agent 层不提供该字段，
 * 契约里是 `unknown` 兜底。此处按可识别的模式推断，
 * **推断不出就留 `unknown`，不猜**。
 */
export function toSourceItem(raw: RawSource): SourceItem {
  const locator = raw.locator ?? ''
  return {
    source_id: raw.source_id,
    title: raw.title,
    locator,
    locator_kind: inferLocatorKind(locator),
    // edition 优先，回落 version——兼容后端两种命名。
    version: raw.edition ?? raw.version ?? '',
    review_status: 'unknown',
  }
}

/** 一条已解析的 SSE 事件。 */
export interface ParsedEvent {
  /**
   * 事件名。
   *
   * 类型是 `string` 而非 `StreamEventName`——**运行时可能收到
   * 后端新增的事件名**（前端的 switch 里有 `default` 分支忽略它们，
   * 这正是「后端加事件不弄坏旧前端」的实现方式）。
   * 若在此收紧类型，TS 会让人误以为所有分支都已覆盖。
   */
  name: string
  data: Record<string, unknown>
}

/**
 * 解析 SSE 字节流，逐条产出事件。
 *
 * ## 三条实测得到的关键事实（凭直觉写一定错）
 *
 * **1. 中文被转义为 `\uXXXX`。**
 * 后端 FastAPI 的 `ServerSentEvent` 用 `json.dumps` 默认
 * `ensure_ascii=True` 编码 `data` 字段，故中文到达时是
 * `"\u82ef\u915a"`。**必须 `JSON.parse` 才能还原**——
 * 直接显示会看到一串转义码。
 *
 * **2. 事件块以空行分隔，不是以连接关闭为界。**
 * 一个 chunk 可能只含半个块，故必须自己缓冲。
 *
 * **3. `data:` 与 `event:` 的顺序不保证。**
 * 多数实现先写 `event:`，但规范并未强制。**故解析器
 * 必须容忍任意顺序**，否则换服务端就崩。
 *
 * @param chunk 本次收到的文本块（可能截断在多字节字符中间）
 * @param buffer 上次残留的半块文本（会被本函数消费并追加）
 * @returns 本次可完整解析出的事件
 */
export function parseSseChunk(
  chunk: string,
  buffer: string,
): { events: ParsedEvent[]; rest: string } {
  const events: ParsedEvent[] = []
  const combined = buffer + chunk

  // 以空行切分事件块。SSE 规范允许 \r\n，故同时处理。
  const blocks = combined.split(/\r?\n\r?\n/)
  // 最后一个元素是未闭合的残块，留到下次
  const rest = blocks.pop() ?? ''

  for (const block of blocks) {
    // 缺 event: 时 SSE 规范规定事件名为 "message"。本后端从不省略该字段，
    // 故用 `unknown` 表示「未识别」——**不塞进 StreamEventName**，
    // 否则调用方的 switch 会被类型系统误导，以为自己已处理了这种情况。
    let name = 'unknown'
    const dataLines: string[] = []

    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(':')) continue // 注释行（心跳）
      if (line.startsWith('event:')) name = line.slice(6).trim() as StreamEventName
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }

    if (dataLines.length === 0) continue
    const raw = dataLines.join('\n')
    if (raw === '[DONE]') continue // OpenAI 兼容协议的结束标记，本后端未用但兼容

    try {
      // 关键：JSON.parse 同时完成「反转义」与「解析」
      events.push({ name, data: JSON.parse(raw) as Record<string, unknown> })
    } catch {
      // 单条事件解析失败不应中断整个流——继续处理下一条。
      // 记在控制台供排查，但不抛给用户（那会表现为「答到一半停了」）。
      console.warn('[sse] 跳过无法解析的事件', name, raw.slice(0, 120))
    }
  }

  return { events, rest }
}
