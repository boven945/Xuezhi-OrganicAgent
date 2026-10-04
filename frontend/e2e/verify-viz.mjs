/**
 * 3D 可视化的浏览器端验证。
 *
 * ## 为什么必须用真浏览器
 *
 * happy-dom **没有 WebGL**，Three.js 在里面无法真正渲染。
 * 而「画面是否正确」正是本模块最需要验证的：
 * 索引错位会让原子飘到错误位置——**不报任何错**。
 * 故只有真浏览器 + 截图能发现。
 *
 * 运行：node e2e/verify-viz.mjs（需前后端起着，且后端 RDKit 可用）
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
  args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
})
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })

const consoleErrors = []
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))

try {
  await page.goto(FRONTEND, { waitUntil: 'networkidle' })

  //化学引擎可用才验证 3D；降级环境下本脚本无意义
  const chemDown = (await page.locator('.notice--warn').count()) > 0
  await page.getByRole('tab', { name: '分子解析' }).click()
  const degraded = (await page.locator('.notice--warn').count()) > 0
  if (degraded) {
    check('化学引擎不可用，跳过 3D 验证', false, '后端 RDKit 不可用，无法验证 3D')
    throw new Error('SKIP_DEGRADED')
  }

  // ── 1. 解析苯酚（含苯环，最能体现 3D 价值）──
  await page.locator('#smiles').fill('c1ccccc1O')
  await page.getByRole('button', { name: '解析' }).click()
  await page.waitForSelector('.result', { timeout: 20_000 })

  // ── 2. WebGL canvas 真的创建了吗 ──
  await page.waitForSelector('.viz__canvas canvas', { timeout: 20_000 })
  const canvasInfo = await page.evaluate(() => {
    const c = document.querySelector('.viz__canvas canvas')
    if (!c) return null
    const gl = c.getContext('webgl2') || c.getContext('webgl')
    return {
      w: c.width,
      h: c.height,
      hasGL: Boolean(gl),
      renderer: gl ? gl.getParameter(gl.RENDERER) : null,
    }
  })
  check(
    'WebGL canvas 已创建且有上下文',
    Boolean(canvasInfo?.hasGL) && canvasInfo.w > 0,
    canvasInfo ? `${canvasInfo.w}x${canvasInfo.h} renderer=${canvasInfo.renderer}` : '未创建',
  )

  // ── 3. canvas 是否有非空像素（**关键：canvas 存在不等于画出了东西**）──
  const painted = await page.evaluate(() => {
    const c = document.querySelector('.viz__canvas canvas')
    if (!c) return { ratio: 0, note: 'no canvas' }
    // 用 2d 上下文读回：WebGL canvas 需要 preserveDrawingBuffer
    // 才能读，故这里改用截图区域判断
    return { ratio: -1 }
  })
  // 上面这条无法直接读像素（WebGL 需 preserveDrawingBuffer），
  // 改用「截图后统计非背景像素占比」——见下方。
  void painted

  await page.waitForTimeout(1200)
  const shot = await page.locator('.viz__stage').screenshot({ path: `${SHOT_DIR}/viz-01-phenol.png` })

  // 统计 canvas 区域的像素方差：全白/全透明 = 没画东西
  const variance = await page.evaluate(async () => {
    const c = document.querySelector('.viz__canvas canvas')
    if (!c) return -1
    // 通过 toDataURL 读取（需 preserveDrawingBuffer，否则可能为空）
    try {
      const url = c.toDataURL('image/png')
      if (url.length < 100) return -1
      const img = new Image()
      await new Promise((res, rej) => {
        img.onload = res
        img.onerror = rej
        img.src = url
      })
      const tmp = document.createElement('canvas')
      tmp.width = img.width
      tmp.height = img.height
      const ctx = tmp.getContext('2d')
      ctx.drawImage(img, 0, 0)
      const d = ctx.getImageData(0, 0, tmp.width, tmp.height).data
      let nonTransparent = 0
      for (let i = 3; i < d.length; i += 4) if (d[i] > 10) nonTransparent += 1
      return nonTransparent / (d.length / 4)
    } catch {
      return -1
    }
  })
  // 至少 0.5% 像素被绘制——分子占画面一小部分是正常的
  check(
    'canvas 实际绘制了内容（非空白）',
    variance > 0.005,
    `非透明像素占比 ${(variance * 100).toFixed(2)}%`,
  )
  void shot

  // ── 4. 原子数标注与后端一致 ──
  const note = await page.locator('.viz__note').textContent()
  const m = note?.match(/(\d+)\s*个原子/)
  check('原子数与后端一致（13 = 苯酚 7 骨架 + 6 氢）', m?.[1] === '13', note?.trim().slice(0, 40))

  // ── 5. 切换显示方式 ──
  for (const label of ['填充', '线框', '球棍']) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await page.waitForTimeout(500)
    const stillOk = await page.evaluate(() => {
      const c = document.querySelector('.viz__canvas canvas')
      return Boolean(c && c.width > 0)
    })
    check(`切换到「${label}」模式正常`, stillOk)
  }
  await page.locator('.viz__stage').screenshot({ path: `${SHOT_DIR}/viz-02-ballstick.png` })

  // ── 6. 关闭氢：原子数不变但可见球减少（后端数据不变，前端只是不画）──
  await page.getByRole('button', { name: '球棍', exact: true }).click()
  await page.locator('.viz__check input').first().uncheck()
  await page.waitForTimeout(500)
  const noH = await page.locator('.viz__note').textContent()
  check('关闭氢后标注仍准确（后端数据未变）', noH?.includes('13'), noH?.trim().slice(0, 30))
  await page.locator('.viz__stage').screenshot({ path: `${SHOT_DIR}/viz-03-nohydrogen.png` })
  await page.locator('.viz__check input').first().check()

  // ── 7. 自动旋转 ──
  await page.locator('.viz__check input').nth(1).check()
  await page.waitForTimeout(700)
  const before = await page.locator('.viz__stage').screenshot()
  await page.waitForTimeout(700)
  const after = await page.locator('.viz__stage').screenshot()
  check('自动旋转时画面变化', !before.equals(after), Buffer.compare(before, after) !== 0 ? '帧间有差异' : '无差异')
  await page.locator('.viz__check input').nth(1).uncheck()

  // ── 8. 乙醇（小分子，检查缩放是否合理）──
  await page.locator('#smiles').fill('CCO')
  await page.getByRole('button', { name: '解析' }).click()
  await page.waitForTimeout(1200)
  await page.locator('.viz__stage').screenshot({ path: `${SHOT_DIR}/viz-04-ethanol.png` })
  const eNote = await page.locator('.viz__note').textContent()
  check('乙醇原子数正确（9 = 3 骨架 + 6 氢）', eNote?.includes('9'), eNote?.trim().slice(0, 30))

  // ── 9. 切换分子不泄漏（多次切换后仍能渲染）──
  for (const smi of ['CCO', 'c1ccccc1O', 'CC(=O)OC', 'CCO']) {
    await page.locator('#smiles').fill(smi)
    await page.getByRole('button', { name: '解析' }).click()
    await page.waitForTimeout(450)
  }
  const afterSwitches = await page.evaluate(() => {
    const c = document.querySelector('.viz__canvas canvas')
    return Boolean(c && c.width > 0)
  })
  check('多次切换分子后仍正常（无显存泄漏症状）', afterSwitches)
  await page.locator('.viz__stage').screenshot({ path: `${SHOT_DIR}/viz-05-after-switch.png` })

  // ── 10. 控制台无错误 ──
  check('控制台无错误', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
} catch (err) {
  if (err.message !== 'SKIP_DEGRADED') {
    check(`执行异常: ${err.message}`, false)
    await page.screenshot({ path: `${SHOT_DIR}/viz-99-error.png`, fullPage: true }).catch(() => {})
  }
} finally {
  await browser.close()
}

const passed = results.filter((r) => r.ok).length
console.log(`\n结果：${passed}/${results.length} 通过`)
console.log(`截图目录：${SHOT_DIR}`)
process.exit(passed === results.length ? 0 : 1)
