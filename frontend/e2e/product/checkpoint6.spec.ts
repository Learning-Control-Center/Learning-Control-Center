import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

type Manifest = {
  scenarioId: string
  setupProfile: string
  status: string
  viewports: Record<string, { width: number; height: number }>
  entries: {
    id: string
    route: string
    viewport: string
    reducedMotion: 'reduce' | 'no-preference'
    setupSteps: { kind: 'authenticatedFixture' }[]
    interactionSteps: { kind: 'waitForText'; value: string }[]
    ready: { kind: 'heading' | 'roadmap' | 'selector'; name?: string; selector?: string }
    expected: {
      productState: string
      provenance: string
      assertions: ({ kind: 'text' | 'urlPattern'; value: string } | { kind: 'selector'; value: string })[]
    }
  }[]
}
const manifest = JSON.parse(readFileSync(new URL('../../qa/visual-product-qa-manifest.json', import.meta.url), 'utf8')) as Manifest

test.skip(process.env.LCC_PRODUCT_SCENARIO !== 'roadmap-25', 'Checkpoint 6 uses the representative populated fixture.')

async function login(page: Page, route = '/') {
  await page.goto(route)
  const username = page.getByLabel('Username')
  await expect(username.or(page.locator('#main-content'))).toBeVisible()
  if (await username.isVisible()) {
    await username.fill('fixture-learner')
    await page.getByLabel('Password').fill('fixture-password-with-enough-entropy')
    await page.getByRole('button', { name: 'Sign in' }).click()
  }
  await expect(page.locator('#main-content')).toBeVisible()
}

async function expectNoOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    offenders: [...document.querySelectorAll<HTMLElement>('body *')]
      .map((element) => {
        const rectangle = element.getBoundingClientRect()
        return { tag: element.tagName, text: element.textContent?.trim().slice(0, 80), left: rectangle.left, right: rectangle.right, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth }
      })
      .filter((item) => item.left < -1 || item.right > window.innerWidth + 1)
      .slice(0, 8),
  }))
  expect(overflow.documentWidth, JSON.stringify(overflow, null, 2)).toBeLessThanOrEqual(overflow.viewportWidth)
}

async function expectAxeClean(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(result.violations).toEqual([])
}

async function expectControlInViewport(page: Page, selector: import('@playwright/test').Locator) {
  await selector.scrollIntoViewIfNeeded()
  await expect(selector).toBeInViewport()
  const box = await selector.boundingBox()
  const viewport = page.viewportSize()
  expect(box).not.toBeNull()
  expect(viewport).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width + 1)
}

async function manifestRouteTokens(page: Page) {
  const [projectionResponse, curriculaResponse, projectsResponse] = await Promise.all([
    page.request.get('/api/v2/roadmap-projection/current'),
    page.request.get('/api/v2/curricula'),
    page.request.get('/api/v2/projects'),
  ])
  expect(projectionResponse.ok()).toBe(true)
  expect(curriculaResponse.ok()).toBe(true)
  expect(projectsResponse.ok()).toBe(true)
  const projection = await projectionResponse.json() as { nodes: { id: string }[] }
  const curricula = await curriculaResponse.json() as { id: string }[]
  const projects = await projectsResponse.json() as { id: string }[]
  expect(projection.nodes.length).toBeGreaterThan(0)
  expect(curricula.length).toBeGreaterThan(0)
  expect(projects.length).toBeGreaterThan(0)
  return {
    '{competencyId}': encodeURIComponent(projection.nodes[0].id),
    '{curriculumId}': encodeURIComponent(curricula[0].id),
    '{projectId}': encodeURIComponent(projects[0].id),
  }
}

function resolveManifestRoute(route: string, tokens: Record<string, string>) {
  return Object.entries(tokens).reduce((resolved, [token, value]) => resolved.replaceAll(token, value), route)
}

