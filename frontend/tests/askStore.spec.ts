/**
 * 问答 store 的状态机测试。
 *
 * ## 为什么重点测这个
 *
 * `phase` 有五个态`idle → streaming → done / error / cancelled`，
 * 转换规则里有几条不直观的：
 *
 * - 用户取消时**已收到的文本要保留**（不是清空）
 * - 流结束但既无 `done` 也无 `error` →按**错误**处理，
 *   **不假装成功**（否则用户以为答完了）
 * - `result` 事件的完整文本**覆盖**逐 token 累积的结果
 *
 * 这些行为错了不会崩溃，只会静默地给出错误答案。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAskStore } from '../src/stores'

// 模拟后端流。每次调用返回配置好的事件回调序列。
const streamMock = vi.fn()

vi.mock('../src/api/stream', () => ({
  streamAsk: (q: string, h: unknown, s: unknown) => streamMock(q, h, s),
}))

/**
 * 构造一个假的 streamAsk，实现。
 * @param script 每个事件类型的处理器会被依次调用
 */
function scriptStream(
  script: {
    meta?: boolean
    stages?: string[]
    tools?: { tool: string; ok: boolean }[]
    sources?: unknown[]
    deltas?: string[]
    result?: Record<string, unknown>
    error?: Record<string, unknown>
    /** 返回 cancelled=true，模拟用户点了停止 */
    cancelled?: boolean
    /** 抛错，模拟网络失败 */
    throws?: Error
  },
) {
  streamMock.mockImplementation(async (_q: string, h: Record<string, Function>) => {
    if (script.throws) throw script.throws
    if (script.meta !== false) h.onMeta?.('rid-123', 'note')
    for (const s of script.stages ?? []) h.onStage?.(s, `正在${s}`)
    for (const t of script.tools ?? []) h.onTool?.(0, t.tool, t.ok)
    if (script.sources) h.onSources?.(script.sources)
    for (const d of script.deltas ?? []) h.onDelta?.(d)
    if (script.error) h.onError?.(script.error)
    if (script.result) h.onDone?.(script.result)
    return { cancelled: script.cancelled ?? false, completed: !script.cancelled }
  })
}

const RESULT = {
  requestId: 'rid-123',
  status: 'completed',
  explanation: '完整答复',
  sources: [],
  toolInvocations: [{ tool: 'search_knowledge', ok: true, error_code: null }],
  steps: 2,
  elapsedSeconds: 3.5,
}

beforeEach(() => {
  setActivePinia(createPinia())
  streamMock.mockReset()
})

describe('ask store 状态机', () => {
  it('初始为 idle 且不可提交', () => {
    const s = useAskStore()
    expect(s.phase).toBe('idle')
    expect(s.canAsk).toBe(false)
  })

  it('问题为空时不可提交', () => {
    const s = useAskStore()
    s.question = '   '
    expect(s.canAsk).toBe(false)
  })

  it('问题超长时不可提交', () => {
    const s = useAskStore()
    s.question = 'x'.repeat(4097)
    expect(s.canAsk).toBe(false)
  })

  it('正常完成后进入 done并保留内容', async () => {
    scriptStream({ deltas: ['苯酚', '有弱酸性'], result: RESULT })
    const s = useAskStore()
    s.question = '苯酚酸性'
    await s.submit()

    expect(s.phase).toBe('done')
    expect(s.explanation).toBe('完整答复')
    expect(s.requestId).toBe('rid-123')
    expect(s.steps).toBe(2)
    expect(s.invocations).toHaveLength(1)
  })

  it('result 的完整文本覆盖逐 token 累积', async () => {
    // 某些路径下增量之和与最终文本有细微差异，以最终为准更可靠
    scriptStream({ deltas: ['甲', '乙丙'], result: RESULT })
    const s = useAskStore()
    s.question = 'q'
    await s.submit()
    expect(s.explanation).toBe('完整答复')
  })

  it('流中错误进入 error 并保留错误码', async () => {
    scriptStream({
      deltas: ['部分内容'],
      error: { code: 'llm_upstream_error', message: '服务暂时不可用。', retryable: true },
    })
    const s = useAskStore()
    s.question = 'q'
    await s.submit()

    expect(s.phase).toBe('error')
    expect(s.error?.code).toBe('llm_upstream_error')
    // 错误时已收到的文本应保留——用户至少看到部分内容
    expect(s.explanation).toBe('部分内容')
  })

  it('流意外结束（既无 done 也无 error）按错误处理，不假装成功', async () => {
    // 这是最容易漏的分支：连接断了但界面显示"已完成"
    scriptStream({ deltas: ['答到一半'] })
    const s = useAskStore()
    s.question = 'q'
    await s.submit()

    expect(s.phase).toBe('error')
    expect(s.error?.code).toBe('stream_incomplete')
    expect(s.error?.message).toContain('不完整')
  })

  it('用户取消进入 cancelled 且保留已收到文本', async () => {
    scriptStream({ deltas: ['答了一半'], cancelled: true })
    const s = useAskStore()
    s.question = 'q'
    await s.submit()

    expect(s.phase).toBe('cancelled')
    // 关键：不清空
    expect(s.explanation).toBe('答了一半')
  })

  it('网络异常转为可展示的错误而非抛异常', async () => {
    scriptStream({ throws: new Error('boom') })
    const s = useAskStore()
    s.question = 'q'
    await s.submit()

    expect(s.phase).toBe('error')
    expect(s.error?.message).toBeTruthy()
  })

  it('流式中不可再次提交', async () => {
    let resolveStream: (v: unknown) => void = () => {}
    streamMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStream = resolve
        }),
    )
    const s = useAskStore()
    s.question = 'q'
    const p = s.submit()
    await Promise.resolve()
    expect(s.isStreaming).toBe(true)
    expect(s.canAsk).toBe(false)

    resolveStream({ cancelled: false, completed: true })
    await p
    expect(s.isStreaming).toBe(false)
  })

  it('完成后写入历史', async () => {
    scriptStream({ result: RESULT })
    const s = useAskStore()
    s.question = '苯酚酸性'
    await s.submit()
    expect(s.history).toHaveLength(1)
    expect(s.history[0].question).toBe('苯酚酸性')
  })

  it('历史上限 20 条——经由 submit 累积时生效', async () => {
    // 必须走 submit 才能验证裁剪：裁剪逻辑在 pushHistory 内，
    // 从外部直接 push history 数组会绕过它（那不是真实使用路径）。
    const s = useAskStore()
    for (let i = 0; i < 25; i += 1) {
      scriptStream({ result: { ...RESULT, explanation: `答复 ${i}` } })
      s.question = `问题 ${i}`
      await s.submit()
    }
    expect(s.history).toHaveLength(20)
    // 保留的是最近 20 条，故最早的那条应已被丢弃
    expect(s.history[0].question).toBe('问题 5')
    expect(s.history[19].question).toBe('问题 24')
  })

  it('重新提问会清空上一次的来源与统计', async () => {
    const s = useAskStore()
    scriptStream({
      sources: [{ source_id: 'a', title: '讲义', locator: '第一章', locator_kind: 'chapter', version: '', review_status: 'unknown' }],
      result: RESULT,
    })
    s.question = 'q1'
    await s.submit()
    expect(s.steps).toBe(2)

    scriptStream({ result: { ...RESULT, steps: 1 } })
    s.question = 'q2'
    await s.submit()
    // 不残留上一次的来源
    expect(s.sources).toHaveLength(0)
    expect(s.steps).toBe(1)
  })
})
