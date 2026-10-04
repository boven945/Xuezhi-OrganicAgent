/**
 * 答复正文 Markdown 渲染的测试。
 *
 * ## 重点：XSS 防护
 *
 * 这是本文件存在的**首要理由**。渲染走`v-html`，
 * 等于把模型输出当 HTML 执行——消毒一旦失效就是可利用的注入。
 * 故XSS 用例**逐条对应真实攻击向量**，不写"看起来像"的输入。
 *
 * ## 实测发现的真实问题（2026-10-05）
 *
 * 模型返回 Markdown，学生看到的是原始 `**羟基**`。
 * 这是**主交付不可读**，不是样式瑕疵——
 * 故本文件也锁住"真实模型输出能被正确渲染"这一行为。
 */

import { describe, expect, it } from 'vitest'

import { renderMarkdown, stripMarkdown } from '../src/api/markdown'

describe('Markdown 渲染', () => {
  it('渲染粗体——实测模型最常产出这个', () => {
    const html = renderMarkdown('乙醇的官能团是**羟基（—OH）**。')
    expect(html).toContain('<strong>')
    expect(html).toContain('羟基（—OH）')
    // 标签外不应残留星号
    expect(html).not.toContain('**')
  })

  it('渲染标题（实测模型会产出）', () => {
    expect(renderMarkdown('### 主要区别')).toContain('<h3>')
  })

  it('渲染引用（实测模型会产出）', () => {
    const html = renderMarkdown('> 教材原文：乙醇俗称酒精。')
    expect(html).toContain('<blockquote>')
    expect(html).toContain('教材原文')
  })

  it('渲染无序列表（实测模型会产出）', () => {
    const html = renderMarkdown('- 羟基连接在饱和碳上\n- 分子式 C2H6O')
    expect(html).toContain('<ul>')
    expect(html).toContain('<li>')
  })

  it('空输入返回空串', () => {
    expect(renderMarkdown('')).toBe('')
  })

  it('段落被保留——化学式常带换行', () => {
    const html = renderMarkdown('第一段。\n\n第二段。')
    expect(html).toContain('<p>')
    expect((html.match(/<p>/g) ?? []).length).toBe(2)
  })
})

describe('XSS 防护（安全边界）', () => {
  it('script 标签被转义，不产生可执行标签', () => {
    const html = renderMarkdown('<script>alert(1)</script>')
    expect(html).not.toMatch(/<script/i)
    // 内容保留为文本——学生还能看到模型说了什么
    expect(html).toContain('alert(1)')
  })

  it('img 标签被整体转义（不产生可执行元素）', () => {
    // **断言曾写错**：原先断言 `not.toContain('onerror')`，
    // 但正确行为是**整个标签转义成文本**——于是字符串里
    // 仍会出现 "onerror" 这几个字母（作为可见文本）。
    // 那正是我们要的：学生能看到模型说了什么，
    // 而浏览器不会执行它。
    //
    // 因此断言必须是"没有可执行标签"，
    // 而非"文本里不含某关键词"。
    const html = renderMarkdown('<img src=x onerror="alert(1)">')
    expect(html).not.toMatch(/<img/i)
    // 标签被转义为可见文本，内容保留
    expect(html).toContain('&lt;img')
  })

  it('javascript: 伪协议的 href 被丢弃', () => {
    const html = renderMarkdown('[点我](javascript:alert(1))')
    expect(html).not.toContain('javascript:')
  })

  it('允许的标签也不会带上事件属性', () => {
    // 攻击者把事件属性挂在**白名单内**的标签上
    const html = renderMarkdown('<strong onclick="alert(1)">粗体</strong>')
    expect(html).toContain('<strong>')
    expect(html).not.toContain('onclick')
  })

  it('style 属性被剥离——避免模型样式盖住课件配色', () => {
    const html = renderMarkdown('<p style="color:red">红字</p>')
    expect(html).not.toContain('style')
  })

  it('iframe 被转义', () => {
    const html = renderMarkdown('<iframe src="https://evil.test"></iframe>')
    expect(html).not.toMatch(/<iframe/i)
  })

  it('svg 标签被整体转义', () => {
    const html = renderMarkdown('<svg onload="alert(1)"></svg>')
    expect(html).not.toMatch(/<svg/i)
    expect(html).not.toMatch(/<\/svg/i)
  })

  it('闭合标签也走白名单（不只开标签）', () => {
    // 攻击者用不认识的闭合标签破坏结构
    const html = renderMarkdown('正常文本</customtag>')
    expect(html).not.toMatch(/<\/customtag/i)
  })

  it('http 链接的 href 被保留', () => {
    const html = renderMarkdown('[教材](https://example.test/a)')
    expect(html).toContain('href="https://example.test/a"')
  })
})

describe('剥掉 Markdown 标记（供语音朗读）', () => {
  it('去掉粗体标记——否则会念出星号', () => {
    expect(stripMarkdown('乙醇含**羟基**。')).toBe('乙醇含羟基。')
  })

  it('去掉标题井号与引用符号', () => {
    expect(stripMarkdown('### 标题')).toBe('标题')
    expect(stripMarkdown('> 引用内容')).toBe('引用内容')
  })

  it('列表去掉前导符号', () => {
    expect(stripMarkdown('- 甲\n- 乙')).toBe('甲\n乙')
  })

  it('链接只保留可见文本', () => {
    expect(stripMarkdown('见[讲义](https://x.test)')).toBe('见讲义')
  })

  it('空输入返回空串', () => {
    expect(stripMarkdown('')).toBe('')
  })
})