test('Checkpoint 6 @cross-browser covers shell, forms, Roadmap views, recovery, and responsive interaction', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await login(page)
  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()
  await expectNoOverflow(page)

  const compact = (page.viewportSize()?.width ?? 1440) < 768
  if (compact) {
    await page.getByRole('button', { name: 'Open navigation' }).click()
    const drawer = page.getByRole('dialog', { name: 'Application navigation' })
    await expect(drawer).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Open navigation' })).toBeFocused()
  }

  await page.getByRole('link', { name: 'Do something else' }).first().click()
  await expect(page.getByRole('heading', { name: 'Continue: Choose actual work' })).toBeVisible()
  const title = page.getByLabel('Activity title')
  const activityTitle = `Cross-browser Session ${testInfo.project.name}`
  await title.fill(activityTitle)
  await page.getByRole('button', { name: 'Create and use Activity' }).click()
  await expect(page.getByText('Activity selected. No suggestion or canonical reference has been changed yet.')).toBeVisible()
  await page.getByRole('button', { name: 'Start timer' }).click()
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
  await page.getByRole('button', { name: 'Resume' }).click()
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await page.getByRole('button', { name: 'Complete' }).click()
  await expect(page.getByText('Timer completed.')).toBeVisible()
  await expectNoOverflow(page)

  await page.goto('/roadmap?view=outline')
  await expect(page.locator('[data-roadmap-ready="true"]')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Outline' })).toHaveAttribute('aria-pressed', 'true')
  const firstRoadmapItem = page.locator('[data-roadmap-node-id]').first().getByRole('button')
  await firstRoadmapItem.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Focused path').first()).toBeVisible()
  const closeDetails = page.getByRole('button', { name: 'Close details' })
  await closeDetails.click()
  await expect(firstRoadmapItem).toBeFocused()
  await page.getByRole('button', { name: 'Map', exact: true }).click()
  if ((page.viewportSize()?.width ?? 1440) < 1280) {
    await expect(page.getByRole('button', { name: 'Interact with map' })).toBeVisible()
  } else {
    await expect(page.locator('[data-roadmap-map-surface]')).toBeVisible()
  }
  await expectNoOverflow(page)
  await expectAxeClean(page)

  let failHistory = true
  await page.route('**/api/v2/analysis/history', async (route) => {
    if (failHistory) await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'UNAVAILABLE', message: 'Compatibility retry fixture.' } }) })
    else await route.continue()
  })
  await page.goto('/insights/analysis')
  await expect(page.getByText('Compatibility retry fixture.')).toBeVisible()
  failHistory = false
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByRole('heading', { name: 'Diagnostic signals' })).toBeVisible()
  await expectAxeClean(page)

  if (testInfo.project.name === 'product-desktop-chromium') {
    await page.setViewportSize({ width: 320, height: 720 })
    await expectNoOverflow(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    const spacing = await page.addStyleTag({ content: '* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } p { margin-bottom: 2em !important; }' })
    await expectNoOverflow(page)
    await spacing.evaluate((element) => element.remove())
    const textResize = await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })
    await expectNoOverflow(page)
    await textResize.evaluate((element) => element.remove())
    await page.setViewportSize({ width: 568, height: 320 })
    await page.goto('/activity')
    const keyboardInput = page.getByLabel('Activity title')
    await keyboardInput.focus()
    await keyboardInput.scrollIntoViewIfNeeded()
    await expect(keyboardInput).toBeInViewport()
    await expectNoOverflow(page)
  }
})

