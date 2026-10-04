/**
 * 前端端到端验证：用真实浏览器跑一遍完整流程。
 *
 * ## 为什么必须用真浏览器
 *
 * 单元测试用 happy-dom，**它没有真实的 fetch / ReadableStream /
 * TextDecoder 行为**。SSE 解析、逐 token 追加、代理转发这三件事
 * 都只能在真浏览器里验证。
 *
 * 运行：node e2e/verify.mjs（需先起前后端）
 */

import { chromium } from 'playwright'
import { mkdirSync, existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const FRONTEND = process.env.E2E_FRONTEND ?? 'http://127.0.0.1:5178'
const SHOT_DIR = 'e2e/shots'
mkdirSync(SHOT_DIR, { recursive: true })

/**
 * 定位 Chromium 可执行文件。
 *
 * ## 为什么不直接 `chromium.launch()`
 *
 * Playwright 默认找的是 `chromium_headless_shell-<ver>`，
 * 而本机只装了完整版 `chromium-<ver>`——两者是不同产物。
 * 等它下完可能要几十分钟（实测 33 分钟仍未完成）。
 *
 * 故：优先用环境变量指定的路径；否则扫已安装版本目录取最新；
 * 都找不到才回退到 Playwright 默认行为（由它给出清晰报错）。
 */
function resolveChromium() {
  if (process.env.E2E_CHROMIUM) return process.env.E2E_CHROMIUM

  const base = join(homedir(), 'AppData', 'Local', 'ms-playwright')
  if (!existsSync(base)) return undefined

  const candidates = readdirSync(base)
    .filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]))

  for (const dir of candidates) {
    for (const rel of ['chrome-win64/chrome.exe', 'chrome-win/chrome.exe', 'chrome-linux/chrome']) {
      const p = join(base, dir, rel)
      if (existsSync(p)) return p
    }
  }
  return undefined
}

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`)
}

const executablePath = resolveChromium()
console.log(`Chromium: ${executablePath ?? '(用 Playwright 默认)'}`)
const browser = await chromium.launch(executablePath ? { executablePath } : {})
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })

// 收集控制台错误——**渲染错误不会让 HTTP 失败**，只能靠这个发现
const consoleErrors = []
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))

try {
  // ── 1. 首屏 ──
  await page.goto(FRONTEND, { waitUntil: 'networkidle' })
  check('页面加载', await page.locator('.app__name').isVisible(), '品牌名可见')

  // 健康状态徽章
  const healthText = await page.locator('.health__text').textContent()
  check('健康状态已加载', healthText.length > 0, `显示"${healthText}"`)

  // ── 2. 分子解析 ──
  await page.getByRole('tab', { name: '分子解析' }).click()

  // 本机 RDKit 的C++ 扩展被应用控制策略拦截，故 /health 报 chem 不可用。
  // **两种环境都要覆盖**：完整环境验证解析结果，降级环境验证提示正确。
  const chemDown = (await page.locator('.notice--warn').count()) > 0

  if (chemDown) {
    const noticeText = await page.locator('.notice--warn').textContent()
    check(
      '化学不可用时提前说明原因',
      noticeText.includes('化学引擎') && noticeText.includes('问答功能不受影响'),
      noticeText.replace(/\s+/g, ' ').trim().slice(0, 50),
    )
    const btn = page.getByRole('button', { name: '解析' })
    check('降级时解析按钮禁用', await btn.isDisabled())
    await page.screenshot({ path: `${SHOT_DIR}/02-chem-degraded.png`, fullPage: true })
  } else {
    await page.locator('#smiles').fill('CCO')
    await page.getByRole('button', { name: '解析' }).click()
    await page.waitForSelector('.result', { timeout: 10_000 })

    const formula = await page.locator('.props__row dd').first().textContent()
    check('分子解析返回分子式', formula?.trim() === 'C2H6O', `得到 ${formula?.trim()}`)

    const groupNames = await page.locator('.group__name').allTextContents()
    // 决策项 I5 后应为「醇羟基」而非笼统的「羟基」
    check(
      '乙醇只识别出醇羟基',
      groupNames.length === 1 && groupNames[0] === '醇羟基',
      `实得 [${groupNames.join(', ')}]`,
    )

    // 苯酚：验证 I5 的另一半（酚羟基）
    await page.locator('#smiles').fill('c1ccccc1O')
    await page.getByRole('button', { name: '解析' }).click()
    await page.waitForTimeout(900)
    const phenolGroups = await page.locator('.group__name').allTextContents()
    check(
      '苯酚识别出酚羟基（I5 另一半）',
      phenolGroups.includes('酚羟基') && !phenolGroups.includes('醇羟基'),
      `实得 [${phenolGroups.join(', ')}]`,
    )
    await page.screenshot({ path: `${SHOT_DIR}/02-molecule.png`, fullPage: true })
  }

  // ── 3. 流式问答 ──
  await page.getByRole('tab', { name: '问答' }).click()
  await page.locator('#question').fill('苯酚的酸性为什么比碳酸弱？')
  await page.screenshot({ path: `${SHOT_DIR}/01-ask-empty.png`, fullPage: true })

  await page.getByRole('button', { name: '提问' }).click()

  // 建流后应立刻出现 request_id（meta 首发的意义）
  await page.waitForSelector('.stage__rid', { timeout: 8_000 })
  const rid = await page.locator('.stage__rid').textContent()
  check('meta 事件首发送出 request_id', rid?.trim().length > 0, `request_id 前缀 ${rid?.trim()}`)

  // 逐 token 追加：抓一个**真正的中间态**。
  // 关键：不能只断言「有文本」——流太快时等到的是完成态，
  // 那与同步返回无区别，测不出流式是否真的在工作。
  await page.waitForSelector('.answer__body', { timeout: 15_000 })
  await page.waitForFunction(
    () => (document.querySelector('.answer__body')?.textContent?.trim().length ?? 0) > 3,
    { timeout: 10_000 },
  )
  const midText = (await page.locator('.answer__body').textContent())?.trim() ?? ''
  await page.screenshot({ path: `${SHOT_DIR}/03-streaming.png`, fullPage: true })
  check('流式过程中已有部分文本', midText.length > 0, `中途已有 ${midText.length} 字`)

  // 等待完成
  await page.waitForFunction(
    () => !document.querySelector('.stage__spinner'),
    { timeout: 40_000 },
  )
  const finalText = (await page.locator('.answer__body').textContent()) ?? ''
  check(
    '答复为完整中文（非 \\u 转义）',
    finalText.includes('苯酚') && !finalText.includes('\\u'),
    `最终 ${finalText.trim().length} 字：${finalText.slice(0, 30)}…`,
  )
  // **流式真正工作的证据**：中途的文本比最终的短。
  // 只断言「有文本」是不够的——若等待时间过长拿到的是完成态，
  // 那个结果与同步返回完全相同，测不出流式是否生效。
  check(
    '逐token 追加（中途短于最终）',
    midText.length < finalText.trim().length,
    `中途 ${midText.length} 字 < 最终 ${finalText.trim().length} 字`,
  )

  // 来源区
  const sourceTitle = await page.locator('.source__title').first().textContent()
  check('来源区显示讲义标题', sourceTitle?.includes('讲义') === true, `显示"${sourceTitle?.trim()}"`)

  // 审核状态——**关键：不得显示为已认证**
  const reviewText = await page.locator('.source__review').first().textContent()
  check(
    '来源标注待审核而非已认证',
    reviewText?.includes('待') && !reviewText.includes('已认证'),
    `显示"${reviewText?.trim()}"`,
  )

  const locatorKind = await page.locator('.source__locator-kind').first().textContent()
  check('定位类型被正确推断', locatorKind?.trim() === '章节', `显示"${locatorKind?.trim()}"`)

  await page.screenshot({ path: `${SHOT_DIR}/04-answer-done.png`, fullPage: true })

  // ── 4. 字符计数与禁用态 ──
  const countText = await page.locator('.composer__count').textContent()
  check('显示字符计数', /\d+ \/ 4096/.test(countText ?? ''), `显示"${countText?.trim()}"`)

  // ── 5. 明暗主题 ──
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOT_DIR}/05-dark.png`, fullPage: true })
  const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  check('暗色主题生效', darkBg !== 'rgb(250, 250, 249)', `body 背景 ${darkBg}`)

  await page.emulateMedia({ colorScheme: 'light' })
  await page.waitForTimeout(200)

  // ── 6. 控制台无错误 ──
  check(
    '控制台无错误',
    consoleErrors.length === 0,
    consoleErrors.length > 0 ? consoleErrors.slice(0, 3).join(' | ') : '',
  )
} catch (err) {
  check(`执行异常: ${err.message}`, false)
  await page.screenshot({ path: `${SHOT_DIR}/99-error.png`, fullPage: true }).catch(() => {})
} finally {
  await browser.close()
}

const passed = results.filter((r) => r.ok).length
console.log(`\n结果：${passed}/${results.length} 通过`)
console.log(`截图目录：${SHOT_DIR}`)
process.exit(passed === results.length ? 0 : 1)
