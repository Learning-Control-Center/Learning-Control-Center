import { chromium } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const output = process.argv[2]
if (!output) throw new Error('Output path is required.')
const baseURL = process.env.LCC_PRODUCT_BASE_URL
const fixtureSize = Number(process.env.LCC_ROADMAP_FIXTURE_SIZE)
const repeats = Number(process.env.LCC_ROADMAP_REPEATS ?? 30)
const warmups = Number(process.env.LCC_ROADMAP_WARMUPS ?? 5)
if (!baseURL || ![100, 250].includes(fixtureSize)) throw new Error('Measurement environment is incomplete.')

const percentile = (values, fraction) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * fraction) - 1]
const summarize = (samples) => {
  const keys = Object.keys(samples[0])
  return Object.fromEntries(keys.map((key) => [key, percentile(samples.map((sample) => sample[key]), 0.5)]))
}
const p95 = (samples) => {
  const keys = Object.keys(samples[0])
  return Object.fromEntries(keys.map((key) => [key, percentile(samples.map((sample) => sample[key]), 0.95)]))
}

async function login(page) {
  await page.goto(baseURL)
  await page.getByLabel('Username').fill('fixture-learner')
  await page.getByLabel('Password').fill('fixture-password-with-enough-entropy')
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => candidate.url().endsWith('/api/v1/auth/login')),
    page.getByRole('button', { name: 'Sign in' }).click(),
  ])
  if (!response.ok()) throw new Error(`Fixture login failed with ${response.status()}.`)
}

async function sample(browser, cacheMode, storageState, activationMode) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState })
  const page = await context.newPage()
  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: cacheMode === 'cold' })
  await page.addInitScript(() => {
    window.__LCC_LONG_TASKS__ = []
    new PerformanceObserver((list) => window.__LCC_LONG_TASKS__.push(...list.getEntries().map((entry) => ({ startTime: entry.startTime, duration: entry.duration })))).observe({ type: 'longtask', buffered: true })
  })
  if (cacheMode === 'warm') {
    await page.goto(`${baseURL}/roadmap?__measure=1&__geometry=all`)
    await page.locator('[data-roadmap-ready="true"]').waitFor()
  }
  await page.goto(`${baseURL}/roadmap?__measure=1&__geometry=all`)
  await page.locator('[data-roadmap-ready="true"]').waitFor({ timeout: 120000 })
  const navigationDuration = await page.evaluate(() => performance.now())
  const navLongTasks = await page.evaluate(() => window.__LCC_LONG_TASKS__ ?? [])
  await page.evaluate(() => { window.__LCC_LONG_TASKS__ = [] })
  await page.getByRole('button', { name: 'Fit all' }).click()
  const mountedNodeCount = await page.locator('.roadmap-node').count()
  if (mountedNodeCount !== fixtureSize) throw new Error(`Expected ${fixtureSize} mounted nodes; found ${mountedNodeCount}.`)
  const target = page.locator('.roadmap-node').filter({ hasText: 'Roadmap capability 6 with a representative long title' }).getByRole('button')
  await target.waitFor({ state: 'attached' })
  const map = page.locator('[data-roadmap-map-surface]')
  for (let index = 0; index < 30; index += 1) {
    const [targetBounds, mapBounds] = await Promise.all([target.boundingBox(), map.boundingBox()])
    if (!targetBounds || !mapBounds) throw new Error('Target or Map bounds are unavailable.')
    const targetX = targetBounds.x + targetBounds.width / 2
    const targetY = targetBounds.y + targetBounds.height / 2
    const minimumX = mapBounds.x + 48
    const maximumX = mapBounds.x + mapBounds.width - 48
    const minimumY = mapBounds.y + 48
    const maximumY = mapBounds.y + mapBounds.height - 96
    if (targetX >= minimumX && targetX <= maximumX && targetY >= minimumY && targetY <= maximumY) break
    if (targetX < minimumX) await page.getByRole('button', { name: 'Pan left' }).click()
    else if (targetX > maximumX) await page.getByRole('button', { name: 'Pan right' }).click()
    if (targetY < minimumY) await page.getByRole('button', { name: 'Pan up' }).click()
    else if (targetY > maximumY) await page.getByRole('button', { name: 'Pan down' }).click()
  }
  const finalTargetBounds = await target.boundingBox()
  const finalMapBounds = await map.boundingBox()
  if (!finalTargetBounds || !finalMapBounds || finalTargetBounds.x < finalMapBounds.x || finalTargetBounds.y < finalMapBounds.y || finalTargetBounds.x + finalTargetBounds.width > finalMapBounds.x + finalMapBounds.width || finalTargetBounds.y + finalTargetBounds.height > finalMapBounds.y + finalMapBounds.height) throw new Error('The declared selection target could not be positioned inside the Map viewport.')
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.evaluate(() => { window.__LCC_LONG_TASKS__ = [] })
  const commitsBefore = await page.evaluate(() => window.__LCC_ROADMAP_COMMITS__ ?? 0)
  const nodeRendersBefore = await page.evaluate(() => window.__LCC_ROADMAP_NODE_RENDERS__ ?? 0)
  await target.evaluate((element, mode) => {
    window.__LCC_SELECTION_START__ = undefined
    const eventName = mode === 'keyboard' ? 'keydown' : 'click'
    element.addEventListener(eventName, () => { window.__LCC_SELECTION_START__ = performance.now() }, { capture: true, once: true })
  }, activationMode)
  if (activationMode === 'keyboard') {
    await target.focus()
    await page.keyboard.press('Enter')
  } else {
    await target.click()
  }
  await page.locator('[data-roadmap-detail-settled]').waitFor()
  const selectionStart = await page.evaluate(() => window.__LCC_SELECTION_START__)
  if (typeof selectionStart !== 'number') throw new Error('Trusted selection activation was not observed.')
  const selectionEnd = await page.evaluate(() => performance.now())
  const selectionLongTasks = await page.evaluate(() => window.__LCC_LONG_TASKS__ ?? [])
  const commitsAfter = await page.evaluate(() => window.__LCC_ROADMAP_COMMITS__ ?? 0)
  const nodeRendersAfter = await page.evaluate(() => window.__LCC_ROADMAP_NODE_RENDERS__ ?? 0)
  await context.close()
  return {
    navigationStartToRoadmapReadyMs: Math.round(navigationDuration * 100) / 100,
    selectionToSettledDetailMs: selectionEnd - selectionStart,
    navigationLongTaskCount: navLongTasks.length,
    navigationLongTaskTotalMs: Math.round(navLongTasks.reduce((sum, item) => sum + item.duration, 0) * 100) / 100,
    selectionLongTaskCount: selectionLongTasks.length,
    selectionLongTaskTotalMs: Math.round(selectionLongTasks.reduce((sum, item) => sum + item.duration, 0) * 100) / 100,
    reactCommitsPerSelection: commitsAfter - commitsBefore,
    journeyNodeRendersPerSelection: nodeRendersAfter - nodeRendersBefore,
  }
}

