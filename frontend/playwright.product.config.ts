import { defineConfig, devices } from '@playwright/test'

if (!process.env.LCC_PRODUCT_BASE_URL) {
  throw new Error('LCC_PRODUCT_BASE_URL is required for the isolated product fixture project.')
}

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
  projects: [
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
        viewport: { width: 320, height: 568 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
})
