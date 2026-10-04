/**
 * 来源区组件测试。
 *
 * 重点覆盖**三种「没有来源」的区别**——
 * 后端在三种情况下都返回 `sources: []`，但对学生意味着不同结果：
 * 检索失败 / 检索了但没命中 / 根本没检索。
 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SourceList from '../src/components/SourceList.vue'
import type { SourceItem, ToolInvocation } from '../src/types/api'

const OK_INVOCATION: ToolInvocation[] = [{ tool: 'search_knowledge', ok: true, error_code: null }]
const FAILED_INVOCATION: ToolInvocation[] = [
  { tool: 'search_knowledge', ok: false, error_code: 'rag_unavailable' },
]

function makeSource(over: Partial<SourceItem> = {}): SourceItem {
  return {
    source_id: 'org-001',
    title: '有机化学自编讲义',
    locator: '第三章 烃',
    locator_kind: 'chapter',
    version: 'project-authored',
    review_status: 'unknown',
    ...over,
  }
}

describe('SourceList', () => {
  it('显示来源的标题与定位', () => {
    const w = mount(SourceList, {
      props: { sources: [makeSource()], invocations: OK_INVOCATION, retrieved: true },
    })
    expect(w.text()).toContain('有机化学自编讲义')
    expect(w.text()).toContain('第三章 烃')
    expect(w.text()).toContain('1 条')
  })

  it('检索失败时明确提示可能不完整', () => {
    const w = mount(SourceList, {
      props: { sources: [], invocations: FAILED_INVOCATION, retrieved: true },
    })
    expect(w.text()).toContain('检索未成功')
  })

  it('检索了但没命中时说明「未经讲义核实」', () => {
    // 这是最容易被忽略的一档：模型可能凭自身知识答对了，
    // 但学生无法区分「有依据」与「凭记忆」
    const w = mount(SourceList, {
      props: { sources: [], invocations: OK_INVOCATION, retrieved: true },
    })
    expect(w.text()).toContain('未找到相关段落')
    expect(w.text()).toContain('未经讲义核实')
  })

  it('未调用检索时如实说明', () => {
    const w = mount(SourceList, {
      props: { sources: [], invocations: [], retrieved: false },
    })
    expect(w.text()).toContain('未使用知识库检索')
  })

  it('不把待审核渲染成已认证', () => {
    // 后端恒返回 unknown；前端若显示「已认证」即为伪造事实
    const w = mount(SourceList, {
      props: { sources: [makeSource()], invocations: OK_INVOCATION, retrieved: true },
    })
    expect(w.text()).toContain('待化学教师审核')
    expect(w.text()).not.toContain('已认证')
  })

  it('approved 状态才显示「已审核」', () => {
    const w = mount(SourceList, {
      props: {
        sources: [makeSource({ review_status: 'approved' })],
        invocations: OK_INVOCATION,
        retrieved: true,
      },
    })
    expect(w.text()).toContain('已审核')
  })

  it('按定位类型选择中文标签', () => {
    const w = mount(SourceList, {
      props: {
        sources: [
          makeSource({ source_id: 'a', locator: '第 12 页', locator_kind: 'page' }),
          makeSource({ source_id: 'b', locator: '第三章', locator_kind: 'chapter' }),
        ],
        invocations: OK_INVOCATION,
        retrieved: true,
      },
    })
    expect(w.text()).toContain('页码')
    expect(w.text()).toContain('章节')
  })

  it('来源为空数组时不渲染列表', () => {
    const w = mount(SourceList, {
      props: { sources: [], invocations: [], retrieved: false },
    })
    expect(w.findAll('.source')).toHaveLength(0)
  })
})
