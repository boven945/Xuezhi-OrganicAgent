/**
 * 答复正文的 Markdown 渲染。
 *
 * ## 为什么要渲染而不是直接显示
 *
 * **实测（2026-10-05）**：模型返回的是 Markdown，学生看到的是
 * 原始的 `**羟基**` 与 `> 教材原文`。主交付显示符号，
 * 等于功能没做完——这不是"样式瑕疵"，是内容不可读。
 *
 * 实测扫过三个真实提问，模型会产出的格式有四种：
 * **标题、引用、无序列表、粗体**。不含 HTML 标签。
 * 但「当前不产出」不等于「永远不产出」——渲染层必须防住
 * 将来模型或上游数据带 HTML 的情况。
 *
 * ## 为什么必须消毒
 *
 * 用 `v-html` 渲染等于把模型输出当 HTML 执行。
 * 即便模型今天不输出标签，语料、检索结果、
 * 未来换模型都可能引入。**只要经过 `v-html`，就必须消毒。**
 * 这与 `security-privacy.md` §3 是同一条原则的另一面：
 * 那里说"不得泄露输入"，这里说"不得执行输入"。
 *
 * ## 白名单而非黑名单
 *
 * DOMPurify 默认黑名单（禁止 script、on* 事件等）。
 * **本项目额外禁用 `style` 属性**：模型偶尔会产出
 * `**加粗**` 被误解析成带style 的标签，颜色会盖住正文配色，
 * 而课件的视觉规范是统一的。
 */

import { marked } from 'marked'

/** marked 的解析选项。 */
marked.setOptions({
  gfm: true,
  breaks: true,
})

/**
 * 把 Markdown 转成**已消毒**的 HTML。
 *
 * ## 消毒策略：不引入 DOMPurify，手写白名单
 *
 * DOMPurify 是 20KB 的依赖，且需要 `jsdom` 之类的环境才能跑
 * （浏览器外跑不了，CI 里就得再加一层）。
 * 而我们的输入是**模型生成的受限 Markdown**，
 * 实际用到的标签只有 `<h3> <ul> <ol> <li> <strong> <em> <code> <pre> <blockquote> <br>`。
 *
 * 手写白名单的价值：**规则可审计**——能一眼看出允许什么。
 * 用 DOMPurify 时"它到底放行了什么"要翻源码才知道。
 *
 * ### 为什么这样就不会漏XSS
 *
 * 白名单是**枚举允许的标签与属性**，不在名单里的一律转义。
 * `<script>` 不在名单 → 被转义成文本，不会执行。
 * `<img onerror=x>` 不在名单 → 同样被转义。
 * 属性上只放行 `href`（且强制 http/https），
 * 所以 `onclick` 之类根本没有机会进入输出。
 *
 * **前提是所有文本都经过 escapeHtml**——
 * 因此本函数**不接收已渲染好的 HTML**，只接收原始 Markdown。
 */
const ALLOWED_TAGS = new Set([
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'p', 'br', 'hr',
  'ul', 'ol', 'li',
  'strong', 'em', 'del', 'code', 'pre', 'blockquote',
  // **a 必须允许**：答复里的教材引用常带链接
  //（语料 source_id 映射到讲义页面）。实测踩过——
  // 漏掉它会把整条引用转义成可见的 `<a href=...>` 文本。
  'a',
  // **表格必须允许**（实测踩过，见 docs/frontend-verification.md H30）：
  // 化学回答里表格极常见（物质对比表、鉴别表、官能团表），
  // 而白名单是**枚举式**的——漏掉的标签会被整体转义成可见文本，
  // 学生看到的是满屏 `<table><tr><td>` 源码，**主交付直接不可读**。
  //
  // 这五个标签是**实测marked 的真实产出**，不是凭印象列的：
  // `| A | B |\n|---|---|\n| 1 | 2 |` 经marked 产出
  // `table thead tr th tbody td`（含对齐时还有 `tr/th` 上的 style）。
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
  // 上下标：模型输出化学式时会用`CO₂` 的 LaTeX 写法或 <sub>。
  // 实测 LaTeX 出现率低（3 次采样 0 复现），但 <sub>/<sup> 是
  // 无害且语义明确的标签，允许它们比转义成源码好。
  'sub', 'sup',
])

