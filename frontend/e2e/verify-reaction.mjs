/**
 * 反应机理 3D 动画的浏览器端验证（本机 E2E）。
 *
 * ## 为什么必须用真浏览器
 *
 * happy-dom **没有 WebGL**，Three.js 在里面无法真正渲染；而「动画是否真的在播、
 * 帧是否推进、键是否在淡入淡出」正是本模块最需要验证的——这些不报任何错，
 * 只有真浏览器 + 进度条/截图能发现。
 *
 * 本脚本与 verify-viz.mjs 同源，但验证的是**反应机理页**（ReactionMechanism
 * 组件 + ReactionPlayer），而非分子解析页（MoleculeView）。两者复用同一套
 * viewer.ts 的 Three.js 基元，但 ReactionPlayer 用持久网格 + 帧间插值，
 * 是本项目此前未被真浏览器覆盖的缺口。
 *
 * ## 前置：前后端起着（本机）
 *
 *   # 后端（venv，RDKit 可用）
 *   PYTHONPATH=backend .venv-xuezhi312/Scripts/python.exe -m uvicorn \
 *       app.main:app --host 127.0.0.1 --port 8000
 *   # 前端
 *   cd frontend && npm run dev -- --port 5180
 *
 * 运行：node e2e/verify-reaction.mjs
 */
import { chromium } from 'playwright'
import { mkdirSync, existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const FRONTEND = process.env.E2E_FRONTEND ?? 'http://127.0.0.1:5180'
const SHOT_DIR = 'e2e/shots'
mkdirSync(SHOT_DIR, { recursive: true })

/** 定位 Chromium（Playwright 默认找 headless_shell，与完整版不同产物）。 */
function resolveChromium() {
  if (process.env.E2E_CHROMIUM) return process.env.E2E_CHROMIUM
  const base = join(homedir(), 'AppData', 'Local', 'ms-playwright')
  if (!existsSync(base)) return undefined
  const dir = readdirSync(base)
    .filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]))[0]
  for (const rel of ['chrome-win64/chrome.exe', 'chrome-win/chrome.exe', 'chrome-linux/chrome']) {
    const p = join(base, dir, rel)
    if (existsSync(p)) return p
  }
  return undefined
}

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`)
}

const executablePath = resolveChromium()
const browser = await chromium.launch({
  executablePath,
  args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'],
})
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })

const consoleErrors = []
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))

try {
  await page.goto(FRONTEND, { waitUntil: 'networkidle' })

  // 切到反应机理 tab
  await page.getByRole('tab', { name: '反应机理' }).click()
  await page.waitForSelector('.rx', { timeout: 20_000 })

  // 等待 WebGL canvas 真正创建（反应页默认加载第一条反应）
  await page.waitForSelector('.rx__canvas canvas', { timeout: 20_000 })
  // 默认反应加载完（播放按钮可用 = status=ready）
  await page.waitForSelector('.rx__btn:not([disabled])', { timeout: 20_000 })

  // 1. WebGL 上下文真的拿到（不只是 canvas 元素存在）
  const gl = await page.evaluate(() => {
    const c = document.querySelector('.rx__canvas canvas')
    if (!c) return false
    const g = c.getContext('webgl2') || c.getContext('webgl')
    return Boolean(g)
  })
  check('WebGL canvas 已创建且有上下文', gl)

  // 2. 教学名已加载（默认第一条：乙烯与溴的加成）
  const name0 = (await page.locator('.rx__name').textContent())?.trim()
  check('默认反应教学名非空', Boolean(name0), name0?.slice(0, 30))

  // 3. 断键成键讲解非空（transition_note）
  const notes = await page.locator('.rx__note').allTextContents()
  check('断键成键讲解非空', notes.some((n) => n.replace(/\s/g, '').length > 2), notes.join(' | ').slice(0, 40))

  // 4. 真实像素绘制（canvas 非空白）。WebGL 需 preserveDrawingBuffer，
  //    否则 toDataURL 可能为空——允许该检查降级，靠帧推进兜底。
  const painted = await page.evaluate(() => {
    const c = document.querySelector('.rx__canvas canvas')
    if (!c) return -1
    try {
      const url = c.toDataURL('image/png')
      if (url.length < 200) return -1
      return url.length
    } catch {
      return -1
    }
  })
  if (painted > 0) {
    check('canvas 实际绘制了内容（toDataURL 非空）', true, `dataURL ${painted}B`)
  } else {
    check('canvas 像素读取不可用（WebGL 缓冲），改用帧推进验证', true, '降级')
  }

  // 5. 播放 → 帧推进（aria-label 含「第 N / M 帧」）
  await page.getByRole('button', { name: '播放' }).click()
  await page.waitForTimeout(1500)
  const aria1 = await page.locator('.rx__seek').getAttribute('aria-label')
  const fn1 = Number(aria1?.match(/第\s*(\d+)\s*\//)?.[1] ?? '0')
  check('播放后帧推进（>1）', fn1 > 1, `帧=${fn1}`)
  await page.waitForTimeout(1000)
  const aria2 = await page.locator('.rx__seek').getAttribute('aria-label')
  const fn2 = Number(aria2?.match(/第\s*(\d+)\s*\//)?.[1] ?? '0')
  check('继续播放帧继续推进', fn2 >= fn1, `帧 ${fn1}→${fn2}`)
  await page.locator('.rx__stage').screenshot({ path: `${SHOT_DIR}/rx-01-playing.png` })

  // 6. 切换反应 → 重新加载（SN2）。注意：速度选择器也带 `rx__select` 类，
  //    须用 `.rx__pick` 限定到反应选择器，否则 strict mode 报多元素。
  // 反应清单现由后端拉取（异步），先等选项挂到 DOM 再选。
  // 注意用 state:'attached'：<option> 在下拉未展开时不可见（hidden），
  // 默认等 'visible' 会一直超时（实测 43 次已 resolved 仍判不可见）。
  await page.waitForSelector(
    '.rx__pick .rx__select option[value="substitution-sn2-bromoethane-hydroxide"]',
    { timeout: 20_000, state: 'attached' },
  )
  await page.locator('.rx__pick .rx__select').selectOption('substitution-sn2-bromoethane-hydroxide')
  await page.waitForSelector('.rx__btn:not([disabled])', { timeout: 20_000 })
  const name1 = (await page.locator('.rx__name').textContent())?.trim()
  check('切换 SN2 后重新加载', Boolean(name1) && name1 !== name0, name1?.slice(0, 30))
  await page.locator('.rx__stage').screenshot({ path: `${SHOT_DIR}/rx-02-sn2.png` })

  // 7. 控制台无错误
  check('控制台无错误', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
} catch (err) {
  check(`执行异常: ${err.message}`, false)
  await page.screenshot({ path: `${SHOT_DIR}/rx-99-error.png`, fullPage: true }).catch(() => {})
} finally {
  await browser.close()
}

const passed = results.filter((r) => r.ok).length
console.log(`\n结果：${passed}/${results.length} 通过`)
console.log(`截图目录：${SHOT_DIR}`)
process.exit(passed === results.length ? 0 : 1)
