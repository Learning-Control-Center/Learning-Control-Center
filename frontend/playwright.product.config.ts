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
      name: 'product-wide-desktop-chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: 1920, height: 1080 },
      },
    },
    ...[
      ['product-compact-landscape-chromium', 1024, 768],
      ['product-tablet-portrait-chromium', 768, 1024],
      ['product-mobile-chromium', 390, 844],
      ['product-minimum-mobile-chromium', 320, 568],
      ['product-narrow-landscape-chromium', 568, 320],
    ].map(([name, width, height]) => ({
      name: String(name),
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: Number(width), height: Number(height) },
        hasTouch: true,
      },
    })),
  ],
})
