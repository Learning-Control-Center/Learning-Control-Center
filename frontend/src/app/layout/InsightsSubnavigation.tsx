import { NavLink, useLocation } from 'react-router-dom'

import { insightNavigation } from './navigation'

export function InsightsSubnavigation() {
  const location = useLocation()
  if (!location.pathname.startsWith('/insights')) return null
  return <nav aria-label="Insights sections" className="mb-5 grid max-w-full grid-cols-2 gap-2 rounded-xl border border-ink/10 bg-white/55 p-2 sm:grid-cols-3">{insightNavigation.map((item, index) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `inline-flex min-h-11 min-w-0 items-center justify-center rounded-lg px-3 py-2 text-center text-sm font-semibold ${index === insightNavigation.length - 1 ? 'col-span-2 sm:col-span-1' : ''} ${isActive ? 'bg-ink text-white' : 'text-ink/65 hover:bg-white hover:text-ink'}`}>{item.label}</NavLink>)}</nav>
}
