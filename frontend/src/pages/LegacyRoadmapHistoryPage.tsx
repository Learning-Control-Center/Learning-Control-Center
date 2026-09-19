import { Archive } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'
import type { Roadmap } from '../types'

type RoadmapResponse = { configured: boolean; guidance?: string; roadmap?: Roadmap }

export function LegacyRoadmapHistoryPage() {
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const value = await api<RoadmapResponse>('/roadmap/current')
      setRoadmap(value.roadmap ?? null)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Legacy Roadmap history could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => void load(), [load])
  if (loading) return <LoadingState label="Loading preserved V1 Roadmap" />
  if (error) return <ErrorState message={error} retry={() => void load()} />
  return (
    <section className="mx-auto w-full max-w-6xl space-y-6" aria-labelledby="legacy-roadmap-heading">
      <header>
        <p className="eyebrow mb-2">Preserved compatibility source</p>
        <h1 id="legacy-roadmap-heading" className="page-title">Legacy Roadmap</h1>
        <p className="mt-2 max-w-3xl text-sm text-ink/60">
          This labeled V1 view is read-only after V2 activation. Phases remain historical context and never decide native V2 eligibility.
        </p>
      </header>
      {!roadmap ? (
        <EmptyState title="No legacy Roadmap configured" detail="The preserved V1 compatibility domain is empty." />
      ) : (
        <div className="space-y-4">
          <div className="surface flex flex-wrap items-center justify-between gap-3 p-5">
            <div><p className="eyebrow">Version {roadmap.activeVersion.version}</p><h2 className="mt-1 font-display text-2xl font-semibold">{roadmap.title}</h2></div>
            <span className="flex items-center gap-2 rounded-full bg-ink/5 px-3 py-1 text-xs text-ink/60"><Archive className="size-3.5" /> Read-only V1</span>
          </div>
          {roadmap.phases.map((phase) => (
            <article className="surface p-5" key={phase.id}>
              <h3 className="font-display text-xl font-semibold">{phase.title}</h3>
              <p className="mt-1 text-xs text-ink/50">Historical phase · {phase.isCurrent ? 'last V1 current pointer' : 'not current'}</p>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                {phase.tracks.map((track) => (
                  <section className="rounded-xl border border-ink/10 p-4" key={track.id}>
                    <h4 className="font-medium">{track.title}</h4>
                    <ul className="mt-2 space-y-1 text-sm text-ink/60">
                      {track.competencies.map((competency) => <li key={competency.identityId}>{competency.title} · {competency.status.replaceAll('_', ' ')}</li>)}
                    </ul>
                  </section>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