const browser = await chromium.launch({ channel: 'chrome' })
const browserVersion = browser.version()
const byMode = {}
try {
  const setupContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const setupPage = await setupContext.newPage()
  await login(setupPage)
  const storageState = await setupContext.storageState()
  await setupContext.close()
  for (const mode of ['cold', 'warm']) {
    for (let index = 0; index < warmups; index += 1) await sample(browser, mode, storageState, index % 2 ? 'keyboard' : 'pointer')
    byMode[mode] = []
    for (let index = 0; index < repeats; index += 1) byMode[mode].push(await sample(browser, mode, storageState, index % 2 ? 'keyboard' : 'pointer'))
  }
} finally {
  await browser.close()
}

const all = [...byMode.cold, ...byMode.warm]
const median = { cold: summarize(byMode.cold), warm: summarize(byMode.warm) }
const p95Values = { cold: p95(byMode.cold), warm: p95(byMode.warm) }
const stress = fixtureSize === 250
const navBudget = stress ? { coldMedian: 4500, coldP95: 6500, warmMedian: 2500, warmP95: 4000 } : { coldMedian: 3000, coldP95: 4500, warmMedian: 1500, warmP95: 2500 }
const selectionBudget = stress ? { median: 250, p95: 500 } : { median: 150, p95: 300 }
const longTaskBudget = stress ? { count: 6, total: 500 } : { count: 3, total: 250 }
const commitBudget = stress ? { median: 8, p95: 14 } : { median: 6, p95: 10 }
const nodeRenderBudget = stress ? { median: 12, p95: 20 } : { median: 10, p95: 16 }
const maxSelectionMedian = Math.max(median.cold.selectionToSettledDetailMs, median.warm.selectionToSettledDetailMs)
const maxSelectionP95 = Math.max(p95Values.cold.selectionToSettledDetailMs, p95Values.warm.selectionToSettledDetailMs)
const budgetVerdicts = {
  navigation: median.cold.navigationStartToRoadmapReadyMs <= navBudget.coldMedian && p95Values.cold.navigationStartToRoadmapReadyMs <= navBudget.coldP95 && median.warm.navigationStartToRoadmapReadyMs <= navBudget.warmMedian && p95Values.warm.navigationStartToRoadmapReadyMs <= navBudget.warmP95,
  selection: maxSelectionMedian <= selectionBudget.median && maxSelectionP95 <= selectionBudget.p95,
  longTasks: all.every((item) => item.navigationLongTaskCount <= longTaskBudget.count && item.navigationLongTaskTotalMs <= longTaskBudget.total && item.selectionLongTaskCount <= longTaskBudget.count && item.selectionLongTaskTotalMs <= longTaskBudget.total),
  reactCommits: Math.max(median.cold.reactCommitsPerSelection, median.warm.reactCommitsPerSelection) <= commitBudget.median && Math.max(p95Values.cold.reactCommitsPerSelection, p95Values.warm.reactCommitsPerSelection) <= commitBudget.p95,
  journeyNodeRenders: Math.max(median.cold.journeyNodeRendersPerSelection, median.warm.journeyNodeRendersPerSelection) <= nodeRenderBudget.median && Math.max(p95Values.cold.journeyNodeRendersPerSelection, p95Values.warm.journeyNodeRendersPerSelection) <= nodeRenderBudget.p95,
}
const payload = {
  schemaVersion: 1,
  repositoryRevision: process.env.LCC_ROADMAP_REPOSITORY_REVISION,
  productionBuildHash: process.env.LCC_ROADMAP_BUILD_HASH,
  fixtureHash: process.env.LCC_ROADMAP_FIXTURE_HASH,
  fixtureSize,
  browser: { name: 'Google Chrome', version: browserVersion, executablePath: chromium.executablePath() },
  viewport: { width: 1440, height: 900, deviceScaleFactor: 1 },
  cacheMode: ['cold', 'warm'],
  samples: byMode,
  median,
  p95: p95Values,
  budgetVerdicts,
}
await writeFile(output, `${JSON.stringify(payload, null, 2)}\n`)
