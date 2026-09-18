import { Clock3, ListChecks, RefreshCw, Scale } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, apiV2, formatDuration } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type PortfolioItem = {
  candidateId: string
  stableId: string
  candidateType: string
  title: string
  description: string
  expectedLearningValue: string
  expectedLearningValueReasons: string[]
  score: number
  rank: number
  scoreComponents: { code: string; value: number }[]
  portfolioRole: 'primary' | 'complementary' | 'maintenance'
  decisionReason: string
  durationRangeMs: [number, number, number] | null
  advisoryDurationMs: number | null
  durationReason: string | null
  reasons: {
    code: string
    title: string
    renderedText: string
    scoreContribution: number | null
  }[]
}

type RecommendationRun = {
  id: string
  analysisSnapshotId: string
  generatedAt: number
  cutoffAt: number
  availableTimeMs: number | null
  algorithmVersion: string
  policyVersions: Record<string, string>
  inputHash: string
  outputHash: string
  portfolio: PortfolioItem[]
}

type CurrentAnalysis = {
  configured: boolean
  status: string
  snapshot: { id: string } | null
}

const label = (value: string) => value.replaceAll('_', ' ')

export function RecommendationsV2Page() {
  const [history, setHistory] = useState<RecommendationRun[]>([])
  const [selected, setSelected] = useState<RecommendationRun | null>(null)
  const [availableMinutes, setAvailableMinutes] = useState('')
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const runs = await apiV2<RecommendationRun[]>('/recommendations/history')
      setHistory(runs)
      setSelected((current) => current ?? runs[0] ?? null)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Recommendations could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])

  const inspect = async (runId: string) => {
    try {
      setSelected(await apiV2<RecommendationRun>(`/recommendations/runs/${runId}`))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The Recommendation run could not be loaded.')
    }
  }

  const generate = async () => {
    setGenerating(true)
    setError('')
    try {
      const analysis = await apiV2<CurrentAnalysis>('/analysis/current')
      if (!analysis.snapshot || analysis.status !== 'current') {
        throw new Error('A current Analysis V3 snapshot is required before generating recommendations.')
      }
      const parsed = availableMinutes.trim() ? Number(availableMinutes) : null
      if (parsed !== null && (!Number.isInteger(parsed) || parsed < 0)) {
        throw new Error('Available time must be a whole number of minutes.')
      }
      const run = await apiV2<RecommendationRun>('/recommendations/runs', {
        method: 'POST',
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          analysis_snapshot_id: analysis.snapshot.id,
          available_time_ms: parsed === null ? null : parsed * 60_000,
        }),
      })
      setSelected(run)
      await load()
      setSelected(run)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Recommendations could not be generated.')
    } finally {
      setGenerating(false)
    }
  }

  if (loading) return <LoadingState label="Loading immutable Recommendation history" />
  if (error && !selected && !history.length) {
    return <ErrorState message={error} retry={() => void load()} />
  }

  return (
    <div className="mx-auto w-full max-w-[86rem]">
      <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow mb-2">Deterministic learning control</p>
          <h1 className="page-title">Recommendation V2</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink/60">
            An advisory portfolio selected from immutable Analysis facts and explicit eligibility,
            score, tie-break, and duration policies.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs font-medium text-ink/65">
            Available minutes (optional)
            <input
              className="mt-1 block w-44 rounded-xl border border-ink/15 bg-white px-3 py-2 text-sm"
              min="0"
              onChange={(event) => setAvailableMinutes(event.target.value)}
              placeholder="Unknown"
              step="5"
              type="number"
              value={availableMinutes}
            />
          </label>
          <button
            className="button-primary min-h-10"
            disabled={generating}
            onClick={() => void generate()}
            type="button"
          >
            <RefreshCw className={`size-4 ${generating ? 'animate-spin' : ''}`} />
            {generating ? 'Generating' : 'Generate new run'}
          </button>
        </div>
      </header>
      {error ? <div className="mb-5"><ErrorState message={error} /></div> : null}
      {!selected ? (
        <EmptyState
          title="No Recommendation V2 run yet"
          detail="Generate explicitly from the current Analysis V3 snapshot. Reading this page never generates or changes recommendations."
        />
      ) : (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="space-y-5">
            <section className="surface grid gap-4 p-5 sm:grid-cols-3">
              <div><p className="eyebrow">Generated</p><p className="mt-2 text-sm">{new Date(selected.generatedAt).toLocaleString()}</p></div>
              <div><p className="eyebrow">Available time</p><p className="mt-2 text-sm">{selected.availableTimeMs === null ? 'Unknown — no total-window constraint' : formatDuration(selected.availableTimeMs)}</p></div>
              <div><p className="eyebrow">Portfolio</p><p className="mt-2 text-sm">{selected.portfolio.length} advisory item{selected.portfolio.length === 1 ? '' : 's'}</p></div>
            </section>
            <section className="space-y-4">
              {selected.portfolio.map((item) => (
                <article className="surface p-5" key={item.candidateId}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="eyebrow capitalize">{item.portfolioRole}</p>
                      <h2 className="mt-1 font-display text-xl font-semibold">{item.title}</h2>
                      <p className="mt-2 text-sm text-ink/60">{item.description}</p>
                    </div>
                    <span className="rounded-full bg-moss/10 px-3 py-1 text-xs font-medium capitalize text-moss">
                      {label(item.candidateType)}
                    </span>
                  </div>
                  <div className="mt-5 grid gap-3 sm:grid-cols-3">
                    <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Advisory duration</p><p className="mt-1 text-sm">{item.advisoryDurationMs === null ? label(item.durationReason ?? 'unknown') : formatDuration(item.advisoryDurationMs)}</p></div>
                    <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Expected value</p><p className="mt-1 text-sm capitalize">{label(item.expectedLearningValue)}</p></div>
                    <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Exact score</p><p className="mt-1 text-sm">{item.score}</p></div>
                  </div>
                  <details className="mt-4 rounded-xl border border-ink/10 p-3">
                    <summary className="cursor-pointer text-sm font-medium">Why this was selected</summary>
                    <ul className="mt-3 space-y-2 text-xs text-ink/65">
                      {item.reasons.map((reason) => (
                        <li key={`${reason.code}-${reason.title}`}>{reason.renderedText}</li>
                      ))}
                    </ul>
                    <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                      {item.scoreComponents.map((component) => (
                        <div className="rounded-lg bg-ink/5 p-2 text-xs" key={component.code}>
                          <span className="block font-mono text-[0.65rem] text-ink/55">{component.code}</span>
                          <span className="font-medium">{component.value >= 0 ? '+' : ''}{component.value}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                </article>
              ))}
              {!selected.portfolio.length ? <EmptyState title="No useful eligible work" detail="The run is preserved with its rejected-candidate audit. No score threshold was used to manufacture a recommendation." /> : null}
            </section>
          </div>
          <aside className="space-y-5">
            <section className="surface p-5">
              <div className="mb-4 flex items-center gap-2"><ListChecks className="size-4 text-moss" /><h2 className="font-display text-lg font-semibold">Run history</h2></div>
              <ul className="space-y-2">
                {history.map((run) => (
                  <li key={run.id}><button className={`w-full rounded-xl border p-3 text-left ${selected.id === run.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`} onClick={() => void inspect(run.id)} type="button"><span className="block text-sm font-medium">{run.portfolio.length} selected</span><span className="mt-1 flex items-center gap-1 text-xs text-ink/50"><Clock3 className="size-3" />{new Date(run.generatedAt).toLocaleString()}</span></button></li>
                ))}
              </ul>
            </section>
            <section className="surface break-words p-5 text-xs text-ink/60">
              <div className="mb-3 flex items-center gap-2"><Scale className="size-4 text-copper" /><p className="eyebrow">Policy lineage</p></div>
              <p>{selected.algorithmVersion}</p>
              {Object.values(selected.policyVersions).map((version) => <p key={version}>{version}</p>)}
              <p className="mt-3 font-mono">{selected.outputHash}</p>
            </section>
          </aside>
        </div>
      )}
    </div>
  )
}
