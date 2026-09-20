import { Link } from 'react-router-dom'

import { PageHeader, Surface } from '../../shared/components'
import { paths } from '../../shared/navigation/paths'

const destinations = [
  { to: paths.analysis, title: 'Analysis V3', detail: 'Current and historical Evidence-backed diagnosis: gaps, signals, completeness, and Unknown state. It describes what has happened.' },
  { to: paths.recommendations, title: 'Recommendation V2', detail: 'Deterministic advisory choices, human-readable reasons, and audit lineage. It chooses what may make sense next.' },
  { to: paths.legacy, title: 'Legacy V1 history', detail: 'Read-only V1 Today/Roadmap records, recalculated compatibility Analytics, and immutable generated Reports.' },
]

export function InsightsIndexPage() {
  return <div className="page-stack"><PageHeader eyebrow="Understand the loop" title="Insights" description="Review current V2 diagnosis and recommendation history without mixing them with V1 compatibility records." /><div className="responsive-card-grid">{destinations.map((item) => <Surface key={item.to} className="p-5"><h2 className="font-display text-xl font-semibold"><Link className="focus-link" to={item.to}>{item.title}</Link></h2><p className="mt-2 text-sm leading-6 text-ink/65">{item.detail}</p></Surface>)}</div></div>
}

export function LegacyIndexPage() {
  return <div className="page-stack"><PageHeader eyebrow="Read-only compatibility" title="Legacy V1 history" description="These preserved records remain readable under their original meaning. They are not current V2 capability, eligibility, Profile, Analysis, or Recommendation truth." /><div className="responsive-card-grid"><LegacyCard to={paths.legacyToday} title="Today and recommendation history" detail="Immutable V1 daily recommendations and interactions." /><LegacyCard to={paths.legacyRoadmap} title="Roadmap history" detail="Preserved V1 phase and Track interpretation." /><LegacyCard to={paths.legacyAnalytics} title="V1 compatibility analytics" detail="A recalculated range/as-of view, not an immutable snapshot." /><LegacyCard to={paths.legacyReports} title="Generated V1 reports" detail="Immutable report documents; current reflection is edited on Today or Activity." /></div></div>
}

function LegacyCard({ to, title, detail }: { to: string; title: string; detail: string }) { return <Surface className="p-5"><p className="eyebrow">Read-only V1</p><h2 className="mt-2 font-display text-xl font-semibold"><Link className="focus-link" to={to}>{title}</Link></h2><p className="mt-2 text-sm leading-6 text-ink/65">{detail}</p></Surface> }
