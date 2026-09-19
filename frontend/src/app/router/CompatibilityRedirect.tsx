import { Navigate, useLocation } from 'react-router-dom'

import { routeMetadata } from './routes'

const allowedDestinations = new Set(routeMetadata.map((route) => route.path).filter((path) => !path.includes(':')))

export function CompatibilityRedirect({ to }: { to: string }) {
  const location = useLocation()
  if (!allowedDestinations.has(to)) throw new Error('Compatibility redirects require a canonical destination.')
  const safeSearch = location.search.length <= 2048 ? location.search : ''
  const safeHash = location.hash.length <= 512 ? location.hash : ''
  return <Navigate replace to={`${to}${safeSearch}${safeHash}`} />
}
