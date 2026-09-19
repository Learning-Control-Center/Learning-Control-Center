import { NavLink, useLocation } from 'react-router-dom'

import { insightNavigation } from './navigation'

export function InsightsSubnavigation() {
  const location = useLocation()
  if (!location.pathname.startsWith('/insights')) return null
  return <nav aria-label="Insights sections" className="mb-5 flex max-w-full gap-2 overflow-x-auto rounded-xl border border-ink/10 bg-white/55 p-2">{insightNavigation.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `inline-flex min-h-11 shrink-0 items-center rounded-lg px-3 py-2 text-sm font-semibold ${isActive ? 'bg-ink text-white' : 'text-ink/65 hover:bg-white hover:text-ink'}`}>{item.label}</NavLink>)}</nav>
}
