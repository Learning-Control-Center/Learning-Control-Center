import { defineConfig, devices } from '@playwright/test'

if (!process.env.LCC_PRODUCT_BASE_URL) {
  throw new Error('LCC_PRODUCT_BASE_URL is required for the isolated product fixture project.')
}

const webkitLaunchEnvironment = process.env.LCC_WEBKIT_LIBRARY_PATH
  ? Object.fromEntries(Object.entries({ ...process.env, LD_LIBRARY_PATH: process.env.LCC_WEBKIT_LIBRARY_PATH }).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
  : undefined

export default defineConfig({
  testDir: './e2e/product',
  outputDir: process.env.LCC_PRODUCT_ARTIFACT_DIR ?? './test-results/product',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: process.env.LCC_PRODUCT_BASE_URL,
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: (process.env.LCC_PRODUCT_SCENARIO === 'roadmap-100' || process.env.LCC_PRODUCT_SCENARIO === 'roadmap-250') ? [
    {
      name: 'product-desktop-chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: 1440, height: 900 },
      },
    },
  ] : [
    {
      name: 'product-desktop-chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'product-mobile-chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: 390, height: 844 },
        hasTouch: true,
      },
    },
    {
      name: 'product-mobile-landscape-chromium',
      grep: /@cross-browser/,
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: 568, height: 320 },
        hasTouch: true,
      },
    },
    {
      name: 'product-firefox',
      grep: /@cross-browser/,
      use: {
        ...devices['Desktop Firefox'],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'product-webkit',
      grep: /@cross-browser/,
      use: {
        ...devices['Desktop Safari'],
        viewport: { width: 1440, height: 900 },
        launchOptions: webkitLaunchEnvironment ? { env: webkitLaunchEnvironment } : undefined,
      },
    },
  ],
})
