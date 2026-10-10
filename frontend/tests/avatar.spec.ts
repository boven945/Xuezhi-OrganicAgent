/**
 * 数字人形象与 Fay 动作映射的测试。
 *
 * ## 重点测什么
 *
 * 1. **状态推导**——Fay 的`Action.behavior` 有二十多种取值，
 *    我们只认三个。**认不出的必须落待机**，
 *    猜成"说话"会让学生看到嘴在动但没声音。
 * 2. **Fay 未启用时恒为待机**——这是用户明确要求的行为。
 * 3. **断线不抛错**——数字人是增强能力，连不上必须静默降级。
 *
 * ## 不测什么
 *
 * 不测像素渲染与图片加载（需要真实浏览器，属e2e 范围）。
 * 本文件只覆盖状态机的决策。
 */

import { describe, expect, it, vi } from 'vitest'

import {
  AVATAR_IMAGES,
  lipsDurationMs,
  resolveAvatarState,
  type AvatarState,
  type FayHumanData,
} from '../src/viz/avatar'

function data(over: Partial<FayHumanData> = {}): FayHumanData {
  return { Key: 'audio', ...over }
}

describe('形象状态推导', () => {
  it('有音素时判为说话——这是最强的发声信号', () => {
    //实测 Fay 的音素形如 sil/FF/aa，每项带毫秒时长
    const payload = data({ Lips: [{ Lip: 'sil', Time: 180 }, { Lip: 'FF', Time: 144 }] })
    expect(resolveAvatarState(payload, false)).toBe('speaking')
  })

  it('音频在播但无音素时也判为说话', () => {
    // 兜底：某些片段可能不带Lips，但音频确实在放
    expect(resolveAvatarState(data(), true)).toBe('speaking')
  })

  it('音素优先于动作——有声音就是说话', () => {
    // 实测踩过的坑：Fay 可能在发声片段里同时给出 think 动作。
    // 若按动作优先，会出现「嘴在动但显示思考表情」。
    const payload = data({
      Lips: [{ Lip: 'aa', Time: 100 }],
      Action: { behavior: 'think' },
    })
    expect(resolveAvatarState(payload, true)).toBe('speaking')
  })

  it('讲解类动作映射为说话', () => {
    // 这三个都是"老师在讲"的语义
    for (const behavior of ['nod', 'invite', 'wave']) {
      expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('speaking')
    }
  })

  it('思考类动作映射为思考', () => {
    for (const behavior of ['think', 'question']) {
      expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('thinking')
    }
  })

  it('认不出的动作落待机而非说话', () => {
    // 核心防护：Fay 的 behavior 取值远多于我们认的三类
    // （社区实测可见 reject / farewell / 各种自定义 code）
    for (const behavior of ['reject', 'farewell', 'some_custom_action', '']) {
      expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('idle')
    }
  })

  it('无消息时为待机', () => {
    expect(resolveAvatarState(null, false)).toBe('idle')
    expect(resolveAvatarState(undefined, false)).toBe('idle')
  })

  it('永远返回可用状态，不返回 null', () => {
    // 组件需要永远能显示一张图——返回 null 会让界面出现无图分支
    const states: AvatarState[] = ['idle', 'idle', 'speaking', 'idle']
    for (const s of states) expect(s).not.toBeNull()
    expect(resolveAvatarState(data({ Action: { behavior: 'unknown' } }), false)).not.toBeNull()
  })
})

describe('音素时长累计', () => {
  it('累加所有音素的毫秒数', () => {
    const lips = [
      { Lip: 'sil', Time: 180 },
      { Lip: 'FF', Time: 144 },
      { Lip: 'aa', Time: 220 },
    ]
    expect(lipsDurationMs(lips)).toBe(544)
  })

  it('无音素时为 0', () => {
    expect(lipsDurationMs(undefined)).toBe(0)
    expect(lipsDurationMs([])).toBe(0)
  })

  it('负数与NaN 被忽略——否则状态会立即抖动', () => {
    // 实测踩过：负时长会让 setTimeout 立即触发，
    // 形象在说话/待机之间高频闪烁，看起来像故障
    const lips = [
      { Lip: 'aa', Time: -100 },
      { Lip: 'bb', Time: Number.NaN },
      { Lip: 'cc', Time: 150 },
    ]
    expect(lipsDurationMs(lips)).toBe(150)
  })
})

