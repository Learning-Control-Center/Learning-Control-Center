import { StatusBadge, UnknownValue } from './ProductPrimitives'

export type CapabilitySummaryState = {
  scopeKey: string
  dimensionKey?: string | null
  levelTitle?: string | null
  capabilityLevelKey?: string | null
  assessmentStatus: string
  aggregateConfidence?: string
  confidence?: string
  freshness: string
  reviewDue?: boolean | null
}

const tone = (value: string) =>
  value === 'current' || value === 'fresh'
    ? 'success'
    : value === 'stale' || value === 'review_due'
      ? 'warning'
      : value === 'unknown' || value === 'unassessed'
        ? 'unknown'
        : 'neutral'

export function CapabilitySummary({ state, compact = false }: { state: CapabilitySummaryState | null; compact?: boolean }) {
  if (!state) return <UnknownValue reason="no current evaluation exists for this scope" />
  const level = state.levelTitle
  return (
    <div className={compact ? 'space-y-1' : 'rounded-xl border border-ink/10 p-3'}>
      <p className="font-medium text-ink">
        Current: {level ?? (state.assessmentStatus === 'unassessed' ? 'Unassessed' : 'Unknown')}
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <StatusBadge label={state.assessmentStatus.replaceAll('_', ' ')} tone={tone(state.assessmentStatus)} />
        <StatusBadge label={`Confidence ${state.aggregateConfidence ?? state.confidence ?? 'unknown'}`} tone={tone(state.aggregateConfidence ?? state.confidence ?? 'unknown')} />
        <StatusBadge label={`Freshness ${state.freshness}`} tone={tone(state.freshness)} />
        {state.reviewDue ? <StatusBadge label="Review due" tone="warning" /> : null}
      </div>
    </div>
  )
}
