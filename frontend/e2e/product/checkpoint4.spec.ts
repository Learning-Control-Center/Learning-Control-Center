import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test.skip(process.env.LCC_PRODUCT_SCENARIO !== 'roadmap-25', 'Checkpoint 4 uses the representative populated fixture.')

async function login(page: import('@playwright/test').Page) {
  await page.goto('/profile')
  await page.getByLabel('Username').fill('fixture-learner')
  await page.getByLabel('Password').fill('fixture-password-with-enough-entropy')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Profile', level: 1 })).toBeVisible()
}

async function expectAccessible(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(result.violations).toEqual([])
}

test('Checkpoint 4 connects Profile, Learn, Projects, and explicit Activity handoff', async ({ page }) => {
  test.setTimeout(90_000)
  await login(page)
  await expect(page.getByText('First domain ready')).toBeVisible()
  await expect(page.getByText('First domain readiness')).toBeVisible()
  const curriculumTarget = page.getByRole('article').filter({ hasText: 'Roadmap capability 5 with a representative long title' })
  await curriculumTarget.getByRole('link', { name: /View competency/ }).first().click()
  await expect(page.getByRole('heading', { name: /Roadmap capability 5/, level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Related learning and outcome work' })).toBeVisible()
  await expect(page.getByRole('link', { name: /Practice the next Roadmap capability/ })).toBeVisible()
  await expectAccessible(page)

  await page.getByRole('link', { name: /Practice the next Roadmap capability/ }).click()
  await expect(page.getByRole('heading', { name: 'Roadmap journey actions 25', level: 1 })).toBeVisible()
  await expect(page.getByText(/does not infer a best, next, or recommended action/)).toBeVisible()
  await expectAccessible(page)
  await page.getByText('Curriculum version management').click()
  const reviewActivation = page.getByRole('button', { name: 'Review activation' })
  await reviewActivation.click()
  const activationDialog = page.getByRole('dialog', { name: 'Confirm Curriculum activation' })
  await expect(activationDialog).toBeVisible()
  await expect(activationDialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await expectAccessible(page)
  await page.keyboard.press('Escape')
  await expect(activationDialog).toBeHidden()
  await expect(reviewActivation).toBeFocused()

  await page.getByRole('link', { name: 'Start or log actual work' }).click()
  await expect(page.getByRole('heading', { name: /Continue: Practice the next Roadmap capability/ })).toBeVisible()
  await expectAccessible(page)

  const mutations: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET') mutations.push(`${request.method()} ${new URL(request.url()).pathname}`)
  })
  await page.reload()
  await expect(page.getByRole('heading', { name: /Continue: Practice the next Roadmap capability/ })).toBeVisible()
  expect(mutations).toEqual([])
  await page.getByRole('button', { name: 'Create and relate Activity' }).click()
  await expect(page.getByText(/Relationship recorded/)).toBeVisible()
  expect(mutations).toContain('POST /api/v2/activities')
  expect(mutations).toContain('POST /api/v2/curricula/activity-links')

  await page.goto('/projects')
  await expect(page.getByRole('heading', { name: 'Projects', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Evidence-backed criteria' })).toBeVisible()
  await expect(page.getByText(/Evidence record/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Start or log actual work' })).toHaveAttribute('href', /origin=projects/)
  await expectAccessible(page)
})