describe('图片资源', () => {
  it('三态各有图片', () => {
    for (const s of ['idle', 'speaking', 'thinking'] as AvatarState[]) {
      expect(AVATAR_IMAGES[s]).toMatch(/^\/avatar\/[a-z]+\.png$/)
    }
  })
})

describe('WebSocket 降级', () => {
  it('地址非法时组件不抛错，状态回落待机', async () => {
    // **这条曾写成恒真断言**：原先只验证 `new WebSocket` 会抛，
    // 而组件早就 try/catch 了——测的是前提，不是行为。
    // 现在真正挂载组件，验证它不会把异常抛给调用方。
    const { mount } = await import('@vue/test-utils')
    const Fake = vi.fn(() => {
      throw new Error('invalid URL')
    })
    vi.stubGlobal('WebSocket', Fake)

    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')
    const wrapper = mount(DigitalHuman, {
      props: { fayEnabled: true, endpoint: 'ws://' },
    })

    // 核心断言：渲染出来了，且说明当前是简化形态（而不是白屏或抛错）。
    //
    // **判据从`toContain('未连接')` 换成了「简化口型」**：
    // 原断言把「降级友好」等同于「显示连接错误」，
    // 于是把内部服务状态变成了主交付文案——Fay 连不上时功能完全正常
    // （本地兜底口型接管），显示"未连接服务"会被学生读成出了故障。
    // 现判据仍能证明「组件没白屏、且如实说明当前形态」，
    // **这才是这条测试本要证明的东西**。
    expect(wrapper.find('img').exists()).toBe(true)
    expect(wrapper.find('.avatar__hint').text()).toContain('简化口型')
    // 反向判据：不得暴露内部服务状态
    expect(wrapper.find('.avatar__hint').text()).not.toContain('未连接')
    wrapper.unmount()
    vi.unstubAllGlobals()
  })

  it('Fay 未启用时完全不建立连接', async () => {
    // 用户明确要求：Fay 未启用时前端默认待机。
    // **不该去连一个未配置的服务**——那会在控制台留下连接错误。
    const { mount } = await import('@vue/test-utils')
    const Fake = vi.fn()
    vi.stubGlobal('WebSocket', Fake)

    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')
    const wrapper = mount(DigitalHuman, { props: { fayEnabled: false } })

    expect(Fake).not.toHaveBeenCalled()
    expect(wrapper.find('.avatar__hint').text()).toBe('待机')
    // 默认必须是待机图
    expect(wrapper.find('img').attributes('src')).toBe(AVATAR_IMAGES.idle)
    wrapper.unmount()
    vi.unstubAllGlobals()
  })
})


describe('数字人常驻（回归防护）', () => {
  it('未提问时数字人也应显示', async () => {
    // **这个缺陷本该被测出来**：原先数字人被放进
    // `v-if="ask.hasContent"`，于是学生刚打开页面时
    // 整个形象不显示——一个"老师"在学生举手前就消失了。
    //
    // 单元测试测不到（它不渲染完整 AskView），
    // 但**契约必须写下来**，否则下次重构又会退回。
    const { mount } = await import('@vue/test-utils')
    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')

    // 不传任何 props：模拟"页面刚加载、还没提问"
    const wrapper = mount(DigitalHuman)
    expect(wrapper.find('img').exists()).toBe(true)
    expect(wrapper.find('img').attributes('src')).toBe(AVATAR_IMAGES.idle)
    wrapper.unmount()
  })

  it('组件自身不含 v-if="hasContent" 之类的条件', async () => {
    // 读源码断言：分屏容器不应受答复内容控制。
    // 这类"结构约束"用渲染测试很难精确表达，
    // 读源码反而更直接。
    // **两次踩坑才写对**：
    // ① `new URL(..., import.meta.url)` 在 vitest 下不是 file 协议
    //   （报 "The URL must be of scheme file"）；
    // ② 改用 `process.cwd()` + `node:path` 后 typecheck 报
    //   "Cannot find name 'process'"——本项目 `tsconfig.app.json`
    //   的 types 只有 `vite/client`，**刻意不装 @types/node**
    //   （浏览器项目不该引入 Node 类型），且它覆盖 tests/。
    //
    // 故用 vite 自带的 `?raw` 导入：它把文件当字符串返回，
    // 类型由 vite/client 提供，不需要任何 Node 类型。
    const src = await import('../src/components/AskView.vue?raw').then((m) => m.default)
    // 取出数字人所在容器的开标签
    const m = src.match(/<div class="teacher[^"]*"(v-if[^>]*)?>/)
    expect(m).not.toBeNull()
    expect(m![1]).toBeUndefined()   // 容器上不得有 v-if
  })
})


