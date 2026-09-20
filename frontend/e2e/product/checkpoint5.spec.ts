import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test.skip(process.env.LCC_PRODUCT_SCENARIO !== 'roadmap-25', 'Checkpoint 5 uses the representative populated fixture.')

async function expectAccessible(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(result.violations).toEqual([])
}

async function login(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByLabel('Username').fill('fixture-learner')
  await page.getByLabel('Password').fill('fixture-password-with-enough-entropy')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()
}

async function noDebtSnapshot(page: import('@playwright/test').Page) {
  const [evidenceResponse, sessionsResponse, analyticsResponse] = await Promise.all([
    page.request.get('/api/v2/evidence?limit=200'),
    page.request.get('/api/v1/sessions?limit=100'),
    page.request.get('/api/v1/analytics?range=30d'),
  ])
  expect(evidenceResponse.ok()).toBe(true)
  expect(sessionsResponse.ok()).toBe(true)
  expect(analyticsResponse.ok()).toBe(true)
  const analytics = await analyticsResponse.json() as Record<string, unknown>
  delete analytics.generatedAt
  return {
    evidence: await evidenceResponse.json(),
    sessions: await sessionsResponse.json(),
    analytics,
  }
}

async function csrfToken(page: import('@playwright/test').Page) {
  const sessionResponse = await page.request.get('/api/v1/auth/session')
  expect(sessionResponse.ok()).toBe(true)
  const session = await sessionResponse.json() as { csrf_token: string }
  return session.csrf_token
}

async function ensureActiveSessionFixture(page: import('@playwright/test').Page) {
  const current = await page.request.get('/api/v1/sessions/active')
  expect(current.ok()).toBe(true)
  const state = await current.json() as { active: boolean; session: { id: string } | null }
  const token = await csrfToken(page)
  if (state.session) {
    const cancel = await page.request.post(`/api/v2/sessions/${state.session.id}/cancel`, { headers: { 'X-CSRF-Token': token } })
    expect(cancel.ok()).toBe(true)
  }
  const activityResponse = await page.request.post('/api/v2/activities', {
    data: { title: `Mobile Session fixture ${crypto.randomUUID()}`, category_stable_key: 'practice' },
    headers: { 'X-CSRF-Token': token },
  })
  expect(activityResponse.status()).toBe(201)
  const activity = await activityResponse.json() as { id: string }
  const sessionResponse = await page.request.post('/api/v2/sessions/timed', {
    data: { activity_id: activity.id, assistance_mode: 'none', contributions: [] },
    headers: { 'X-CSRF-Token': token },
  })
  expect(sessionResponse.status()).toBe(201)
  return ((await sessionResponse.json()) as { id: string }).id
}

async function cancelActiveSessionFixture(page: import('@playwright/test').Page, sessionId: string) {
  const token = await csrfToken(page)
  const response = await page.request.post(`/api/v2/sessions/${sessionId}/cancel`, { headers: { 'X-CSRF-Token': token } })
  expect(response.ok()).toBe(true)
}

