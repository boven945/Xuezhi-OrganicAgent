/**
 * 健康徽章的**契约一致性**测试。
 *
 * ## 为什么这个文件存在
 *
 * 2026-10-05 运行时走查发现：后端一切正常时，
 * 健康徽章却一直显示「检查中」——**实测 60 秒不熄灭**。
 * 学生打开页面第一眼看到的是一盏坏掉的状态灯。
 *
 * 根因是**两侧契约不一致**：
 *
 * |位置 | 认的值 |
 * | --- | --- |
 * | 后端 `routes.py` 实际产出 | `ok` / `degraded` / `not_ready` |
 * | 前端 `HealthBadge` 原本认| `healthy` / `degraded` / `unhealthy` |
 *
 * **只有 `degraded` 恰好对上**。于是后端返回 `ok` 时，
 * 前端落进 `default` 分支 → 显示「检查中」。
 *
 * 这类缺陷能通过全部测试：`status` 声明为 `string`，
 * 类型系统不做枚举校验；而组件渲染正常、页面不报错。
 *
 * **所以必须用真实的后端取值来测**，而不是自造一组「看起来对」的值。
 */

import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import HealthBadge from '../src/components/HealthBadge.vue'
import { useHealthStore } from '../src/stores'
import type { ComponentStatus } from '../src/types/api'

/**
 * 后端 `routes.py:105` 实际产出的三个取值。
 *
 * **来源是读源码，不是猜的**：
 *   status = "ok" if all(...) else "degraded"
 *   若 llm 未就绪则为 "not_ready"
 *
 * 若日后改后端，本测试会失败——那正是它该做的。
 */
const BACKEND_STATUSES = ['ok', 'degraded', 'not_ready'] as const

/** 造一个够用的 health store。 */
function makeHealth(over: {
  status: string
  reachable?: boolean
  components?: ComponentStatus[]
} = { status: 'unknown' }): ReturnType<typeof useHealthStore> {
  const store = useHealthStore()
  store.status = over.status as never
  store.reachable = over.reachable ?? true
  store.components = over.components ?? []
  return store
}

function badgeText(status: string): string {
  const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status }) } })
  return wrapper.find('.health__text').text()
}

/**
 * pinia 需要一个激活的实例，否则 `useHealthStore()` 报
 * "getActivePinia() was called but there was no active Pinia"。
 * 每个用例前重建，避免状态串味。
 */
beforeEach(() => {
  setActivePinia(createPinia())
})

describe('健康徽章认得后端真实取值', () => {
  it.each(BACKEND_STATUSES)('后端返回 %s 时不应显示「检查中」', (status) => {
    // 核心断言：用**后端实际产出的值**逐个试。
    // 任何落到 default 分支的值都会失败——这正是原缺陷。
    expect(badgeText(status)).not.toBe('检查中')
  })

  it('ok → 服务正常（绿）', () => {
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'ok' }) } })
    expect(wrapper.find('.health__text').text()).toBe('服务正常')
    expect(wrapper.find('.health').attributes('data-status')).toBe('ok')
  })

  it('degraded → 部分降级（黄），不是异常', () => {
    // 降级**不是故障**：chem 不可用时其余仍可用，
    // 显示红色会让学生以为整个系统坏了。
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'degraded' }) } })
    expect(wrapper.find('.health__text').text()).toBe('部分降级')
    expect(wrapper.find('.health').attributes('data-status')).toBe('warn')
  })

  it('not_ready → 服务异常（红）', () => {
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'not_ready' }) } })
    expect(wrapper.find('.health__text').text()).toBe('服务异常')
    expect(wrapper.find('.health').attributes('data-status')).toBe('err')
  })
})

describe('不可达与未知取值', () => {
  it('后端不可达时显示「后端未连接」', () => {
    const wrapper = mount(HealthBadge, {
      props: { health: makeHealth({ status: 'unreachable', reachable: false }) },
    })
    expect(wrapper.find('.health__text').text()).toBe('后端未连接')
  })

  it('未知取值显式暴露原值，不静默显示「检查中」', () => {
    // **刻意的行为**：后端加了新状态而前端没跟上时，
    // 「未知状态(xxx)」能一眼看出契约变了；
    // 而「检查中」会被当成"还在加载"，真坏了也不报警。
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'brand_new' }) } })
    const text = wrapper.find('.health__text').text()
    expect(text).toContain('未知状态')
    expect(text).toContain('brand_new')
    expect(text).not.toBe('检查中')
  })

  it('异常状态用红色而非 muted 灰', () => {
    // 灰色（muted）视觉上像"还没加载"，会掩盖真故障
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'brand_new' }) } })
    expect(wrapper.find('.health').attributes('data-status')).toBe('err')
  })
})

describe('组件明细（回归防护）', () => {
  it('组件清单能展开，不可用项显示原因', () => {
    // 徽章的价值就在这：chem 被拦截时要**如实展示**，
    // 否则学生会发现分子解析不能用却不知道原因。
    const components: ComponentStatus[] = [
      { name: 'chem', ready: false, detail: 'RDKit 未就绪' },
      { name: 'llm', ready: true, detail: '' },
    ]
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'degraded', components }) } })

    const items = wrapper.findAll('.health__item')
    expect(items).toHaveLength(2)
    expect(items[0]!.find('.health__state').text()).toBe('不可用')
    expect(items[0]!.find('.health__reason').text()).toBe('RDKit 未就绪')
    expect(items[1]!.find('.health__state').text()).toBe('可用')
  })

  it('无组件时清单整体不渲染', () => {
    const wrapper = mount(HealthBadge, { props: { health: makeHealth({ status: 'ok' }) } })
    expect(wrapper.find('.health__detail').exists()).toBe(false)
  })
})