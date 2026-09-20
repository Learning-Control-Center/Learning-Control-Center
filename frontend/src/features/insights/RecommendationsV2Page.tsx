import { Clock3, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { ApiError, apiV2, formatDuration } from '../../api'
import { AuditDisclosure, Button, EmptyState, ErrorState, LiveNotice, LoadingState, MutationError, PageHeader, ReasonList, SectionHeader, StatusBadge, Surface } from '../../shared/components'
import type { CurrentAnalysis, RecommendationRun } from './model'

const label = (value: string) => value.replaceAll('_', ' ')

export function RecommendationsV2Page() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedRunId = searchParams.get('run')
  const requestedCandidateId = searchParams.get('candidate')
  const [history, setHistory] = useState<RecommendationRun[]>([])
  const [selected, setSelected] = useState<RecommendationRun | null>(null)
  const [availableMinutes, setAvailableMinutes] = useState('')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState('')
  const [loadError, setLoadError] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setLoadError('')
    try {
      const runs = await apiV2<RecommendationRun[]>('/recommendations/history', { signal })
      setHistory(runs)
      const selectedId = runs.some((run) => run.id === requestedRunId) ? requestedRunId : runs[0]?.id
      setSelected(selectedId ? await apiV2<RecommendationRun>(`/recommendations/runs/${selectedId}`, { signal }) : null)
    } catch (caught) {
      if ((caught as Error).name !== 'AbortError') setLoadError(caught instanceof ApiError ? caught.message : 'Recommendation history could not be loaded.')
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [requestedRunId])

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])

  const inspect = async (id: string) => {
    if (working) return
    setWorking(id); setLoadError('')
    try {
      setSelected(await apiV2<RecommendationRun>(`/recommendations/runs/${id}`))
      setSearchParams({ run: id }, { replace: false })
    } catch (caught) { setLoadError(caught instanceof ApiError ? caught.message : 'The Recommendation run could not be loaded.') }
    finally { setWorking('') }
  }

  const generate = async () => {
    if (working) return
    setWorking('generate'); setMutationError(''); setNotice('')
    try {
      const analysis = await apiV2<CurrentAnalysis>('/analysis/current')
      if (!analysis.snapshot || analysis.status !== 'current') throw new Error('A current Analysis V3 snapshot is required before generating recommendations.')
      const parsed = availableMinutes.trim() ? Number(availableMinutes) : null
      if (parsed !== null && (!Number.isInteger(parsed) || parsed < 0)) throw new Error('Available time must be a whole number of minutes.')
      const run = await apiV2<RecommendationRun>('/recommendations/runs', { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), analysis_snapshot_id: analysis.snapshot.id, available_time_ms: parsed === null ? null : parsed * 60_000 }) })
      setHistory((current) => [run, ...current.filter((item) => item.id !== run.id)])
      setSelected(run); setSearchParams({ run: run.id }, { replace: true })
      setNotice('A new deterministic Recommendation run was generated explicitly.')
    } catch (caught) { setMutationError(caught instanceof Error ? caught.message : 'Recommendations could not be generated.') }
    finally { setWorking('') }
  }

  if (loading) return <LoadingState label="Loading immutable Recommendation history" />
  if (loadError && !selected && !history.length) return <div className="space-y-6"><PageHeader eyebrow="Deterministic choice" title="Recommendations" description="What makes sense to work on next, based on Analysis V3." /><ErrorState message={loadError} retry={() => void load()} /></div>

  return <div className="mx-auto w-full max-w-[90rem] space-y-6">
    <LiveNotice>{notice}</LiveNotice>
    <PageHeader eyebrow="Deterministic choice" title="Recommendations" description="Recommendation V2 chooses an advisory portfolio from Analysis facts. It does not diagnose capability, record work, or alter Today until an explicit command." actions={<div className="flex flex-wrap items-end gap-2"><label className="text-xs font-medium text-ink/65">Available minutes (optional)<input className="mt-1 block w-44 rounded-xl border border-ink/15 bg-white px-3 py-2 text-sm" min="0" step="5" type="number" value={availableMinutes} placeholder="Unknown" onChange={(event) => setAvailableMinutes(event.target.value)} /></label><Button disabled={Boolean(working)} aria-busy={working === 'generate'} onClick={() => void generate()}><RefreshCw className="size-4" />{working === 'generate' ? 'Generating…' : 'Generate new run'}</Button></div>} />
    {mutationError ? <MutationError>{mutationError}</MutationError> : null}{loadError ? <MutationError>{loadError}</MutationError> : null}
    {!selected ? <EmptyState title="No Recommendation V2 run" detail="Generate explicitly from the current Analysis V3 snapshot. Reading this page never generates or changes recommendations." /> : <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-5">
        <Surface className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4"><Fact title="Generated" value={new Date(selected.generatedAt).toLocaleString()} /><Fact title="Exclusive cutoff" value={new Date(selected.cutoffAt).toLocaleString()} /><Fact title="Available time" value={selected.availableTimeMs === null ? 'Unknown — unconstrained total window' : formatDuration(selected.availableTimeMs)} /><Fact title="Advisory portfolio" value={`${selected.portfolio.length} item${selected.portfolio.length === 1 ? '' : 's'}`} /></Surface>
        <section aria-labelledby="recommendation-portfolio-heading"><SectionHeader headingId="recommendation-portfolio-heading" title="Selected work" description="Readable reasons lead. Scores and policy machinery remain available as audit details." /><div className="mt-4 space-y-4">{selected.portfolio.map((item) => <article className={`surface p-5 ${item.portfolioRole === 'primary' ? 'border-l-4 border-moss' : ''}`} key={item.candidateId}><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">{item.portfolioRole === 'primary' ? 'Primary action' : label(item.portfolioRole ?? 'selected')}</p><h2 className="mt-1 font-display text-xl font-semibold">{item.title}</h2><p className="mt-2 text-sm leading-6 text-ink/65">{item.description}</p></div><StatusBadge label={label(item.candidateType)} tone="info" /></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><Fact title="Advisory duration" value={item.advisoryDurationMs == null ? label(item.durationReason ?? 'Unknown') : formatDuration(item.advisoryDurationMs)} /><Fact title="Expected learning value" value={label(item.expectedLearningValue)} /></div><div className="mt-4"><ReasonList reasons={item.reasons.map((reason) => ({ code: reason.code, title: reason.title, text: reason.renderedText }))} /></div><AuditDisclosure label="Scoring and selection audit"><p>Deterministic score: {item.score}; rank: {item.rank}</p><p>Decision: {item.decisionReason}</p><ul className="mt-2">{item.scoreComponents.map((component) => <li key={component.code}>{component.code}: {component.value >= 0 ? '+' : ''}{component.value}</li>)}</ul><p className="mt-2 font-mono">Candidate ID: {item.candidateId}</p></AuditDisclosure></article>)}{!selected.portfolio.length ? <EmptyState title="No useful eligible work" detail="The run and rejected-candidate audit are preserved. No score threshold manufactured a recommendation." /> : null}</div></section>
        <AuditDisclosure label={`Full candidate audit · ${selected.candidateAudit?.length ?? 0} considered`} open={Boolean(requestedCandidateId)}><ul className="space-y-3">{(selected.candidateAudit ?? []).map((candidate) => <li className={`rounded-lg border p-3 ${candidate.candidateId === requestedCandidateId ? 'border-copper bg-copper/5' : 'border-ink/10'}`} id={`candidate-${candidate.candidateId}`} key={candidate.candidateId}><p className="font-medium">{candidate.title}</p><p>{label(candidate.decision)} · {candidate.eligible ? 'eligible' : label(candidate.eligibilityReason)}</p><p>{candidate.score == null || candidate.rank == null ? 'Not scored or ranked' : `Score ${candidate.score}; rank ${candidate.rank}`}; {label(candidate.decisionReason)}</p><p className="mt-1">Duration decision: {candidate.advisoryDurationMs == null ? label(candidate.durationReason ?? 'Unknown') : formatDuration(candidate.advisoryDurationMs)}</p><details className="mt-2"><summary className="min-h-11 cursor-pointer py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-moss">Eligibility rules and decisive facts</summary><pre className="mt-2 overflow-auto">{JSON.stringify(candidate.eligibilityRules, null, 2)}</pre></details><AuditDisclosure label="Reason facts and source lineage"><pre className="overflow-auto">{JSON.stringify(candidate.reasons, null, 2)}</pre><pre className="mt-2 overflow-auto">{JSON.stringify(candidate.source ?? null, null, 2)}</pre></AuditDisclosure><p className="mt-2 font-mono">Candidate ID: {candidate.candidateId}</p></li>)}</ul></AuditDisclosure>
      </div>
      <aside className="space-y-5"><Surface className="p-5"><SectionHeader title="Run history" /><ul className="mt-4 space-y-2">{history.map((run) => <li key={run.id}><button aria-pressed={selected.id === run.id} className={`w-full rounded-xl border p-3 text-left ${selected.id === run.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`} disabled={Boolean(working)} onClick={() => void inspect(run.id)}><span className="block text-sm font-medium">{run.portfolio.length} selected{selected.id === run.id ? ' · Selected' : ''}</span><span className="mt-1 flex items-center gap-1 text-xs text-ink/65"><Clock3 className="size-3" />{new Date(run.generatedAt).toLocaleString()}</span></button></li>)}</ul></Surface><AuditDisclosure label="Policy and run lineage"><p>{selected.algorithmVersion}</p>{Object.entries(selected.policyVersions).map(([key, value]) => <p key={key}>{key}: {value}</p>)}<p>Exclusive cutoff: {new Date(selected.cutoffAt).toLocaleString()}</p><p className="mt-2 font-mono">Analysis snapshot: {selected.analysisSnapshotId}</p><p className="font-mono">Output: {selected.outputHash}</p><p className="font-mono">Input: {selected.inputHash}</p></AuditDisclosure></aside>
    </div>}
  </div>
}

function Fact({ title, value }: { title: string; value: string }) { return <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">{title}</p><p className="mt-1 text-sm capitalize">{value}</p></div> }
