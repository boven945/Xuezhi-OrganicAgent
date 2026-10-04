/**
 * SSE 解析器的测试。
 *
 * ## 为什么这些用例都用**实测得到的真实字节**
 *
 * 解析器最常见的失败方式是「按规范写、按想象测」——
 * 规范说 `data:` 后跟一个空格，但实际服务端可能不跟；
 * 规范说块以空行分隔，但实际可能用 `\r\n`。
 *
 * 这里的每个 fixture 都取自 `docs/interface-contract-verification.md`
 * 记录的实测线格式（用 repr 打印过的原始字节），
 * 其中最关键的一条是**中文被转义为 `\uXXXX`**。
 */

import { describe, expect, it } from 'vitest'

import { parseSseChunk, toSourceItem } from '../src/api/client'

describe('parseSseChunk', () => {
  it('解析单条完整事件', () => {
    // 实测字节：event: done\ndata: {"request_id": "abc"}\n\n
    const { events, rest } = parseSseChunk('event: done\ndata: {"request_id":"abc"}\n\n', '')
    expect(events).toHaveLength(1)
    expect(events[0].name).toBe('done')
    expect(events[0].data.request_id).toBe('abc')
    expect(rest).toBe('')
  })

  it('还原被转义的中文——实测后端用 ensure_ascii=True', () => {
    // 实测字节：中文到达时是 \u82ef\u915a
    const chunk =
      'event: delta\ndata: {"text":"\\u82ef\\u915a"}\n\n'
    const { events } = parseSseChunk(chunk, '')
    expect(events[0].data.text).toBe('苯酚')
    // 关键断言：不是转义串
    expect(events[0].data.text).not.toContain('\\u')
  })

  it('把跨 chunk 切断的事件正确拼接', () => {
    // 第一次只收到一半，第二次收到剩余部分
    const first = parseSseChunk('event: delta\ndata: {"text":"苯', '')
    expect(first.events).toHaveLength(0)
    expect(first.rest).toBe('event: delta\ndata: {"text":"苯')

    const second = parseSseChunk('酚"}\n\n', first.rest)
    expect(second.events).toHaveLength(1)
    expect(second.events[0].data.text).toBe('苯酚')
  })

  it('一个 chunk 内含多条完整事件时全部产出', () => {
    const chunk =
      'event: meta\ndata: {"request_id":"r1"}\n\n' +
      'event: stage\ndata: {"stage":"thinking"}\n\n' +
      'event: done\ndata: {"request_id":"r1"}\n\n'
    const { events, rest } = parseSseChunk(chunk, '')
    expect(events.map((e) => e.name)).toEqual(['meta', 'stage', 'done'])
    expect(rest).toBe('')
  })

  it('容忍 event 与 data 的顺序颠倒', () => {
    // SSE 规范未强制顺序；只认 event 在前会脆
    const chunk = 'data: {"stage":"thinking"}\nevent: stage\n\n'
    const { events } = parseSseChunk(chunk, '')
    expect(events).toHaveLength(1)
    expect(events[0].name).toBe('stage')
  })

  it('处理 CRLF 行尾', () => {
    const chunk = 'event: done\r\ndata: {"request_id":"x"}\r\n\r\n'
    const { events } = parseSseChunk(chunk, '')
    expect(events).toHaveLength(1)
    expect(events[0].name).toBe('done')
  })

  it('跳过注释行（心跳）', () => {
    const chunk = ': keep-alive\n\nevent: done\ndata: {"request_id":"x"}\n\n'
    const { events } = parseSseChunk(chunk, '')
    expect(events).toHaveLength(1)
    expect(events[0].name).toBe('done')
  })

  it('单条事件解析失败不影响后续事件', () => {
    // 中间一条是坏 JSON
    const chunk =
      'event: delta\ndata: {坏JSON}\n\n' +
      'event: done\ndata: {"request_id":"x"}\n\n'
    const { events } = parseSseChunk(chunk, '')
    // 只丢掉坏的那条
    expect(events).toHaveLength(1)
    expect(events[0].name).toBe('done')
  })

  it('忽略 OpenAI 兼容协议的 [DONE] 标记', () => {
    const chunk = 'event: done\ndata: {"request_id":"x"}\n\ndata: [DONE]\n\n'
    const { events } = parseSseChunk(chunk, '')
    expect(events).toHaveLength(1)
  })

  it('多行 data 按规范用换行连接', () => {
    // 规范：多个 data: 行用 U+000A 连接后作为一个完整事件数据。
    //
    // fixture 说明：真实服务端会把一个 JSON 拆到多个 data: 行吗？不会——
    // FastAPI 的 ServerSentEvent 每个事件只写一个 data: 行（实测确认）。
    // 故此处用一个**语义上确实能被 \n 连接成合法 JSON** 的载荷：
    // 两个字符串元素连接后是 ["a","b"]，这才是规范描述的场景。
    //
    // 我第一次写成 `{"text":"第一"` + `"`，那是把 JSON 结构拆两半，
    // 连接后必然非法——**fixture 写错，不是解析器错**。
    const chunk = 'event: delta\ndata: ["\\u884c",\ndata: "\\u52a8"]\n\n'
    const { events } = parseSseChunk(chunk, '')
    //载荷是数组（JSON 解析后保持其类型），中文已还原
    expect(events[0].data).toEqual(['行', '动'])
  })

  it('未闭合的尾块留给下一次，不丢失', () => {
    const { events, rest } = parseSseChunk('event: done\ndata: {"a":1}', '')
    expect(events).toHaveLength(0)
    expect(rest).toBe('event: done\ndata: {"a":1}')
  })
})

describe('toSourceItem', () => {
  it('把 edition 映射为 version（实测两层字段名不同）', () => {
    const s = toSourceItem({
      source_id: 'org-001',
      title: '自编讲义',
      edition: 'project-authored',
      locator: '第三章 烃',
    })
    expect(s.version).toBe('project-authored')
  })

  it('从章节号推断 locator_kind', () => {
    const s = toSourceItem({ source_id: 'a', title: 't', locator: '第三章 烃' })
    expect(s.locator_kind).toBe('chapter')
  })

  it('从页码推断 locator_kind', () => {
    const s = toSourceItem({ source_id: 'a', title: 't', locator: '第 12 页' })
    expect(s.locator_kind).toBe('page')
  })

  it('无法推断时回落 unknown，不猜', () => {
    // 错标页码比不标更糟——学生会去找一个不存在的页
    const s = toSourceItem({ source_id: 'a', title: 't', locator: '见附录' })
    expect(s.locator_kind).toBe('unknown')
  })

  it('缺 locator 时不报错且 locator_kind 为 unknown', () => {
    const s = toSourceItem({ source_id: 'a', title: 't' })
    expect(s.locator).toBe('')
    expect(s.locator_kind).toBe('unknown')
  })

  it('review_status 恒为 unknown，不写 approved', () => {
    // 后端刻意不写审核结论；前端若写 approved 就是伪造事实
    const s = toSourceItem({ source_id: 'a', title: 't' })
    expect(s.review_status).toBe('unknown')
  })
})
