import { Link } from 'react-router-dom'

import { PageHeader, Surface } from '../../shared/components'
import { paths } from '../../shared/navigation/paths'

const destinations = [
  { to: paths.analysis, title: 'Current analysis', detail: 'Evidence-backed diagnosis, gaps, blockers, and Unknown state.' },
  { to: paths.recommendations, title: 'Recommendation history', detail: 'Deterministic choices, reasons, and audit lineage.' },
  { to: paths.legacy, title: 'Legacy V1 history', detail: 'Clearly labeled compatibility analytics, reports, and historical views.' },
]

export function InsightsIndexPage() {
  return <div className="page-stack"><PageHeader eyebrow="Understand the loop" title="Insights" description="Review current V2 diagnosis and recommendation history without mixing them with V1 compatibility records." /><div className="responsive-card-grid">{destinations.map((item) => <Surface key={item.to} className="p-5"><h2 className="font-display text-xl font-semibold"><Link className="focus-link" to={item.to}>{item.title}</Link></h2><p className="mt-2 text-sm leading-6 text-ink/65">{item.detail}</p></Surface>)}</div></div>
}

export function LegacyIndexPage() {
  return <div className="page-stack"><PageHeader eyebrow="Read-only compatibility" title="Legacy V1 history" description="These records preserve V1 meaning. They are not current V2 capability, eligibility, or Profile truth." /><div className="responsive-card-grid"><Surface className="p-5"><h2 className="font-display text-xl font-semibold"><Link to={paths.legacyToday}>Today and recommendation history</Link></h2></Surface><Surface className="p-5"><h2 className="font-display text-xl font-semibold"><Link to={paths.legacyRoadmap}>Roadmap history</Link></h2></Surface><Surface className="p-5"><h2 className="font-display text-xl font-semibold"><Link to={paths.legacyAnalytics}>V1 compatibility analytics</Link></h2></Surface><Surface className="p-5"><h2 className="font-display text-xl font-semibold"><Link to={paths.legacyReports}>Generated reports</Link></h2></Surface></div></div>
}
