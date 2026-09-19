import { History } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type LegacySnapshot = {
  id: string
  generatedAt: string
  localDate: string
  engineVersion: number
  acceptedPrimary: boolean | null
  chosenCompetencyIdentityId: string | null
  analysisSnapshotId: string | null
  authority: 'legacy_v1_history'
  recommendation: {
    primary?: { title?: string; activity?: string } | null
    secondary?: { title?: string } | null
  }
}

export function LegacyTodayHistoryPage() {
  const [items, setItems] = useState<LegacySnapshot[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setItems(await api<LegacySnapshot[]>('/recommendations/history'))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Legacy recommendation history could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])
  if (loading) return <LoadingState label="Loading preserved V1 recommendation history" />
  if (error) return <ErrorState message={error} retry={() => void load()} />
  return (
    <section className="mx-auto w-full max-w-5xl space-y-6" aria-labelledby="legacy-today-heading">
      <header>
        <p className="eyebrow mb-2">Preserved compatibility history</p>
        <h1 id="legacy-today-heading" className="page-title">Legacy Today history</h1>
        <p className="mt-2 max-w-3xl text-sm text-ink/60">
          Read-only V1 Recommendation snapshots remain queryable after V2 activation. This view never generates work or records decisions.
        </p>
      </header>
      {!items.length ? (
        <EmptyState title="No legacy recommendations recorded" detail="No V1 Recommendation snapshot exists in the preserved history." />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <article className="surface p-5" key={item.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="eyebrow">V1 engine {item.engineVersion} · {item.localDate}</p>
                  <h2 className="mt-2 font-display text-xl font-semibold">
                    {item.recommendation.primary?.title ?? 'No primary recommendation'}
                  </h2>
                  {item.recommendation.secondary?.title ? <p className="mt-1 text-sm text-ink/55">Secondary: {item.recommendation.secondary.title}</p> : null}
                </div>
                <span className="flex items-center gap-2 rounded-full bg-ink/5 px-3 py-1 text-xs text-ink/60">
                  <History className="size-3.5" aria-hidden="true" /> Read-only V1
                </span>
              </div>
              <dl className="mt-4 grid gap-3 text-xs text-ink/55 sm:grid-cols-3">
                <div><dt>Generated</dt><dd className="mt-1 text-ink">{new Date(item.generatedAt).toLocaleString()}</dd></div>
                <div><dt>Primary accepted</dt><dd className="mt-1 text-ink">{item.acceptedPrimary === null ? 'Not recorded' : item.acceptedPrimary ? 'Yes' : 'No'}</dd></div>
                <div><dt>Snapshot ID</dt><dd className="mt-1 break-all font-mono text-ink">{item.id}</dd></div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
