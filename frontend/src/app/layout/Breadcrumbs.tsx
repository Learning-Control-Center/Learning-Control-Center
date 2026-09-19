import { ChevronRight } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'

import { breadcrumbsForPath } from '../router/routes'

export function Breadcrumbs() {
  const location = useLocation()
  const items = breadcrumbsForPath(location.pathname)
  if (items.length <= 1) return null
  return (
    <nav aria-label="Breadcrumb" className="mb-4 text-sm text-ink/60">
      <ol className="flex flex-wrap items-center gap-1.5">
        {items.map((item, index) => <li key={item.path} className="flex items-center gap-1.5">{index > 0 ? <ChevronRight className="size-3.5" aria-hidden="true" /> : null}{index === items.length - 1 ? <span aria-current="page">{item.label}</span> : <Link className="rounded-sm underline-offset-4 hover:underline" to={item.path}>{item.label}</Link>}</li>)}
      </ol>
    </nav>
  )
}
