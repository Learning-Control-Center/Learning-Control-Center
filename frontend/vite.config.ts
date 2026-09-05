import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

const formatGuideRoute = '/IMPORT_EXPORT_FORMAT.md'
const formatGuidePath = fileURLToPath(
  new URL('../docs/IMPORT_EXPORT_FORMAT.md', import.meta.url),
)

function formatGuidePlugin(): Plugin {
  return {
    name: 'lcc-format-guide',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (
          request.url !== formatGuideRoute ||
          (request.method !== 'GET' && request.method !== 'HEAD')
        ) {
          next()
          return
        }
        response.setHeader('Content-Type', 'text/markdown; charset=utf-8')
        response.setHeader(
          'Content-Disposition',
          'attachment; filename="IMPORT_EXPORT_FORMAT.md"',
        )
        response.end(request.method === 'HEAD' ? undefined : readFileSync(formatGuidePath))
      })
    },
    buildStart() {
      this.addWatchFile(formatGuidePath)
    },
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'IMPORT_EXPORT_FORMAT.md',
        source: readFileSync(formatGuidePath),
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), formatGuidePlugin()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
