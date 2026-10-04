/**
 * SSE 流式问答客户端。
 *
 * ## 为什么用 fetch + ReadableStream 而非 `EventSource`
 *
 * `EventSource` 只支持 GET，不能带请求体。而问答必须 POST
 * （问题文本放body，符合 `security-privacy.md`「不得放URL」）。
 *
 * 实测确认后端 `POST /api/v1/ask/stream` 返回
 * `Content-Type: text/event-stream; charset=utf-8`，是标准 SSE 线格式，
 * 故用 fetch 读流 + 自行解析完全可行。
 *
 * ##流中错误必须走事件通道
 *
 * **实测关键行为：后端一旦发出响应头，就无法再改 HTTP 状态码。**
 * 所以即使 Agent 装配失败，流式请求返回的仍是 `200`，
 * 错误只能藏在事件流里。因此**只判`response.ok` 会漏掉所有业务错误**。
 */

import {
  API_BASE,
  ApiRequestError,
  parseSseChunk,
  readErrorBody,
  toSourceItem,
  type ParsedEvent,
} from './client'
import type { RawSource, SourceItem, ToolInvocation } from '../types/api'

/** 流式问答的回调集合。 */
export interface StreamHandlers {
  /** 建流成功，已拿到 request_id。此处**后端尚未做任何实质工作**。 */
  onMeta?: (requestId: string, note?: string) => void
  /** 阶段进展。 */
  onStage?: (stage: string, message?: string) => void
  /** 工具完成。 */
  onTool?: (index: number, tool: string, ok: boolean) => void
  /** 检索到来源，可增量展示。 */
  onSources?: (sources: SourceItem[]) => void
  /** 文本增量。**须追加**。 */
  onDelta?: (text: string) => void
  /** 最终结果。 */
  onDone?: (result: {
    requestId: string
    status: string
    explanation: string
    sources: SourceItem[]
    toolInvocations: ToolInvocation[]
    steps: number
    elapsedSeconds: number
  }) => void
  /** 流内错误。后端已脱敏，`message` 可直接展示。 */
  onError?: (error: { code: string; message: string; retryable: boolean; requestId: string }) => void
}

/** 流式问答的返回值。 */
export interface StreamResult {
  /** 用户是否主动取消。 */
  cancelled: boolean
  /** 是否正常收到 `done` 事件。 */
  completed: boolean
}

/**
 * 发起一次流式问答。
 *
 * @param question 已在前端做过长度校验的问题文本
 * @param handlers 事件回调
 * @param signal 用于取消。**取消只停客户端，不保证后端停止工作**
 *                （HTTP 语义限制），但能释放浏览器的连接。
 */
export async function streamAsk(
  question: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<StreamResult> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/api/v1/ask/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question }),
      signal,
    })
  } catch (err) {
    if (isAbort(err)) return { cancelled: true, completed: false }
    throw new ApiRequestError('无法连接后端服务，请确认它已启动。', {
      code: 'network_unreachable',
      status: 0,
      retryable: true,
      requestId: '',
    })
  }

  // 注意：这里**不能只判 response.ok 就认为成功**。
  // 若后端在建流前就失败（如限流 429、校验 422），状态码是正常的错误码，
  // 必须走错误分支；但若是建流后才失败，状态码已是 200，错误在事件流里。
  if (!response.ok) throw await readErrorBody(response)

  if (!response.body) {
    throw new ApiRequestError('浏览器不支持流式响应（response.body 为空）。', {
      code: 'stream_unsupported',
      status: 200,
      retryable: false,
      requestId: '',
    })
  }

  const reader = response.body.getReader()
  // TextDecoder 的 stream 模式：处理多字节字符被 chunk 边界切断的情况。
  // 不用它则中文在边界处会变成乱码——这是实测会遇到的。
  const decoder = new TextDecoder('utf-8')

  let buffer = ''
  let completed = false
  const seenSources = new Map<string, SourceItem>()

  const dispatch = (event: ParsedEvent): void => {
    const d = event.data
    switch (event.name) {
      case 'meta':
        handlers.onMeta?.(String(d.request_id ?? ''), d.note as string | undefined)
        break
      case 'stage':
        handlers.onStage?.(String(d.stage ?? ''), d.message as string | undefined)
        break
      case 'tool':
        handlers.onTool?.(Number(d.index ?? 0), String(d.tool ?? ''), Boolean(d.ok))
        break
      case 'source': {
        // 增量来源：按 source_id 去重，后到的覆盖先到的
        // （同一条来源可能被多个工具重复上报）。
        const raws = (d.sources as RawSource[] | undefined) ?? []
        for (const raw of raws) {
          const item = toSourceItem(raw)
          seenSources.set(`${item.source_id}|${item.locator}`, item)
        }
        handlers.onSources?.([...seenSources.values()])
        break
      }
      case 'delta':
        // 关键：文本已被 JSON.parse 还原为中文（后端转义为 \uXXXX）
        handlers.onDelta?.(String(d.text ?? ''))
        break
      case 'result': {
        //最终结果里的来源可能比流中的更完整，以它为准。
        const finalSources = ((d.sources as RawSource[] | undefined) ?? []).map(toSourceItem)
        if (finalSources.length > 0) {
          seenSources.clear()
          for (const s of finalSources) seenSources.set(`${s.source_id}|${s.locator}`, s)
        }
        handlers.onDone?.({
          requestId: String(d.request_id ?? ''),
          status: String(d.status ?? 'completed'),
          explanation: String(d.explanation ?? ''),
          sources: [...seenSources.values()],
          toolInvocations: (d.tool_invocations as ToolInvocation[] | undefined) ?? [],
          steps: Number(d.steps ?? 1),
          elapsedSeconds: Number(d.elapsed_seconds ?? 0),
        })
        break
      }
      case 'error':
        handlers.onError?.({
          code: String(d.code ?? 'api_internal_error'),
          message: String(d.message ?? '服务出错了。'),
          retryable: Boolean(d.retryable),
          requestId: String(d.request_id ?? ''),
        })
        break
      case 'done':
        completed = true
        break
      default:
        // 未知事件：忽略而非报错——后端将来加事件不应弄坏旧前端。
        break
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      const { events, rest } = parseSseChunk(decoder.decode(value, { stream: true }), buffer)
      buffer = rest
      for (const ev of events) dispatch(ev)
      if (completed) break
    }
  } catch (err) {
    if (isAbort(err)) return { cancelled: true, completed }
    throw new ApiRequestError('接收流式响应时中断。', {
      code: 'stream_interrupted',
      status: 0,
      retryable: true,
      requestId: '',
    })
  } finally {
    // 释放连接。cancel 后服务端会看到客户端断开。
    void reader.cancel().catch(() => undefined)
  }

  return { cancelled: false, completed }
}

/** 判断是否为用户主动取消。 */
function isAbort(err: unknown): boolean {
  return err instanceof DOMException && (err.name === 'AbortError' || err.name === 'TimeoutError')
}
