import { AlertCircle, BrainCircuit, Clock3, History, ShieldQuestion } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, apiV2 } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type Signal = {
  stableKey: string
  type: string
  subject: { subjectType: string; subjectId: string; dimensionKey: string | null }
  severity: string
  reasonCodes: string[]
  decisiveFacts: Record<string, unknown>
}

type NormalizedFact = {
  stable_key: string
  fact_type: string
  subject_type: string
  subject_id: string
  payload: Record<string, unknown>
}

type Gap = {
  targetIdentityId: string
  competencyIdentityId: string
  dimensionKey: string | null
  comparisonStatus: string
  severity: string
  reasonCodes: string[]
}

type UnknownMarker = {
  fieldPath: string
  subjectType: string
  subjectId: string
  reasonCode: string
}

type Snapshot = {
  id: string
  runId: string
  purpose: string
  generatedAt: number
  cutoffAt: number
  cutoffSemantics: string
  completedThroughDate: string
  completeness: string
  inputHash: string
  outputHash: string
  policyVersions: Record<string, string>
  lineage: {
    analysis_algorithm_version: string
    analysis_policy_version: string
    normalization_schema_version: string
  } | null
  gaps: Gap[]
  signals: Signal[]
  normalizedFacts: NormalizedFact[]
  unknownMarkers: UnknownMarker[]
}

type CurrentAnalysis = {
  configured: boolean
  status: string
  snapshot: Snapshot | null
}

type HistoryItem = {
  id: string
  runId: string
  purpose: string
  generatedAt: number
  cutoffAt: number
  completeness: string
  outputHash: string
}

const label = (value: string) => value.replaceAll('_', ' ')

