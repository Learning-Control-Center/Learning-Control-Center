import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

const expectedAuthority = process.env.LCC_PRODUCT_SCENARIO === 'legacy-shell' ? 'V1 compatibility' : 'V2'

test.skip(process.env.LCC_ENVIRONMENT === 'production', 'The shell fixture uses isolated product-harness credentials.')

test('isolated fixture exposes an accessible, reduced-motion product shell without overflow', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('fixture-learner')
  await page.getByLabel('Password').fill('fixture-password-with-enough-entropy')
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.locator('#main-content')).toBeVisible()
  const skipLink = page.getByRole('link', { name: 'Skip to main content' })
  await page.keyboard.press('Home')
  await page.keyboard.press('Tab')
  await expect(skipLink).toBeFocused()
  await skipLink.press('Enter')
  await expect(page.locator('#main-content')).toBeFocused()

  expect(
    await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches),
  ).toBe(true)
  await expect(page.locator('[data-reduced-motion]')).toHaveAttribute('data-reduced-motion', 'true')
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true)
  await expect(page.getByRole('navigation', { name: 'Product navigation' })).toHaveCount(
    test.info().project.name.includes('mobile') ? 0 : 1,
  )

  if (test.info().project.name.includes('mobile')) {
    await page.getByRole('button', { name: 'Open navigation' }).click()
    const dialog = page.getByRole('dialog', { name: 'Application navigation' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(`Center / ${expectedAuthority}`, { exact: true })).toBeVisible()
    await expect(dialog.getByRole('navigation', { name: 'Product navigation' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Open navigation' })).toBeFocused()
    await page.getByRole('button', { name: 'Open navigation' }).click()
    await page.getByRole('dialog', { name: 'Application navigation' }).getByRole('link', { name: 'Insights' }).click()
  } else {
    await expect(page.getByText(`Center / ${expectedAuthority}`, { exact: true })).toBeVisible()
    await page.getByRole('navigation', { name: 'Product navigation' }).getByRole('link', { name: 'Insights' }).click()
  }

  const destinationHeading = page.getByRole('heading', { name: 'Insights', exact: true }).first()
  await expect(destinationHeading).toBeFocused()
  await expect(page.getByRole('status')).toHaveText('Insights page')
  const focusBounds = await destinationHeading.boundingBox()
  const viewport = page.viewportSize()
  expect(focusBounds).not.toBeNull()
  expect(viewport).not.toBeNull()
  expect(focusBounds!.y).toBeGreaterThanOrEqual(0)
  expect(focusBounds!.y + focusBounds!.height).toBeLessThanOrEqual(viewport!.height)

  const accessibility = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(accessibility.violations).toEqual([])
})