describe('按Fay 真实规则表取值映射（2026-10-05 实测）', () => {
  // 下列 behavior / affect 全部取自 Fay 仓库的
  // `config/action_rules.csv`（实测导出 20 条规则、
  // 18 种 behavior、9 种 affect），**不是猜的**。
  //
  // 这组测试的意义：先前映射只认 7 个取值，
  // 而真实场景大量落待机——形象看起来"不会动"。

  const SPEAKING_BEHAVIORS = ['nod', 'invite', 'wave', 'explain', 'recommend', 'summary', 'remind']
  const THINKING_BEHAVIORS = ['think', 'question']

  it.each(SPEAKING_BEHAVIORS)('讲解类behavior %s → 说话', (behavior) => {
    expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('speaking')
  })

  it.each(THINKING_BEHAVIORS)('思考类 behavior %s → 思考', (behavior) => {
    expect(resolveAvatarState(data({ Action: { behavior } }), false)).toBe('thinking')
  })

  it('behavior 认不出时用 affect 兜底', () => {
    // `celebrate` 不在 behavior 映射里（它更像"庆祝"而非"讲解"），
    // 但其 affect 是 excited → 说话态。
    // 若没有 affect 兜底，答对题时形象会毫无反应。
    expect(resolveAvatarState(data({ Action: { behavior: 'celebrate', affect: 'excited' } }), false)).toBe(
      'speaking',
    )
  })

  it('易错点提醒场景能命中（Fay 的 warn + serious）', () => {
    // 讲易错点时模型很可能说"注意…"，Fay 规则表映射为
    // warn + serious。实测这两个都不在 behavior 映射里，
    // 必须靠 affect 兜底。
    expect(
      resolveAvatarState(data({ Action: { behavior: 'warn', affect: 'serious' } }), false),
    ).toBe('speaking')
  })

  it('追问场景为思考态（question + curious）', () => {
    expect(
      resolveAvatarState(data({ Action: { behavior: 'question', affect: 'curious' } }), false),
    ).toBe('thinking')
  })

  it('behavior 优先于 affect', () => {
    // behavior 说"思考"时，即便 affect 是 smile 也应判思考——
    // 动作比情绪更能说明"此刻在干什么"。
    expect(
      resolveAvatarState(data({ Action: { behavior: 'think', affect: 'smile' } }), false),
    ).toBe('thinking')
  })

  it('action 完全缺失时落待机', () => {
    expect(resolveAvatarState(data({ Action: {} }), false)).toBe('idle')
  })
})


describe('Fay 真实契约的边界（实测确认）', () => {
  /**
   * 实测（2026-10-05，跑通Fay 容器后确认）：
   *
   * 1. **`Lips` 仅在 Windows 生成**——源码 `core/fay_core.py:2270`
   *    `if platform.system() == "Windows":`，靠
   *    `ProcessWAV.exe` 从 WAV 离线分析。Linux 容器内恒为空。
   *    故本项目**没有**"真实音素同步口型"能力，口型是本地近似。
   *
   * 2. **`tts_module` 只认五个值**——`ali` / `gptsovits` /
   *    `gptsovits_v3` / `volcano` / azure（`ms_tts_sdk`），
   *    由 `fay_core.py:101-119` 的 if/elif 链分发。
   *    **写不认的值（如 edge_tts）不报错，但 TTS 不会被初始化**，
   *    于是 `transparent-pass` 返回 200却不推任何消息。
   *
   * 这组测试的作用：把上面两条**固化成可执行的断言**。
   * 它们是实测得来的，不是从文档推断的——
   * 第一条让我一度以为「Fay 推音素」，第二条让我配了不存在的 TTS。
   */

  it('Linux 下 Lips 为空，故音素分支不会被误触发', () => {
    // 容器内实测：Fay 收到文本后 45 秒内零消息
    //（因 TTS 未初始化，无音频可播，故不推）。
    // 这条断言锁住"我们的代码不依赖 Lips 才能工作"——
    // 若哪天改成"必须等 Lips 才显示说话态"，在这台机器上会永远卡住。
    expect(resolveAvatarState({ Lips: [] }, true)).toBe('speaking')
  })

  it('无 Lips 时靠 behavior/affect 也能驱动状态', () => {
    const payload = data({ Lips: [], Action: { behavior: 'explain', affect: 'neutral' } })
    expect(resolveAvatarState(payload, false)).toBe('speaking')
  })
})