export function AnalysisPage() {
  const [current, setCurrent] = useState<CurrentAnalysis | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [selected, setSelected] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [currentResult, historyResult] = await Promise.all([
        apiV2<CurrentAnalysis>('/analysis/current'),
        apiV2<HistoryItem[]>('/analysis/history'),
      ])
      setCurrent(currentResult)
      setHistory(historyResult)
      setSelected(currentResult.snapshot)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Analysis could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])

  const inspect = async (snapshotId: string) => {
    try {
      setSelected(await apiV2<Snapshot>(`/analysis/snapshots/${snapshotId}`))
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : 'The Analysis snapshot could not be loaded.',
      )
    }
  }

  if (loading) return <LoadingState label="Loading immutable Analysis history" />
  if (error && !current) return <ErrorState message={error} retry={() => void load()} />
  if (!current?.configured || !selected) {
    return (
      <EmptyState
        title="No Analysis V3 snapshot yet"
        detail="Analysis is generated explicitly or during startup invalidation recovery. Reading this page never recomputes it."
      />
    )
  }

  return (
    <div className="mx-auto w-full max-w-[86rem]">
      <header className="mb-7">
        <p className="eyebrow mb-2">Deterministic diagnosis</p>
        <h1 className="page-title">Analysis V3</h1>
        <p className="mt-2 max-w-3xl text-sm text-ink/60">
          Cutoff-correct gaps and signals describe learning state. Analysis does not choose work or
          change capability.
        </p>
      </header>
      {error ? <div className="mb-5"><ErrorState message={error} /></div> : null}
      <section className="surface mb-5 grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <p className="eyebrow">Current pointer</p>
          <p className="mt-2 font-medium capitalize">
            {current.snapshot?.id === selected.id ? current.status : 'historical snapshot'}
          </p>
        </div>
        <div>
          <p className="eyebrow">Completeness</p>
          <p className="mt-2 font-medium capitalize">{selected.completeness}</p>
        </div>
        <div>
          <p className="eyebrow">Exclusive cutoff</p>
          <p className="mt-2 text-sm">{new Date(selected.cutoffAt).toLocaleString()}</p>
        </div>
        <div>
          <p className="eyebrow">Completed through</p>
          <p className="mt-2 text-sm">{selected.completedThroughDate}</p>
        </div>
      </section>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-5">
          <section className="surface p-5">
            <div className="mb-4 flex items-center gap-2">
              <BrainCircuit className="size-4 text-moss" aria-hidden="true" />
              <h2 className="font-display text-lg font-semibold">Diagnostic signals</h2>
            </div>
            {selected.signals.length ? (
              <div className="grid gap-3 md:grid-cols-2">
                {selected.signals.map((signal) => (
                  <article className="rounded-2xl border border-ink/10 bg-white/70 p-4" key={signal.stableKey}>
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="font-medium">{label(signal.type)}</h3>
                      <span className="rounded-full bg-ink/5 px-2 py-1 text-xs capitalize">{signal.severity}</span>
                    </div>
                    <p className="mt-2 text-xs text-ink/55">
                      {signal.subject.subjectType} · {signal.subject.subjectId}
                    </p>
                    <p className="mt-3 font-mono text-xs text-ink/60">{signal.reasonCodes.join(', ')}</p>
                    <pre className="mt-3 overflow-auto rounded-xl bg-ink/5 p-3 text-[0.7rem] text-ink/60">
                      {JSON.stringify(signal.decisiveFacts, null, 2)}
                    </pre>
                  </article>
                ))}
              </div>
            ) : <p className="text-sm text-ink/55">No diagnostic signals in this snapshot.</p>}
          </section>
          <section className="surface p-5">
            <h2 className="font-display text-lg font-semibold">Normalized diagnostic facts</h2>
            <p className="mt-1 text-sm text-ink/55">
              Capability, confidence, freshness, review, allocation, and workload inputs frozen at
              the exclusive cutoff.
            </p>
            <div className="mt-4 space-y-3">
              {selected.normalizedFacts.map((fact) => (
                <details className="rounded-xl border border-ink/10 bg-white/60 p-3" key={fact.stable_key}>
                  <summary className="cursor-pointer text-sm font-medium">
                    {label(fact.fact_type)} · {fact.subject_id}
                  </summary>
                  <pre className="mt-3 overflow-auto text-[0.7rem] text-ink/60">
                    {JSON.stringify(fact.payload, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          </section>
          <section className="surface p-5">
            <div className="mb-4 flex items-center gap-2">
              <AlertCircle className="size-4 text-copper" aria-hidden="true" />
              <h2 className="font-display text-lg font-semibold">Competency gaps</h2>
            </div>
            {selected.gaps.length ? selected.gaps.map((gap) => (
              <article className="border-t border-ink/10 py-3 first:border-0" key={`${gap.targetIdentityId}:${gap.dimensionKey ?? 'overall'}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium">{gap.competencyIdentityId}</p>
                  <span className="text-sm capitalize">{gap.severity} · {label(gap.comparisonStatus)}</span>
                </div>
                <p className="mt-1 font-mono text-xs text-ink/55">{gap.reasonCodes.join(', ')}</p>
              </article>
            )) : <p className="text-sm text-ink/55">No target gaps are present.</p>}
          </section>
          {selected.unknownMarkers.length ? (
            <section className="surface p-5">
              <div className="mb-4 flex items-center gap-2">
                <ShieldQuestion className="size-4 text-copper" aria-hidden="true" />
                <h2 className="font-display text-lg font-semibold">Unknown inputs</h2>
              </div>
              <ul className="space-y-2 text-sm">
                {selected.unknownMarkers.map((marker) => (
                  <li className="rounded-xl bg-copper/5 p-3" key={`${marker.fieldPath}:${marker.subjectId}:${marker.reasonCode}`}>
                    <span className="font-mono text-xs">{marker.reasonCode}</span>
                    <span className="ml-2 text-ink/55">{marker.fieldPath}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
        <aside className="space-y-5">
          <section className="surface p-5">
            <div className="mb-4 flex items-center gap-2">
              <History className="size-4 text-moss" aria-hidden="true" />
              <h2 className="font-display text-lg font-semibold">History</h2>
            </div>
            <ul className="space-y-2">
              {history.map((item) => (
                <li key={item.id}>
                  <button className={`w-full rounded-xl border p-3 text-left ${selected.id === item.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`} onClick={() => void inspect(item.id)} type="button">
                    <span className="block text-sm font-medium capitalize">{label(item.purpose)}</span>
                    <span className="mt-1 flex items-center gap-1 text-xs text-ink/50"><Clock3 className="size-3" />{new Date(item.generatedAt).toLocaleString()}</span>
                    <span className="mt-1 block text-xs capitalize text-ink/50">{item.completeness}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section className="surface break-words p-5 text-xs text-ink/60">
            <p className="eyebrow mb-3">Lineage</p>
            <p>{selected.lineage?.analysis_algorithm_version}</p>
            <p>{selected.lineage?.analysis_policy_version}</p>
            <p>{selected.lineage?.normalization_schema_version}</p>
            <p className="mt-3 font-mono">output {selected.outputHash}</p>
          </section>
        </aside>
      </div>
    </div>
  )
}
