import { describe, expect, it } from 'vitest'

import { productNavigation, utilityNavigation } from '../layout/navigation'
import {
  breadcrumbsForPath,
  compatibilityAliases,
  metadataForPath,
  paths,
  primaryRoutes,
  routeMetadata,
  utilityRoutes,
} from './routes'

describe('canonical route metadata', () => {
  it('declares the seven product destinations and two separate utility destinations', () => {
    expect(primaryRoutes.map(({ path, label }) => ({ path, label }))).toEqual([
      { path: '/', label: 'Today' },
      { path: '/roadmap', label: 'Roadmap' },
      { path: '/profile', label: 'Profile' },
      { path: '/learn', label: 'Learn' },
      { path: '/projects', label: 'Projects' },
      { path: '/activity', label: 'Activity' },
      { path: '/insights', label: 'Insights' },
    ])
    expect(utilityRoutes.map(({ path, label }) => ({ path, label }))).toEqual([
      { path: '/data-transfer', label: 'Data transfer' },
      { path: '/settings', label: 'Settings' },
    ])
    expect(productNavigation).toEqual(primaryRoutes)
    expect(utilityNavigation).toEqual(utilityRoutes)
  })

  it('gives every route a non-empty title, authority classification, and navigation group', () => {
    expect(new Set(routeMetadata.map((route) => route.path)).size).toBe(routeMetadata.length)
    for (const route of routeMetadata) {
      expect(route.label.trim()).not.toBe('')
      expect(route.title.trim()).not.toBe('')
      expect(['primary', 'utility', 'insights', 'hidden']).toContain(route.group)
      expect(['all', 'legacy_v1', 'v2', 'history']).toContain(route.authority)
      expect(route.focusTarget).toBe('[data-route-focus], #main-content h1')
    }
  })

  it('declares every approved compatibility alias against a canonical route', () => {
    expect(compatibilityAliases).toEqual([
      { from: '/today-v2', to: '/' },
      { from: '/roadmap-v2', to: '/roadmap' },
      { from: '/recommendations-v2', to: '/insights/recommendations' },
      { from: '/recommendations', to: '/insights/recommendations' },
      { from: '/sessions', to: '/activity' },
      { from: '/curriculum', to: '/learn' },
      { from: '/analysis', to: '/insights/analysis' },
      { from: '/analytics', to: '/insights/legacy/analytics' },
      { from: '/reports', to: '/insights/legacy/reports' },
      { from: '/transfer', to: '/data-transfer' },
      { from: '/legacy-today', to: '/insights/legacy/today' },
      { from: '/legacy-roadmap', to: '/insights/legacy/roadmap' },
    ])
    const canonicalStaticPaths = new Set(
      routeMetadata.map(({ path }) => path).filter((path) => !path.includes(':')),
    )
    expect(compatibilityAliases.every(({ to }) => canonicalStaticPaths.has(to))).toBe(true)
  })

  it('builds encoded detail paths and resolves their metadata and breadcrumbs', () => {
    expect(paths.competency('identity / one')).toBe('/profile/competencies/identity%20%2F%20one')
    expect(paths.curriculum('curriculum / one')).toBe('/learn/curricula/curriculum%20%2F%20one')
    expect(paths.project('project / one')).toBe('/projects/project%20%2F%20one')

    expect(metadataForPath('/profile/competencies/identity-1')).toMatchObject({
      label: 'Competency',
      parent: '/profile',
      authority: 'v2',
    })
    expect(breadcrumbsForPath('/profile/competencies/identity-1').map(({ label }) => label)).toEqual([
      'Profile',
      'Competency',
    ])
    expect(breadcrumbsForPath('/insights/legacy/reports').map(({ label }) => label)).toEqual([
      'Insights',
      'Legacy V1 history',
      'Generated reports',
    ])
  })

  it('fails unknown paths closed to Today metadata', () => {
    expect(metadataForPath('/not-a-product-route')).toEqual(routeMetadata[0])
  })
})
