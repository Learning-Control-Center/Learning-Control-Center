import { BookOpen, CheckCircle2, Clock3, ShieldQuestion } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, apiV2, formatDuration } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type Curriculum = {
  id: string
  stableKey: string
  activeVersionId: string | null
}

type Unit = {
  curriculumId: string
  unitDefinitionId: string
  unitStableKey: string
  kind: string
  title: string
  description: string
  durationRangeMs: [number, number, number] | null
  requirements: { stableKey: string; requirementType: string; effect: string }[]
  evidenceOpportunities: { stableKey: string; evidenceKind: string }[]
}

type CurriculumVersion = {
  id: string
  version: number
  title: string
  description: string
  contentHash: string
  effectiveAt: string
}

type Availability = {
  availabilityState: 'met' | 'not_met' | 'unknown'
  readinessState: 'met' | 'not_met' | 'unknown'
  candidateUsabilityState: 'met' | 'not_met' | 'unknown'
  requirements: { stableKey: string; state: string; reasonCode: string }[]
  targetSuitability: { targetId: string; state: string; reasonCode: string }[]
}

type Catalog = {
  activeVersionReferences: { curriculumId: string; versionId: string; version: number }[]
  units: Unit[]
  inputHash: string
}

export function CurriculumPage() {
  const [curricula, setCurricula] = useState<Curriculum[]>([])
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [versions, setVersions] = useState<CurriculumVersion[]>([])
  const [availability, setAvailability] = useState<Record<string, Availability>>({})
  const [activating, setActivating] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [items, active] = await Promise.all([
        apiV2<Curriculum[]>('/curricula'),
        apiV2<Catalog>('/curricula/catalog/active'),
      ])
      setCurricula(items)
      setCatalog(active)
      setSelectedId((current) =>
        items.some((item) => item.id === current) ? current : (items[0]?.id ?? ''),
      )
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Curriculum could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])
  useEffect(() => {
    if (!selectedId) {
      setVersions([])
      return
    }
    void apiV2<CurriculumVersion[]>(`/curricula/${selectedId}/versions`)
      .then(setVersions)
      .catch((caught) =>
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'Curriculum versions could not be loaded.',
        ),
      )
  }, [selectedId])

  const selected = curricula.find((item) => item.id === selectedId)
  const units = useMemo(
    () => catalog?.units.filter((unit) => unit.curriculumId === selectedId) ?? [],
    [catalog, selectedId],
  )

  const inspectAvailability = async (unitId: string) => {
    try {
      const result = await apiV2<Availability>(`/curricula/units/${unitId}/availability`)
      setAvailability((current) => ({ ...current, [unitId]: result }))
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'Unit availability could not be loaded.',
      )
    }
  }

  const activate = async (versionId: string) => {
    if (!selected) return
    setActivating(versionId)
    setError('')
    try {
      await apiV2(`/curricula/${selected.id}/versions/${versionId}/activate`, {
        method: 'POST',
        body: JSON.stringify({
          reason: 'Activated from the Curriculum page',
          source: 'user',
          idempotency_key: crypto.randomUUID(),
        }),
      })
      await load()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Curriculum activation failed.')
    } finally {
      setActivating('')
    }
  }

  if (loading) return <LoadingState label="Loading active Curriculum versions" />
  if (error && !curricula.length) {
    return <ErrorState message={error} retry={() => void load()} />
  }
  if (!curricula.length) {
    return (
      <EmptyState
        title="No Curriculum yet"
        detail="Create a versioned Curriculum through the V2 API to expose learning actions here."
      />
    )
  }

  return (
    <div className="mx-auto w-full max-w-[78rem]">
      <header className="mb-7">
        <p className="eyebrow mb-2">Learning material and actions</p>
        <h1 className="page-title">Curriculum</h1>
        <p className="mt-2 text-sm text-ink/60">
          Active immutable versions provide opportunities for learning and evidence. Completion is
          not capability.
        </p>
      </header>
      {error ? (
        <div className="mb-5">
          <ErrorState message={error} retry={() => void load()} />
        </div>
      ) : null}
      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="surface p-5">
          <p className="eyebrow mb-4">Collections</p>
          <ul className="space-y-2">
            {curricula.map((item) => (
              <li key={item.id}>
                <button
                  className={`w-full rounded-xl border p-3 text-left ${selectedId === item.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`}
                  onClick={() => setSelectedId(item.id)}
                  type="button"
                >
                  <span className="font-medium">{item.stableKey}</span>
                  <span className="mt-1 block text-xs text-ink/50">
                    {item.activeVersionId ? 'Active version' : 'Not active'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="eyebrow mb-3 mt-6">Immutable versions</p>
          <ul className="space-y-2">
            {versions.map((version) => (
              <li className="rounded-xl border border-ink/10 bg-white/60 p-3" key={version.id}>
                <p className="text-sm font-medium">
                  v{version.version} · {version.title}
                </p>
                <p className="mt-1 text-xs text-ink/50">
                  {new Date(version.effectiveAt).toLocaleString()}
                </p>
                {selected?.activeVersionId === version.id ? (
                  <span className="mt-2 inline-flex items-center gap-1 text-xs text-moss">
                    <CheckCircle2 className="size-3" />Active
                  </span>
                ) : (
                  <button
                    className="mt-2 rounded-lg border border-ink/15 px-2.5 py-1 text-xs disabled:opacity-50"
                    disabled={Boolean(activating)}
                    onClick={() => void activate(version.id)}
                    type="button"
                  >
                    {activating === version.id ? 'Activating…' : 'Activate atomically'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </aside>
        <section className="surface p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <p className="eyebrow">Active units</p>
            <span className="font-mono text-xs text-ink/45">{units.length} units</span>
          </div>
          {units.length ? (
            <div className="grid gap-3 md:grid-cols-2">
              {units.map((unit) => {
                const readiness = availability[unit.unitDefinitionId]
                return (
                  <article
                    className="rounded-2xl border border-ink/10 bg-white/70 p-5"
                    key={unit.unitDefinitionId}
                  >
                    <div className="mb-3 flex items-center gap-2 text-moss">
                      <BookOpen className="size-4" aria-hidden="true" />
                      <span className="font-mono text-xs uppercase tracking-wide">
                        {unit.kind.replaceAll('_', ' ')}
                      </span>
                    </div>
                    <h2 className="font-display text-lg font-semibold">{unit.title}</h2>
                    <p className="mt-2 text-sm leading-6 text-ink/60">
                      {unit.description || 'No description.'}
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2 text-xs text-ink/55">
                      <span className="inline-flex items-center gap-1 rounded-full bg-ink/5 px-2.5 py-1">
                        <Clock3 className="size-3" aria-hidden="true" />
                        {unit.durationRangeMs
                          ? formatDuration(unit.durationRangeMs[1])
                          : 'Duration unknown'}
                      </span>
                      <span>{unit.requirements.length} requirements</span>
                      <span>{unit.evidenceOpportunities.length} evidence opportunities</span>
                    </div>
                    <div className="mt-4 border-t border-ink/10 pt-3 text-xs text-ink/60">
                      {unit.requirements.map((item) => (
                        <p key={item.stableKey}>
                          {item.effect}: {item.requirementType}
                        </p>
                      ))}
                      {unit.evidenceOpportunities.map((item) => (
                        <p key={item.stableKey}>Opportunity: {item.evidenceKind}</p>
                      ))}
                    </div>
                    <button
                      className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-ink/15 px-3 py-1.5 text-xs"
                      onClick={() => void inspectAvailability(unit.unitDefinitionId)}
                      type="button"
                    >
                      <ShieldQuestion className="size-3.5" aria-hidden="true" />Inspect readiness
                    </button>
                    {readiness ? (
                      <p className="mt-2 text-xs text-ink/60">
                        Availability: {readiness.availabilityState}; readiness:{' '}
                        {readiness.readinessState}; candidate usability:{' '}
                        {readiness.candidateUsabilityState}; target suitability:{' '}
                        {readiness.targetSuitability.map((item) => item.state).join(', ') ||
                          'not declared'}
                      </p>
                    ) : null}
                  </article>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-ink/60">No units are active at this cutoff.</p>
          )}
        </section>
      </div>
    </div>
  )
}