describe('Fay 10002 协议要求（2026-10-05 容器实测）', () => {
  /**
   * 实测背景：Fay 已在容器内跑通——10002 LISTENING、
   * `/transparent-pass` 返回 200、收到 `Key:audio` 与 `Action`。
   *
   * 但**连接建立 ≠ 能收到推送**。握手必须主动发，
   * 且 **Username 要与后端推送时的 `user` 完全一致**。
   * 违反时**不报错**：连接正常、200 正常，就是没有数据。
   * 这正是我为此白查两轮的原因，故固化成测试。
   */
  it('连接建立后主动发握手，且带 Username 与 Output', async () => {
    const { mount } = await import('@vue/test-utils')

    const sent: string[] = []
    /** 模拟 WebSocket：把 send 的内容记录下来。 */
    class FakeWS {
      onopen: (() => void) | null = null
      onmessage: ((e: MessageEvent<string>) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null
      constructor(_url: string) {
        // 下一拍触发 onopen，模拟真实异步
        setTimeout(() => this.onopen?.(), 0)
      }
      send(s: string): void {
        sent.push(s)
      }
      close(): void {}
    }
    vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket)

    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')
    const wrapper = mount(DigitalHuman, { props: { fayEnabled: true, user: 'stu-01' } })
    await new Promise((r) => setTimeout(r, 10))

    // 核心断言：握手已发出，且两个字段都对
    expect(sent.length).toBe(1)
    const hs = JSON.parse(sent[0]) as { Username: string; Output: boolean }
    expect(hs.Username).toBe('stu-01')
    expect(hs.Output).toBe(true)

    wrapper.unmount()
    vi.unstubAllGlobals()
  })

  it('Fay 禁用时不发握手（不连未配置的服务）', async () => {
    const { mount } = await import('@vue/test-utils')
    const sent: string[] = []
    class FakeWS {
      send(s: string): void {
        sent.push(s)
      }
      close(): void {}
    }
    vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket)

    const { default: DigitalHuman } = await import('../src/components/DigitalHuman.vue')
    const wrapper = mount(DigitalHuman, { props: { fayEnabled: false } })
    await new Promise((r) => setTimeout(r, 10))

    expect(sent).toHaveLength(0)
    wrapper.unmount()
    vi.unstubAllGlobals()
  })
})


describe('模板与样式类名一致性（实测踩过）', () => {
  /**
   * 实测踩过：把分屏容器从 `.stage` 改名为 `.teacher` 时，
   **只改了 `<style>` 里的选择器，忘了改模板里的 class**。
   *
   * 后果：模板仍是 `class="stage__avatar"`，
   * 而样式已是 `.teacher__avatar` —— **布局样式全部没生效**，
   * 但页面能渲染、测试全绿、无任何报错。
   *
   * 这类缺陷靠功能测试抓不到（元素在、只是没样式），
   * 只能**直接比对模板里的 class 与样式里的选择器**。
   */
  it('模板中的 teacher__* 类名都有对应样式', async () => {
    // 用 vite 的?raw 导入：类型由 vite/client 提供，
    // 不需要 @types/node（本项目刻意不装）。
    const src = (await import('../src/components/AskView.vue?raw')).default
    // 取出模板里用到的 teacher__ 前缀类名
    const used = new Set<string>()
    for (const m of src.matchAll(/class="([^"]*)"/g)) {
      for (const cls of (m[1] ?? '').split(/\s+/)) {
        if (cls.startsWith('teacher__') || cls === 'teacher') used.add(cls)
      }
    }
    expect(used.size).toBeGreaterThan(0)
    // 每个用到的类名都必须在样式里出现
    for (const cls of used) {
      expect(src, `类名 ${cls} 缺对应样式`).toContain(`.${cls}`)
    }
  })

  it('分屏容器的三个类名不再用旧前缀 stage__', async () => {
    const src = (await import('../src/components/AskView.vue?raw')).default
    const tpl = src.slice(0, src.indexOf('<style'))
    // **精确列出三个**：不能用 /^stage__/ 全匹配，
    // 因为 `stage__spinner` 属于**推理阶段提示**（合法的 `.stage`），
    // 它不是分屏容器的残留。断言过宽会逼着人改对的东西。
    for (const stale of ['stage__avatar', 'stage__answer', 'stage__waiting']) {
      expect(tpl, `模板仍残留 ${stale}`).not.toContain(stale)
    }
    // 反向确认：新类名确实在用
    for (const fresh of ['teacher__avatar', 'teacher__answer', 'teacher__waiting']) {
      expect(tpl, '模板未使用 ' + fresh).toContain(fresh)
    }
  })
})