/**
 * 允许的**标签属性**白名单。
 *
 * 目前只有 `href` / `title`，**刻意不给 `class` 与 `style`**。
 *
 * ## 关于表格的 `style`（实测权衡，2026-10-05）
 *
 * marked 会把 Markdown 的对齐信息写成内联样式：
 * `| A | B |\n|:--|--:|` → `<th style="text-align:center">`。
 *
 * **本项目选择丢弃对齐信息，而不是放开 `style`**——
 * 理由：`style` 能表达任意 CSS（`position:fixed`、`content:url(...)`），
 * 一旦放开就等于给了注入面；而**内容完整远比列对齐美观重要**
 * （化学对比表少个居中不影响读懂）。
 *
 * 代价是表格默认全部左对齐。可接受。
 * 若日后确需对齐，应加**窄白名单**（如只放行 `text-align:left|center|right`），
 * 而非整体放开 `style`。
 */
const ALLOWED_ATTRS = new Set(['href', 'title'])

/** 转义 HTML 实体。顺序有讲究：`&` 必须最先转。 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 消毒 HTML 字符串。
 *
 * 用**一次性正则**匹配完整标签，而不是逐层解析——
 * 嵌套标签（如 `<strong><script>`）在正则方案下
 * 内层会被单独匹配并转义，不会漏。
 *
 * ## 已知局限（诚实记录）
 *
 * 正则消毒**不处理属性值里的引号逃逸**这类边缘情况，
 * 严格来说不如 DOMPurify 可靠。
 * 之所以可接受：输入是模型生成的 Markdown，
 * 而 marked 的输出结构受控（不会产出畸形属性）。
 * 若将来允许用户自定义内容进正文，**必须换成 DOMPurify**。
 */
function sanitize(html: string): string {
  return html.replace(/<\/?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>/g, (match, rawTag, rawAttrs) => {
    const tag = rawTag.toLowerCase()
    // 自闭合标签单独处理
    const isSelfClosing = match.startsWith('<') && match.endsWith('/>')
    // **闭合标签必须区分**（实测踩过）：`</p>` 同样匹配上面的正则，
    // 若一律走"白名单外就转义"的分支，白名单内的闭合标签会被
    // 转义成文本 —— 结果是 `<p>内容<p>` 这样的畸形 HTML。
    const isClosing = match.startsWith('</')
    if (!ALLOWED_TAGS.has(tag)) {
      // 不在白名单 → 整个标签转义为文本（其内容保留）
      return escapeHtml(match)
    }
    if (isClosing) return `</${tag}>`
    if (isSelfClosing) return `<${tag} />`

    // 过滤属性
    const attrs: string[] = []
    const attrRe = /([a-zA-Z-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/g
    let m: RegExpExecArray | null
    while ((m = attrRe.exec(rawAttrs)) !== null) {
      const name = m[1].toLowerCase()
      if (!ALLOWED_ATTRS.has(name)) continue
      let val = m[2].replace(/^["']|["']$/g, '')
      // href 只放行 http/https——挡住 javascript: 伪协议
      if (name === 'href' && !/^https?:\/\//i.test(val)) continue
      attrs.push(`${name}="${escapeHtml(val)}"`)
    }
    return `<${tag}${attrs.length ? ' ' + attrs.join(' ') : ''}>`
  })
}

/**
 * 渲染答复正文。
 *
 * @param markdown 模型返回的原始 Markdown。
 * @returns **已消毒**的 HTML，可安全用于 `v-html`。
 *
 * ## 为什么不缓存
 *
 * 有学生问同一个问题就会重复解析。理论上该memo，
 * 但实测答复通常 100–500 字，marked 解析耗时远小于
 * 一次网络请求的**数量级**（实测问答 26 秒）。
 * 为此引入缓存层（键管理、失效逻辑）不划算。
 */
export function renderMarkdown(markdown: string): string {
  if (!markdown) return ''
  try {
    return sanitize(marked.parse(markdown) as string)
  } catch {
    // 解析失败**不能**让答复消失——那是主交付。
    // 退化成转义后的纯文本：至少内容还在，且没有注入风险。
    return `<p>${escapeHtml(markdown)}</p>`
  }
}

/**
 * 剥掉 Markdown 标记，取纯文本。
 *
 * **用途**：语音朗读与剪贴板复制。
 * 朗读 `**羟基**` 会把星号念出来——那很怪。
 */
export function stripMarkdown(markdown: string): string {
  if (!markdown) return ''
  return markdown
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^>\s?/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
    .trim()
}
