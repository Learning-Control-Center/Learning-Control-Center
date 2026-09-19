import { paths, primaryRoutes, utilityRoutes } from '../router/routes'

export const productNavigation = primaryRoutes
export const utilityNavigation = utilityRoutes

export const insightNavigation = [
  { to: paths.analysis, label: 'Current analysis' },
  { to: paths.recommendations, label: 'Recommendation history' },
  { to: paths.legacy, label: 'Legacy V1 history' },
]
