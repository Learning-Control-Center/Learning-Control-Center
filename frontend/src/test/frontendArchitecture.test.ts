import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, extname, join, normalize, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const v2Pages = [
  '../pages/ProfileCapabilityPage.tsx',
  '../pages/CurriculumPage.tsx',
  '../pages/ProjectsPage.tsx',
  '../pages/RoadmapV2Page.tsx',
  '../pages/AnalysisPage.tsx',
  '../pages/RecommendationsV2Page.tsx',
  '../pages/TodayV2Page.tsx',
]

describe('Checkpoint 1 frontend architecture contracts', () => {
  it.each(v2Pages)('%s does not use the removed panel styling contract', (relativePath) => {
    const source = readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')
    expect(source).not.toMatch(/className\s*=\s*["'`]([^"'`]*\s)?panel(\s|["'`])/)
  })

  it('enforces shared, feature, and app dependency directions using resolved paths', () => {
    const srcRoot = resolve(process.cwd(), 'src')
    const sourceFiles = readdirSync(srcRoot, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile() && ['.ts', '.tsx'].includes(extname(entry.name)))
      .map((entry) => join(entry.parentPath, entry.name))

    const violations: string[] = []
    const importPattern = /(?:from\s+|import\s*\()\s*['"]([^'"]+)['"]/g
    for (const source of sourceFiles) {
      const sourceRelative = normalize(relative(srcRoot, source))
      const sourceArea = sourceRelative.split('/')[0]
      if (!['app', 'features', 'shared'].includes(sourceArea)) continue
      for (const match of readFileSync(source, 'utf8').matchAll(importPattern)) {
        const specifier = match[1]
        if (!specifier.startsWith('.')) continue
        const unresolved = resolve(dirname(source), specifier)
        const target = ['.ts', '.tsx', '/index.ts', '/index.tsx']
          .map((suffix) => `${unresolved}${suffix}`)
          .find(existsSync)
        if (!target) continue
        const targetRelative = normalize(relative(srcRoot, target))
        const targetParts = targetRelative.split('/')
        const targetArea = targetParts[0]

        if (sourceArea === 'shared' && ['app', 'features'].includes(targetArea)) {
          violations.push(`${sourceRelative} -> ${targetRelative}`)
        }
        if (sourceArea === 'features') {
          const sourceFeature = sourceRelative.split('/')[1]
          if (targetArea === 'app' || (targetArea === 'features' && targetParts[1] !== sourceFeature)) {
            violations.push(`${sourceRelative} -> ${targetRelative}`)
          }
        }
        if (sourceArea === 'app' && targetArea === 'features' && targetParts.length > 3) {
          violations.push(`${sourceRelative} -> ${targetRelative} (feature internals)`)
        }
      }
    }
    expect(violations).toEqual([])
  })
})