test('Checkpoint 5 completes Today, Activity, Analysis, Recommendation, and legacy Insights', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await login(page)
  await expect(page.getByText('Primary action').first()).toBeVisible()
  await expectAccessible(page)

  if (testInfo.project.name === 'product-desktop-chromium') {
    const beforeSkip = await noDebtSnapshot(page)
    await page.getByRole('button', { name: 'Skip without debt' }).first().click()
    await expect(page.getByText('Suggestion skipped. No debt or completion was created.')).toBeVisible()
    expect(await noDebtSnapshot(page)).toEqual(beforeSkip)
    await page.getByRole('button', { name: 'Regenerate explicitly' }).click()
    await expect(page.getByText('Today was explicitly regenerated. Expired or omitted items created no debt.')).toBeVisible()

    const beforeReplacement = await noDebtSnapshot(page)
    const actualWorkLink = page.getByRole('link', { name: 'Do something else' }).first()
    await expect(actualWorkLink).toHaveAttribute('href', /kind=unlinked/)
    await actualWorkLink.click()
    await expect(page.getByRole('heading', { name: 'Continue: Choose actual work' })).toBeVisible()
    await expectAccessible(page)
    await page.getByLabel('Activity title').fill('Investigate a real production issue')
    await page.getByRole('button', { name: 'Create and use Activity' }).click()
    await expect(page.getByText(/No suggestion or canonical reference has been changed yet/)).toBeVisible()
    await page.getByRole('link', { name: 'Return selected Activity' }).click()
    await expect(page.getByRole('heading', { name: /Confirm replacement for/ })).toBeVisible()
    await page.getByRole('button', { name: 'Confirm replacement' }).click()
    await expect(page.getByText(/Today status updated: replace/)).toBeVisible()
    expect(await noDebtSnapshot(page)).toEqual(beforeReplacement)
    await page.getByRole('button', { name: 'Retract relation' }).click()
    await expect(page.getByText('The Activity relation was retracted without rewriting history.')).toBeVisible()
    expect(await noDebtSnapshot(page)).toEqual(beforeReplacement)

    await page.goto('/insights/analysis')
    await page.getByRole('button', { name: 'Generate current Analysis' }).click()
    await expect(page.getByText('A current deterministic Analysis snapshot was generated explicitly.')).toBeVisible()
    await page.goto('/')
    await page.getByRole('button', { name: 'Regenerate explicitly' }).click()
    await expect(page.getByText('Today was explicitly regenerated. Expired or omitted items created no debt.')).toBeVisible()
    const evidenceBeforeWork = (await noDebtSnapshot(page)).evidence as { items: unknown[] }
    await page.getByRole('button', { name: 'Start actual work' }).first().click()
    await expect(page.getByText('Today status updated: start.')).toBeVisible()
    await page.getByRole('link', { name: 'Open and continue Session' }).first().click()
    await expect(page.getByRole('heading', { name: 'Activity', level: 1 })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(page.getByText('Session paused').first()).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
    await page.getByRole('button', { name: 'Resume' }).click()
    await expect(page.getByText('Session running').first()).toBeVisible()
    await page.getByRole('button', { name: 'Complete' }).click()
    await expect(page.getByText('Timer completed.')).toBeVisible()
    await page.goto('/')
    await page.getByRole('button', { name: 'Complete from finalized Session' }).first().click()
    await expect(page.getByText('Today status updated: completed.')).toBeVisible()
    const evidenceAfterWork = (await noDebtSnapshot(page)).evidence as { items: unknown[] }
    expect(evidenceAfterWork.items.length).toBeGreaterThan(evidenceBeforeWork.items.length)
    await page.goto('/insights/analysis')
    await page.getByRole('button', { name: 'Generate current Analysis' }).click()
    await expect(page.getByText('A current deterministic Analysis snapshot was generated explicitly.')).toBeVisible()
    const currentAnalysisResponse = await page.request.get('/api/v2/analysis/current')
    expect(currentAnalysisResponse.ok()).toBe(true)
    const currentAnalysis = await currentAnalysisResponse.json() as { snapshot: { id: string } }
    await page.locator('section').filter({ has: page.getByRole('heading', { name: 'Competency gaps' }) }).getByRole('link').first().click()
    await expect(page.getByRole('heading', { name: 'Evidence history' })).toBeVisible()
    await page.getByRole('link', { name: 'Roadmap', exact: true }).first().click()
    await expect(page.getByRole('heading', { name: 'Roadmap', level: 1 })).toBeVisible()
    await page.getByRole('link', { name: 'Insights', exact: true }).first().click()
    await page.getByRole('link', { name: 'Recommendation V2', exact: true }).click()
    await page.getByRole('button', { name: 'Generate new run' }).click()
    await expect(page.getByText('A new deterministic Recommendation run was generated explicitly.')).toBeVisible()
    await page.getByText('Policy and run lineage').click()
    await expect(page.getByText(`Analysis snapshot: ${currentAnalysis.snapshot.id}`)).toBeVisible()
  }

  if (testInfo.project.name.includes('mobile')) {
    const activeSessionId = await ensureActiveSessionFixture(page)
    await page.reload()
    await expect(page.locator('header').getByText('Session running')).toBeVisible()
    await cancelActiveSessionFixture(page, activeSessionId)
  }

  await page.goto('/insights/analysis')
  await expect(page.getByRole('heading', { name: 'Analysis', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Diagnostic signals' })).toBeVisible()
  await expectAccessible(page)

  await page.goto('/insights/recommendations')
  await expect(page.getByRole('heading', { name: 'Recommendations', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Selected work' })).toBeVisible()
  await page.getByText(/Full candidate audit/).click()
  await expect(page.getByText(/not eligible|ineligible|rejected/i).first()).toBeVisible()
  await expectAccessible(page)

  await page.goto('/insights/legacy/analytics')
  await expect(page.getByRole('heading', { name: 'V1 Compatibility Analytics', level: 1 })).toBeVisible()
  await expect(page.getByText(/Recalculated on request/)).toBeVisible()
  await page.getByText('Exact values table', { exact: true }).click()
  await expect(page.getByRole('table', { name: /Exact V1 compatibility Analytics values/ })).toBeVisible()
  await expectAccessible(page)

  await page.goto('/insights/legacy/reports')
  await expect(page.getByRole('heading', { name: 'Generated V1 Reports', level: 1 })).toBeVisible()
  await expect(page.getByText(/Immutable snapshots/)).toBeVisible()
  await expect(page.getByRole('heading', { name: /learning report/i, level: 2 })).toBeVisible()
  await expect(page.getByLabel('Generated report content')).toBeVisible()
  await expect(page.getByRole('textbox')).toHaveCount(0)
  await expectAccessible(page)

})

test('Checkpoint 5 keyboard-only mobile control loop', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'product-mobile-chromium', 'One representative mobile project proves keyboard operation.')
  await login(page)
  await ensureActiveSessionFixture(page)
  await page.reload()
  await page.getByRole('button', { name: 'Open navigation' }).focus()
  await page.keyboard.press('Enter')
  const activityLink = page.getByRole('dialog').getByRole('link', { name: 'Activity', exact: true })
  await activityLink.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Activity', level: 1 })).toBeVisible()
  const pause = page.getByRole('button', { name: 'Pause' })
  await pause.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('header').getByText('Session paused')).toBeVisible()
  const resume = page.getByRole('button', { name: 'Resume' })
  await resume.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('header').getByText('Session running')).toBeVisible()
  const complete = page.getByRole('button', { name: 'Complete' })
  await complete.focus(); await page.keyboard.press('Enter')
  await expect(page.getByText('Timer completed.')).toBeVisible()
  await page.getByRole('button', { name: 'Open navigation' }).focus()
  await page.keyboard.press('Enter')
  const todayLink = page.getByRole('dialog').getByRole('link', { name: 'Today', exact: true })
  await todayLink.focus(); await page.keyboard.press('Enter')
  const skip = page.getByRole('button', { name: 'Skip without debt' }).first()
  await skip.focus(); await page.keyboard.press('Enter')
  await expect(page.getByText('Suggestion skipped. No debt or completion was created.')).toBeVisible()
})
