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

const appFeatureEntries = new Set([
  'features/activity/index.ts',
  'features/data-transfer/index.ts',
  'features/insights/analysis.ts',
  'features/insights/landing.ts',
  'features/insights/recommendations.ts',
  'features/learn/index.ts',
  'features/legacy-history/analytics.ts',
  'features/legacy-history/reports.ts',
  'features/legacy-history/roadmap.ts',
  'features/legacy-history/today.ts',
  'features/profile/index.ts',
  'features/projects/index.ts',
  'features/roadmap/legacy.ts',
  'features/roadmap/v2.ts',
  'features/settings/index.ts',
  'features/today/legacy.ts',
  'features/today/v2.ts',
])

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
          const publicFeatureEntry = targetArea === 'features' && targetParts.length === 3 && targetParts[2] === 'index.ts'
          if (targetArea === 'app' || (targetArea === 'features' && targetParts[1] !== sourceFeature && !publicFeatureEntry)) {
            violations.push(`${sourceRelative} -> ${targetRelative}`)
          }
        }
        if (sourceArea === 'app' && targetArea === 'features' && !appFeatureEntries.has(targetRelative)) {
          violations.push(`${sourceRelative} -> ${targetRelative} (feature internals)`)
        }
      }
    }
    expect(violations).toEqual([])
  })
})