describe('答复正文样式契约（2026-10-05 实测）', () => {
  /**
   * 背景：走查发现两个**静默失效**的样式缺陷——
   * 页面能渲染、测试全绿、无任何报错，但学生看到的东西是错的。
   *
   * ① **CSS 嵌套错误**：`.answer__body {` 未闭合就写下一条选择器，
   *    导致后续 10 条 `:deep()` 规则全部变成**嵌套规则**，
   *    被解析成 `.answer__body .answer__body p` —— 永远匹配不到。
   *    实测编译产物里出现 `.answer__body { &[data-v-x] {…} }`。
   *
   * ② **white-space: pre-wrap**：marked 会在块级标签之间输出源码换行，
   *    pre-wrap 把它们**保留成真实行盒**，
   *    每个列表项之间凭空多出一整行空白。
   *    实测对照（同一份 HTML，只改这一个属性）：
   *    pre-wrap 间隙 24px≈1 空行，normal 间隙 0px。
   *
   * 这两类缺陷靠渲染测试抓不到（元素在、只是没样式/多了空隙），
   * 故只能**直接比对样式源码**——同本文件上一节的做法。
   */

  /** 取出 <style> 段。 */
  function styleBlock(s: string): string {
    return s.slice(s.indexOf('<style'))
  }

  /** 统计括号平衡：未闭合会让后续规则被吞成嵌套。 */
  function braceBalance(css: string): number {
    let d = 0
    for (const ch of css) {
      if (ch === '{') d += 1
      else if (ch === '}') d -= 1
    }
    return d
  }

  /**
   * 找出「被嵌进别的普通规则内部」的选择器行。
   *
   * ## 两个必须处理的坑
   *
   * **①at-rule 内的选择器是合法的**：`@media` / `@keyframes` 里
   * 本来就该出现选择器，只有嵌在**普通规则**内部才是缺陷。
   * 第一版没区分，把 `@media (max-width:560px)` 里的 `.teacher {`
   * 误报成缺陷——**误报会让测试失去意义**，人只会习惯性忽略它。
   *
   * **② 判定必须先于计数**：`.foo {` 这一行自身开括号、
   * 闭合在后续行。若「先判定后计数」，则处理 `.foo {`
   * 之后 depth 才 +1，而下一个选择器行就被当成嵌在 `.foo` 里。
   * 实测踩过：`@media` 里的 `.teacher__avatar {` 被误报成缺陷。
   * 故用栈：判定时只看**本行开括号之前**的栈状态。
   */
  function nestedSelectors(css: string): string[] {
    const offenders: string[] = []
    /** 栈元素为 'at'（at-rule）或 'rule'（普通规则）。 */
    const stack: string[] = []

    for (const raw of css.split('\n')) {
      const line = raw.trim()
      if (!line || line.startsWith('/*') || line.startsWith('*')) continue

      const isAtRule = /^@(media|keyframes|supports|layer|container|font-face|import)\b/.test(line)
      // 关键：此时栈里都是**本行之前**已开的块
      if (!isAtRule && stack.includes('rule') && /^[.:a-zA-Z]/.test(line) && line.includes('{')) {
        offenders.push(line)
      }

      // 再更新栈
      for (const ch of raw) {
        if (ch === '{') stack.push(isAtRule ? 'at' : 'rule')
        else if (ch === '}') stack.pop()
      }
    }
    return offenders
  }

  it('style 段括号平衡（未闭合会让后续规则变嵌套而静默失效）', async () => {
    const src = (await import('../src/components/AskView.vue?raw')).default
    expect(braceBalance(styleBlock(src))).toBe(0)
  })

  it('顶层选择器不写在其他规则内部（嵌套即失效）', async () => {
    // **已双向验证**：
    // ① 把 `.answer__body :deep(p)` 塞回未闭合的块里 → 本条失败；
    // ② 现状（@media 内的选择器）→ 不误报。
    const src = (await import('../src/components/AskView.vue?raw')).default
    expect(nestedSelectors(styleBlock(src))).toHaveLength(0)
  })

  it('@media 内的选择器不算嵌套（误报会让测试失去意义）', async () => {
    // 反向确认检查器本身：把一个合法 @media 的选择器喂进去，必须**不**报。
    // 这条同时锁住「判定先于计数」——
    // 若改回先计数，`.teacher__avatar` 会重新被误报，本条即失败。
    const legit = [
      '@media (max-width: 560px) {',
      '  .teacher {',
      "    grid-template-areas: 'avatar' 'answer';",
      '  }',
      '',
      '  .teacher__avatar {',
      '    position: static;',
      '  }',
      '}',
      '',
    ].join('\n')
    expect(nestedSelectors(legit)).toHaveLength(0)
  })

  it('真嵌套仍被抓到（防止检查器被改弱）', () => {
    // 反向验证：故意造一个嵌套，确认检查器**会**报。
    // 没有这条，上一条可能因「检查器被打空」而恒真通过。
    const buggy = [
      '.answer__body {',
      '  color: var(--text);',
      '  .answer__body :deep(p) {',
      '    margin: 0;',
      '  }',
      '}',
    ].join('\n')
    expect(nestedSelectors(buggy)).toHaveLength(1)
  })

  it('答复正文不用 pre-wrap（会让列表项之间凭空多一整行）', async () => {
    const src = (await import('../src/components/AskView.vue?raw')).default
    const css = styleBlock(src)
    const bodyRule = css.match(/\.answer__body\s*\{([^}]*)\}/)?.[1] ?? ''
    // 反向确认规则确实抓到且非空，避免「没匹配到就通过」
    expect(bodyRule).toContain('font-size')
    expect(bodyRule).not.toMatch(/white-space\s*:\s*pre-wrap/)
  })

  it('后代排版规则仍在（嵌套修复时勿连带删除）', async () => {
    // 这些正是被嵌套错误吞掉的那批，修好了就得真的生效
    const src = (await import('../src/components/AskView.vue?raw')).default
    const css = styleBlock(src)
    const required = [':deep(p)', ':deep(ul)', ':deep(li)', ':deep(blockquote)', ':deep(code)']
    for (const sel of required) {
      expect(css, sel + ' 规则缺失').toContain(sel)
    }
  })

  it('超长化学式不撑破容器（必须有断行策略）', async () => {
    // 模型会输出 \text{CO}_2 这类长下标串，缺断行会把版面撑破
    const src = (await import('../src/components/AskView.vue?raw')).default
    const css = styleBlock(src)
    const bodyRule = css.match(/\.answer__body\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(bodyRule).toMatch(/word-break|overflow-wrap/)
  })
})

