import { expect, request, test } from '@playwright/test'

const originalPassword = 'correct horse battery staple'
const replacementPassword = 'replacement horse battery staple'

test('production authentication and forced-logout flow', async ({ browser, page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Create the first account' })).toBeVisible()
  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(originalPassword)
  await page.getByLabel('Bootstrap secret').fill('production-bootstrap-token-for-browser-test')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible()
  const sessionCookie = (await page.context().cookies()).find((item) => item.name === 'lcc_session')
  expect(sessionCookie?.secure).toBe(true)
  expect(sessionCookie?.httpOnly).toBe(true)
  expect(sessionCookie?.sameSite).toBe('Lax')

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Current password').fill(originalPassword)
  await page.getByLabel('New password', { exact: true }).fill(replacementPassword)
  await page.getByLabel('Confirm new password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  await expect(page.getByText('lcc-ops recover-password')).toBeVisible()

  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Sign in' }).click()

  const otherContext = await browser.newContext({ ignoreHTTPSErrors: true })
  const otherPage = await otherContext.newPage()
  await otherPage.goto(process.env.LCC_E2E_BASE_URL ?? 'https://localhost:8443')
  await otherPage.getByLabel('Username').fill('learner')
  await otherPage.getByLabel('Password').fill(replacementPassword)
  await otherPage.getByRole('button', { name: 'Sign in' }).click()
  await expect(otherPage.getByRole('heading', { name: 'Today' })).toBeVisible()

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByRole('button', { name: 'Revoke other sessions' }).click()
  await expect(page.getByText('All other sessions were revoked.')).toBeVisible()
  await otherPage.reload()
  await expect(otherPage.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  await otherContext.close()
  await page.getByRole('button', { name: 'Revoke all sessions' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
})

test('production timer survives refresh and completes', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: 'Sessions' }).click()
  await expect(page.getByRole('heading', { name: 'Log & sessions' })).toBeVisible()
  await page.getByRole('button', { name: 'Start timer' }).click()
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
  await page.getByRole('button', { name: 'Resume' }).click()
  await expect(page.getByRole('button', { name: 'Complete' })).toBeVisible()
  await page.getByRole('button', { name: 'Complete' }).click()
  await expect(page.getByText('Unlinked learning session')).toBeVisible()
})

test('production host, origin, and docs boundaries fail closed', async () => {
  const baseURL = process.env.LCC_E2E_BASE_URL ?? 'https://localhost:8443'
  const api = await request.newContext({ baseURL, ignoreHTTPSErrors: true })
  expect((await api.get('/api/v1/docs')).status()).toBe(404)
  expect(
    (
      await api.post('/api/v1/auth/login', {
        data: { username: 'learner', password: replacementPassword },
      })
    ).status(),
  ).toBe(403)
  const backend = await request.newContext({ baseURL: 'http://127.0.0.1:8000' })
  expect((await backend.get('/api/v1/health', { headers: { Host: 'attacker.example' } })).status()).toBe(
    400,
  )
  await backend.dispose()
  await api.dispose()
})