test('Checkpoint 6 exercises the critical control loop at all six target viewports', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'product-desktop-chromium', 'The bounded viewport matrix runs once in primary Chromium.')
  test.setTimeout(240_000)
  await login(page)
  const viewports = Object.entries(manifest.viewports)
  expect(viewports).toHaveLength(6)

  for (const [name, viewport] of viewports) {
    await page.setViewportSize(viewport)
    await page.goto('/insights/analysis')
    await page.getByRole('button', { name: 'Generate current Analysis' }).click()
    await expect(page.getByText('A current deterministic Analysis snapshot was generated explicitly.')).toBeVisible()
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()
    await page.getByRole('button', { name: 'Regenerate explicitly' }).click()
    await expect(page.getByText('Today was explicitly regenerated. Expired or omitted items created no debt.')).toBeVisible()
    await page.getByRole('link', { name: 'Do something else' }).first().click()
    await expect(page.getByRole('heading', { name: 'Continue: Choose actual work' })).toBeVisible()
    const title = page.getByLabel('Activity title')
    const create = page.getByRole('button', { name: 'Create and use Activity' })
    await expectControlInViewport(page, title)
    await expectControlInViewport(page, create)
    await title.fill(`Six-viewport Session ${name}`)
    await create.click()
    await expect(page.getByText('Activity selected. No suggestion or canonical reference has been changed yet.')).toBeVisible()
    await page.getByRole('button', { name: 'Start timer' }).click()
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
    await page.getByRole('button', { name: 'Resume' }).click()
    await page.getByRole('button', { name: 'Complete' }).click()
    await expect(page.getByText('Timer completed.')).toBeVisible()
    await page.getByRole('button', { name: 'Use Activity', exact: true }).click()
    await page.getByRole('link', { name: 'Return selected Activity' }).click()
    await expect(page.getByRole('heading', { name: /Confirm replacement for/ })).toBeVisible()
    await page.getByRole('button', { name: 'Confirm replacement' }).click()
    await expect(page.getByText(/Today status updated: replace/)).toBeVisible()
    await expectNoOverflow(page)
    await expect(page.getByRole('heading', { name: 'Today', level: 1 }), name).toBeVisible()
  }
})

test('Checkpoint 6 smoke-opens every Final Visual Product QA manifest entry without taking screenshots', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'product-desktop-chromium', 'The manifest is verified once in the primary production browser.')
  test.setTimeout(150_000)
  await login(page)
  expect(manifest.scenarioId).toBe('roadmap-25')
  expect(manifest.setupProfile).toBe('roadmap-25')
  const tokens = await manifestRouteTokens(page)
  const results: { id: string; route: string; viewport: string; ready: boolean; assertions: number }[] = []

  for (const entry of manifest.entries) {
    const viewport = manifest.viewports[entry.viewport as keyof typeof manifest.viewports]
    expect(viewport).toBeDefined()
    expect(entry.setupSteps).toEqual([{ kind: 'authenticatedFixture' }])
    expect(entry.interactionSteps.length).toBeGreaterThan(0)
    expect(entry.expected.assertions.length).toBeGreaterThan(0)
    await page.setViewportSize(viewport)
    const route = resolveManifestRoute(entry.route, tokens)
    await page.goto(route)
    await expect(page.locator('#main-content')).toBeVisible()
    for (const step of entry.interactionSteps) {
      if (step.kind === 'waitForText') await expect(page.getByText(step.value, { exact: false }).first()).toBeVisible()
    }
    if (entry.ready.kind === 'roadmap') {
      await expect(page.locator('[data-roadmap-ready="true"]')).toBeVisible()
    } else if (entry.ready.kind === 'selector') {
      await expect(page.locator(entry.ready.selector!)).toBeVisible()
    } else {
      await expect(page.getByRole('heading', { name: entry.ready.name, level: 1 })).toBeVisible()
    }
    for (const assertion of entry.expected.assertions) {
      if (assertion.kind === 'text') await expect(page.getByText(assertion.value, { exact: false }).first()).toBeVisible()
      if (assertion.kind === 'selector') await expect(page.locator(assertion.value)).toBeVisible()
      if (assertion.kind === 'urlPattern') await expect(page).toHaveURL(new RegExp(assertion.value))
    }
    await expectNoOverflow(page)
    results.push({ id: entry.id, route, viewport: entry.viewport, ready: true, assertions: entry.expected.assertions.length })
  }

  await testInfo.attach('visual-product-qa-manifest-results.json', {
    body: JSON.stringify({ campaignStatus: manifest.status, screenshotCampaignExecuted: false, results }, null, 2),
    contentType: 'application/json',
  })
  expect(results).toHaveLength(manifest.entries.length)
})