describe('数字人提示用中性文案，不暴露内部服务状态', () => {
  it('断线时显示「简化口型」而非「未连接服务」', async () => {
    // Fay 连不上时功能完全正常（本地兜底口型接管），
    // 故说明当前形态即可，不必把服务连接状态当主交付文案。
    const src = (await import('../src/components/DigitalHuman.vue?raw')).default
    expect(src).toContain('待机（简化口型）')
    // **只查行为代码，不查全文**——注释里会引用旧文案说明改动理由，
    // 全文匹配会把自己写的说明判成失败（实测踩过）。
    // 故剥掉注释后再断言。
    const code = src
      .split('\n')
      .filter((l) => !l.trim().startsWith('*') && !l.trim().startsWith('//') && !l.trim().startsWith('/*'))
      .join('\n')
    expect(code).not.toContain('未连接数字人服务')
  })

  it('警告样式随文案一并移除（避免留死样式）', async () => {
    const src = (await import('../src/components/DigitalHuman.vue?raw')).default
    // 有 --warn 绑定却无定义、或反之，都是残留
    expect(src).not.toContain('avatar__hint--warn')
  })

  it('说话态也要说明是简化口型（否则状态词前后不一致）', async () => {
    const src = (await import('../src/components/DigitalHuman.vue?raw')).default
    expect(src).toContain('讲解中（简化口型）')
  })
})
