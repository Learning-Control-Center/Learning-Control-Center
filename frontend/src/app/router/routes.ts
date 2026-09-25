export type NavigationGroup = 'primary' | 'utility' | 'insights' | 'hidden'

import { paths } from '../../shared/navigation/paths'

export { paths } from '../../shared/navigation/paths'

export type RouteMetadata = {
  path: string
  label: string
  title: string
  group: NavigationGroup
  parent?: string
  authority: 'all' | 'legacy_v1' | 'v2' | 'history'
  focusTarget: '[data-route-focus], #main-content h1'
}

export const routeMetadata: readonly RouteMetadata[] = [
  { path: paths.today, label: 'Today', title: 'Today', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.roadmap, label: 'Roadmap', title: 'Roadmap', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.profile, label: 'Profile', title: 'Profile', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: '/profile/competencies/:competencyIdentityId', label: 'Competency', title: 'Competency detail', group: 'hidden', parent: paths.profile, authority: 'v2', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.learn, label: 'Learn', title: 'Learn', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: '/learn/curricula/:curriculumId', label: 'Curriculum', title: 'Curriculum detail', group: 'hidden', parent: paths.learn, authority: 'v2', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.projects, label: 'Projects', title: 'Projects', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: '/projects/:projectId', label: 'Project', title: 'Project detail', group: 'hidden', parent: paths.projects, authority: 'v2', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.activity, label: 'Activity', title: 'Activity', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: '/assessments/:executionId', label: 'Assessment execution', title: 'Assessment execution', group: 'hidden', parent: paths.activity, authority: 'v2', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.insights, label: 'Insights', title: 'Insights', group: 'primary', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.analysis, label: 'Current analysis', title: 'Current analysis', group: 'insights', parent: paths.insights, authority: 'v2', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.recommendations, label: 'Recommendation history', title: 'Recommendation history', group: 'insights', parent: paths.insights, authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.legacy, label: 'Legacy V1 history', title: 'Legacy V1 history', group: 'insights', parent: paths.insights, authority: 'history', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.legacyToday, label: 'Today history', title: 'Legacy Today history', group: 'hidden', parent: paths.legacy, authority: 'history', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.legacyRoadmap, label: 'Roadmap history', title: 'Legacy Roadmap history', group: 'hidden', parent: paths.legacy, authority: 'history', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.legacyAnalytics, label: 'V1 compatibility analytics', title: 'V1 compatibility analytics', group: 'hidden', parent: paths.legacy, authority: 'history', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.legacyReports, label: 'Generated reports', title: 'Generated reports', group: 'hidden', parent: paths.legacy, authority: 'history', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.dataTransfer, label: 'Data transfer', title: 'Data transfer', group: 'utility', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
  { path: paths.settings, label: 'Settings', title: 'Settings', group: 'utility', authority: 'all', focusTarget: '[data-route-focus], #main-content h1' },
] as const

export const compatibilityAliases = [
  { from: '/today-v2', to: paths.today },
  { from: '/roadmap-v2', to: paths.roadmap },
  { from: '/recommendations-v2', to: paths.recommendations },
  { from: '/recommendations', to: paths.recommendations },
  { from: '/sessions', to: paths.activity },
  { from: '/curriculum', to: paths.learn },
  { from: '/analysis', to: paths.analysis },
  { from: '/analytics', to: paths.legacyAnalytics },
  { from: '/reports', to: paths.legacyReports },
  { from: '/transfer', to: paths.dataTransfer },
  { from: '/legacy-today', to: paths.legacyToday },
  { from: '/legacy-roadmap', to: paths.legacyRoadmap },
] as const

export const primaryRoutes = routeMetadata.filter((route) => route.group === 'primary')
export const utilityRoutes = routeMetadata.filter((route) => route.group === 'utility')

function pathMatches(pattern: string, pathname: string) {
  if (pattern === '/') return pathname === '/'
  const patternParts = pattern.split('/').filter(Boolean)
  const pathParts = pathname.split('/').filter(Boolean)
  return patternParts.length === pathParts.length && patternParts.every((part, index) => part.startsWith(':') || part === pathParts[index])
}

export function metadataForPath(pathname: string) {
  return routeMetadata.find((route) => pathMatches(route.path, pathname)) ?? routeMetadata[0]
}

export function breadcrumbsForPath(pathname: string) {
  const route = metadataForPath(pathname)
  const breadcrumbs: RouteMetadata[] = [route]
  const visited = new Set([route.path])
  let parentPath = route.parent
  while (parentPath && !visited.has(parentPath)) {
    const parent = routeMetadata.find((candidate) => candidate.path === parentPath)
    if (!parent) break
    breadcrumbs.unshift(parent)
    visited.add(parent.path)
    parentPath = parent.parent
  }
  return breadcrumbs
}
