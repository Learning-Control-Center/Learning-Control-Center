import { ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, apiV2 } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type ProfileTarget = {
  id: string
  stableKey: string
  competencyIdentityId: string
  dimensionKey: string | null
  domainStableKey: string
  targetLevelStableKey: string
  priority: string
}

type ProfileVersion = {
  versionId: string
  version: number
  title: string
  description: string
  targets: ProfileTarget[]
}

type ProfileSummary = {
  id: string
  stableKey: string
  activeVersionId: string | null
  versions: ProfileVersion[]
}

type CapabilityState = {
  scopeKey: string
  dimensionKey: string | null
  capabilityLevelKey: string | null
  assessmentStatus: string
  aggregateConfidence: string
  freshness: string
  reviewDue: boolean
}

type Capability = {
  competencyIdentityId: string
  states: CapabilityState[]
}

type ActiveProfile = {
  profile: ProfileSummary
  version: ProfileVersion
  capabilities: Record<string, Capability>
}

export function ProfileCapabilityPage() {
  const [active, setActive] = useState<ActiveProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const profiles = await apiV2<ProfileSummary[]>('/target-profiles')
      const profile = profiles.find((item) => item.activeVersionId !== null)
      const version = profile?.versions.find((item) => item.versionId === profile.activeVersionId)
      if (!profile || !version) {
        setActive(null)
        return
      }
      const competencyIds = [...new Set(version.targets.map((item) => item.competencyIdentityId))]
      const capabilityItems = await Promise.all(
        competencyIds.map((id) => apiV2<Capability>(`/capabilities/${id}`)),
      )
      setActive({
        profile,
        version,
        capabilities: Object.fromEntries(
          capabilityItems.map((item) => [item.competencyIdentityId, item]),
        ),
      })
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Profile and capability could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])

  if (loading) return <LoadingState label="Loading target and capability context" />
  if (error) return <ErrorState message={error} retry={() => void load()} />
  if (!active) {
    return (
      <EmptyState
        title="No active Target Profile"
        detail="Activate an immutable Target Profile version to expose its target and capability context."
      />
    )
  }

  return (
    <section className="space-y-6" aria-labelledby="profile-capability-heading">
      <header>
        <p className="font-mono text-xs uppercase tracking-[0.18em] text-fern">Canonical learning context</p>
        <h1 id="profile-capability-heading" className="font-display text-3xl font-semibold text-ink">
          Profile &amp; Capability
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-ink/65">
          {active.version.title} · immutable version {active.version.version}. Targets describe intent;
          capability remains Evidence-derived and independently evaluated.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        {active.version.targets.map((target) => {
          const states = active.capabilities[target.competencyIdentityId]?.states ?? []
          const relevantStates = target.dimensionKey
            ? states.filter((item) => item.dimensionKey === target.dimensionKey)
            : states.filter((item) => item.scopeKey === 'overall')
          return (
            <article key={target.id} className="surface p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-display text-lg font-semibold text-ink">{target.stableKey}</p>
                  <p className="mt-1 text-xs text-ink/55">
                    {target.domainStableKey} · {target.priority}
                  </p>
                </div>
                <ShieldCheck className="size-5 text-fern" aria-hidden="true" />
              </div>
              <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-ink/55">Target level</dt>
                  <dd className="font-medium text-ink">{target.targetLevelStableKey}</dd>
                </div>
                <div>
                  <dt className="text-ink/55">Capability scope</dt>
                  <dd className="font-medium text-ink">{target.dimensionKey ?? 'overall'}</dd>
                </div>
              </dl>
              {relevantStates.length ? (
                <ul className="mt-4 space-y-3">
                  {relevantStates.map((state) => (
                    <li key={state.scopeKey} className="rounded-xl border border-ink/10 p-3 text-sm">
                      <p className="font-medium text-ink">
                        Current: {state.capabilityLevelKey ?? state.assessmentStatus}
                      </p>
                      <p className="mt-1 text-xs text-ink/55">
                        Confidence {state.aggregateConfidence} · freshness {state.freshness}
                        {state.reviewDue ? ' · review due' : ''}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 rounded-xl border border-dashed border-ink/15 p-3 text-sm text-ink/55">
                  Capability unknown — no current evaluation exists for this scope.
                </p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
