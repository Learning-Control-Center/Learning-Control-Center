import { Clock3, History, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiV2 } from '../../api'
import { AuditDisclosure, Button, EmptyState, ErrorState, LiveNotice, LoadingState, MutationError, PageHeader, ProvenanceNotice, SectionError, SectionHeader, StatusBadge, Surface } from '../../shared/components'
import type { CurriculumCatalogUnit, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection } from '../../shared/contracts/roadmapProjection'
import { paths } from '../../shared/navigation/paths'
import type { AnalysisHistoryItem, AnalysisSnapshot, CurrentAnalysis } from './model'

type ProfileReferenceCatalog = { versions: { domains?: { id?: string; title: string }[]; readinessGates?: { id: string; title: string }[] }[] }

const label = (value: string) => value.replaceAll('_', ' ')
const reasonLabels: Record<string, string> = {
  ACTIVE_DAY_DURATION_ABOVE_TARGET: 'Active-day duration is above the configured target',
  AGING_CRITICAL_TARGET: 'A critical target is aging and needs maintenance',
  ALLOCATION_DENOMINATOR_INSUFFICIENT: 'Not enough completed activity to evaluate allocation',
  AT_TARGET_QUALITY_WEAKNESS: 'The target level is met, but confidence, freshness, or review quality needs attention',
  BELOW_TARGET: 'Current capability is below the target level',
  CAPABILITY_GAP: 'Current capability is below the intended target',
  CAPABILITY_CONFIDENCE_LOW: 'Capability confidence is low',
  CAPABILITY_UNKNOWN: 'Current capability cannot be determined from available evidence',
  COMPLETED_WEEK_TARGET_VARIANCE: 'Completed-week activity differs from the configured target',
  CRITICAL_GATE_BLOCKED: 'A critical readiness gate blocks progress',
  CRITICAL_TARGET_UNASSESSED: 'A critical target has not been assessed',
  CURRICULUM_MISSING: 'No active Curriculum is available',
  DEADLINE_DUE: 'The target deadline is due',
  DEADLINE_DUE_SOON: 'The target deadline is approaching',
  DEADLINE_OVERDUE: 'The target deadline is overdue',
  DEADLINE_PRESSURE: 'A target deadline needs attention',
  DISCIPLINE_CONFIGURATION_MISSING: 'Discipline settings are unavailable',
  DISCIPLINE_VARIANCE: 'Recent discipline activity differs from its configured target',
  DISCIPLINE_ON_TARGET: 'Discipline activity is within its configured target',
  EVIDENCE_WEAKNESS: 'Evidence coverage or independence needs attention',
  IMPORTANT_SUPPORTING_EVIDENCE_WEAK: 'Important supporting evidence is weak or incomplete',
  LEARNING_GRAPH_MISSING: 'No active Learning Graph is available',
  MAINTENANCE_REMAINS: 'Maintenance work remains after reaching the target',
  MAINTENANCE_DUE: 'Capability maintenance is due',
  MEANINGFUL_ACTIVITY_NEGLECTED: 'No meaningful activity was recorded within the policy window',
  NEGLECT: 'Meaningful activity has been absent within the policy window',
  PARTIAL_REQUIRED_EVIDENCE: 'Required evidence is only partially satisfied',
  PREREQUISITE_NOT_MET: 'A required prerequisite is not met',
  PREREQUISITE_BLOCK: 'A prerequisite blocks progression',
  PREREQUISITE_UNKNOWN: 'A prerequisite state is Unknown',
  PROGRESSION_STALL: 'Progression has stalled',
  PROGRESSION_STALLED: 'Progression has stalled',
  READINESS_BLOCK: 'A readiness gate blocks progression',
  READINESS_GATE_NOT_MET: 'A readiness gate is not met',
  READINESS_GATE_UNKNOWN: 'A readiness gate is Unknown',
  REQUIRED_CAPABILITY_GAP: 'Current capability is below a required target',
  REQUIRED_EVIDENCE_CONTRADICTED: 'Required evidence is contradicted',
  REQUIRED_EVIDENCE_MISSING: 'Required evidence is missing',
  REQUIRED_INDEPENDENCE_MISSING: 'Required independent evidence is missing',
  REQUIRED_LEVEL_UNKNOWN: 'The required prerequisite level is Unknown',
  REQUIREMENT_FACT_MISSING: 'A required readiness fact is unavailable',
  REVIEW_DUE: 'A capability review is due',
  LOW_CONFIDENCE: 'Capability confidence is low',
  REVIEW_STATE_UNKNOWN: 'The prerequisite review state is Unknown',
  TARGET_PROFILE_MISSING: 'No active Target Profile is available',
  TARGET_SATISFIED: 'The target is satisfied',
  UNDER_ALLOCATION: 'Recent allocation is below the configured range',
  OVER_ALLOCATION: 'Recent allocation is above the configured range',
  WEEKLY_TARGET_UNKNOWN: 'The weekly discipline target cannot be evaluated',
  WORKLOAD_INSUFFICIENT_DATA: 'There is not enough activity data to evaluate workload',
  WORKLOAD_SURGE_DENOMINATOR_UNKNOWN: 'The workload comparison baseline is unavailable',
  WORKLOAD_WITHIN_TARGET: 'Workload is within the configured target',
  WORKLOAD_RISK: 'Recent workload needs attention',
}
const reasonLabel = (value: string) => reasonLabels[value] ?? 'Unknown — details unavailable'

export function AnalysisPage() {
  const [current, setCurrent] = useState<CurrentAnalysis | null>(null)
  const [history, setHistory] = useState<AnalysisHistoryItem[]>([])
  const [selected, setSelected] = useState<AnalysisSnapshot | null>(null)
  const [projection, setProjection] = useState<RoadmapProjection | null>(null)
  const [referenceTitles, setReferenceTitles] = useState<Map<string, string>>(new Map())
  const [referenceLinks, setReferenceLinks] = useState<Map<string, string>>(new Map())
  const [referenceError, setReferenceError] = useState('')
  const [loading, setLoading] = useState(true)
  const [inspecting, setInspecting] = useState('')
  const [generating, setGenerating] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setLoadError(''); setReferenceError('')
    try {
      const [currentResult, historyResult] = await Promise.all([
        apiV2<CurrentAnalysis>('/analysis/current', { signal }),
        apiV2<AnalysisHistoryItem[]>('/analysis/history', { signal }),
      ])
      setCurrent(currentResult); setHistory(historyResult); setSelected(currentResult.snapshot)
      void Promise.allSettled([
        apiV2<RoadmapProjection>('/roadmap-projection/current', { signal }),
        apiV2<ProfileReferenceCatalog[]>('/target-profiles', { signal }),
        apiV2<{ units: CurriculumCatalogUnit[] }>('/curricula/catalog/active', { signal }),
        apiV2<{ candidates: ProjectCatalogCandidateApi[] }>('/projects/catalog/current', { signal }),
      ]).then(([roadmapResult, profileResult, curriculumResult, projectResult]) => {
        if (signal?.aborted) return
        const references = new Map<string, string>([
          ['discipline:global', 'Discipline pattern'],
          ['analysis_scope:global', 'Analysis scope'],
          ['curriculum:active', 'Active Curriculum catalog'],
          ['learning_graph:active', 'Active Learning Graph'],
          ['target_profile:active', 'Active Target Profile'],
        ])
        const links = new Map<string, string>()
        if (roadmapResult.status === 'fulfilled') {
          setProjection(roadmapResult.value)
          const nodes = new Map((roadmapResult.value.nodes ?? []).map((node) => [node.id, node.title]))
          for (const edge of roadmapResult.value.edges ?? []) references.set(`learning_graph_edge:${edge.id}`, `${nodes.get(edge.source) ?? 'Earlier capability'} → ${nodes.get(edge.target) ?? 'Later capability'}`)
        }
        if (profileResult.status === 'fulfilled') for (const profile of profileResult.value) for (const version of profile.versions) {
          for (const domain of version.domains ?? []) if (domain.id) { references.set(`profile_domain:${domain.id}`, domain.title); links.set(`profile_domain:${domain.id}`, paths.profile) }
          for (const gate of version.readinessGates ?? []) { references.set(`readiness_gate:${gate.id}`, gate.title); links.set(`readiness_gate:${gate.id}`, paths.profile) }
        }
        if (curriculumResult.status === 'fulfilled') for (const unit of curriculumResult.value.units) {
          references.set(`curriculum_unit:${unit.unitDefinitionId}`, unit.title)
          links.set(`curriculum_unit:${unit.unitDefinitionId}`, `${paths.learn}/curricula/${unit.curriculumId}`)
        }
        if (projectResult.status === 'fulfilled') for (const task of projectResult.value.candidates) {
          references.set(`project_task:${task.task_definition_id}`, task.title)
          links.set(`project_task:${task.task_definition_id}`, `${paths.projects}/${task.project_id}`)
        }
        setReferenceTitles(references); setReferenceLinks(links)
        if ([roadmapResult, profileResult, curriculumResult, projectResult].some((item) => item.status === 'rejected')) setReferenceError('Some human subject references could not be loaded. Analysis facts and raw identifiers remain available.')
      })
    } catch (caught) {
      if ((caught as Error).name !== 'AbortError') setLoadError(caught instanceof ApiError ? caught.message : 'Analysis could not be loaded.')
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [])

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])

  const titles = useMemo(() => {
    const result = new Map<string, string>()
    for (const node of projection?.nodes ?? []) {
      result.set(node.id, node.title); result.set(node.semanticDefinitionId, node.title)
      for (const target of node.profileTargets ?? (node.profileTarget ? [node.profileTarget] : [])) {
        if (target.id) result.set(target.id, `${node.title} target`)
        if (target.identityId) result.set(target.identityId, `${node.title} target`)
      }
    }
    return result
  }, [projection])
  const isHistorical = Boolean(selected && current?.snapshot?.id !== selected.id)
  const snapshotReferences = useMemo(() => {
    const references = new Map<string, string>()
    for (const fact of selected?.normalizedFacts ?? []) {
      if (fact.fact_type !== 'target_state') continue
      const gates = Array.isArray(fact.payload.readinessGates) ? fact.payload.readinessGates : []
      for (const rawGate of gates) {
        if (!rawGate || typeof rawGate !== 'object') continue
        const gate = rawGate as Record<string, unknown>
        const gateId = typeof gate.gateId === 'string' ? gate.gateId : null
        const gateTitle = gateId ? referenceTitles.get(`readiness_gate:${gateId}`) ?? 'Readiness gate' : 'Readiness gate'
        if (gateId) references.set(`readiness_gate:${gateId}`, gateTitle)
        const predicates = Array.isArray(gate.predicates) ? gate.predicates : []
        for (const rawPredicate of predicates) {
          if (!rawPredicate || typeof rawPredicate !== 'object') continue
          const predicate = rawPredicate as Record<string, unknown>
          const predicateId = typeof predicate.predicate_id === 'string' ? predicate.predicate_id : null
          if (predicateId) references.set(`readiness_predicate:${predicateId}`, `${gateTitle} requirement`)
        }
      }
    }
    return references
  }, [referenceTitles, selected])
  const subjectTitle = (type: string, id: string) => {
    const title = titles.get(id) ?? snapshotReferences.get(`${type}:${id}`) ?? referenceTitles.get(`${type}:${id}`)
    if (!title) return 'Unknown subject — details unavailable'
    return isHistorical ? `${title} (current title)` : title
  }
  const subjectLink = (type: string, id: string) => type === 'competency' ? `${paths.profile}/competencies/${id}` : type === 'readiness_predicate' ? paths.profile : referenceLinks.get(`${type}:${id}`)

  const inspect = async (snapshotId: string) => {
    if (inspecting) return
    setInspecting(snapshotId); setLoadError('')
    try { setSelected(await apiV2<AnalysisSnapshot>(`/analysis/snapshots/${snapshotId}`)) }
    catch (caught) { setLoadError(caught instanceof ApiError ? caught.message : 'The Analysis snapshot could not be loaded.') }
    finally { setInspecting('') }
  }

  const generate = async () => {
    if (generating) return
    setGenerating(true); setMutationError(''); setNotice('')
    try {
      const snapshot = await apiV2<AnalysisSnapshot>('/analysis/runs', { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), purpose: 'learning_control' }) })
      setCurrent({ configured: true, status: 'current', snapshot })
      setSelected(snapshot)
      setHistory((items) => [historyItem(snapshot), ...items.filter((item) => item.id !== snapshot.id)])
      setNotice('A current deterministic Analysis snapshot was generated explicitly.')
      await load()
    } catch (caught) {
      setMutationError(caught instanceof ApiError ? caught.message : 'Analysis could not be generated.')
    } finally { setGenerating(false) }
  }

  const analysisAction = <Button disabled={generating} aria-busy={generating} onClick={() => void generate()}><RefreshCw className="size-4" />{generating ? 'Generating…' : current?.snapshot ? 'Generate current Analysis' : 'Generate Analysis'}</Button>

  if (loading) return <LoadingState label="Loading immutable Analysis history" />
  if (loadError && !current) return <div className="space-y-6"><PageHeader eyebrow="Deterministic diagnosis" title="Analysis" description="What the evidence-backed state says, without choosing work." actions={analysisAction} /><ErrorState message={loadError} retry={() => void load()} /></div>
  if (!current?.configured || !selected) return <div className="space-y-6"><LiveNotice>{notice}</LiveNotice><PageHeader eyebrow="Deterministic diagnosis" title="Analysis" description="What the evidence-backed state says, without choosing work." actions={analysisAction} />{mutationError ? <MutationError>{mutationError}</MutationError> : null}<EmptyState title="No Analysis V3 snapshot" detail="Analysis is generated explicitly. Reading or refreshing this page never recomputes it." /></div>

  const boundedCoverage = selected.completeness !== 'complete' || isHistorical || current.status !== 'current'
  return <div className="mx-auto w-full max-w-[90rem] space-y-6">
    <LiveNotice>{notice}</LiveNotice>
    <PageHeader eyebrow="Deterministic diagnosis" title="Analysis" description="Analysis V3 describes gaps, signals, completeness, and Unknown inputs. It does not choose work, change capability, or blend V1 Analytics." actions={analysisAction} />
    {mutationError ? <MutationError>{mutationError}</MutationError> : null}
    {loadError ? <SectionError message={loadError} retry={() => void load()} /> : null}
    {referenceError ? <SectionError message={referenceError} /> : null}
    {boundedCoverage ? <ProvenanceNotice title="Bounded diagnostic coverage"><p>{isHistorical ? 'This is an immutable historical snapshot; labels resolved from live catalogs are marked as current titles. ' : ''}{selected.completeness !== 'complete' ? `Completeness is ${label(selected.completeness)}; affected facts may be absent and Unknown must not be read as zero. ` : ''}{!isHistorical && current.status !== 'current' ? `Current status is ${label(current.status)}; generate a current Analysis before using it for a new decision.` : ''}</p></ProvenanceNotice> : null}
    <Surface className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4"><Fact label="Snapshot" value={isHistorical ? 'Historical snapshot' : label(current.status)} /><Fact label="Completeness" value={label(selected.completeness)} /><Fact label="Exclusive cutoff" value={new Date(selected.cutoffAt).toLocaleString()} /><Fact label="Completed through" value={selected.completedThroughDate} /></Surface>
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-5">
        <Surface className="p-5"><SectionHeader title="Diagnostic signals" description="Readable subjects lead; severity and reasons remain deterministic Analysis output." />{selected.signals.length ? <div className="mt-4 grid gap-3 md:grid-cols-2">{selected.signals.map((signal) => {
          const link = subjectLink(signal.subject.subjectType, signal.subject.subjectId)
          return <article className="rounded-2xl border border-ink/10 bg-white/70 p-4" key={signal.stableKey}><div className="flex items-start justify-between gap-3"><div>{link ? <Link className="font-semibold underline" to={link}>{subjectTitle(signal.subject.subjectType, signal.subject.subjectId)}</Link> : <p className="font-semibold">{subjectTitle(signal.subject.subjectType, signal.subject.subjectId)}</p>}<h3 className="mt-1 text-sm capitalize text-ink/70">{label(signal.type)}</h3></div><StatusBadge label={label(signal.severity)} tone={signal.severity === 'high' ? 'warning' : 'neutral'} /></div><p className="mt-3 text-sm text-ink/70">{signal.reasonCodes.map(reasonLabel).join(', ') || 'No reason code recorded.'}</p><AuditDisclosure><p>Subject type: {signal.subject.subjectType}</p><p>Subject ID: {signal.subject.subjectId}</p><p>Dimension: {signal.subject.dimensionKey ?? 'Overall'}</p><p>Reason codes: {signal.reasonCodes.join(', ') || 'None'}</p><pre className="mt-2 overflow-auto">{JSON.stringify(signal.decisiveFacts, null, 2)}</pre></AuditDisclosure></article>
        })}</div> : <p className="mt-4 text-sm text-ink/65">No diagnostic signals in this snapshot.</p>}</Surface>
        <Surface className="p-5"><SectionHeader title="Competency gaps" description="Target comparisons diagnose state; they do not rank or recommend work." />{selected.gaps.length ? <div className="mt-4 space-y-3">{selected.gaps.map((gap) => <article className="rounded-xl border border-ink/10 p-4" key={`${gap.targetIdentityId}:${gap.dimensionKey ?? 'overall'}`}><div className="flex flex-wrap items-center justify-between gap-2"><Link className="font-semibold underline" to={`${paths.profile}/competencies/${gap.competencyIdentityId}`}>{subjectTitle('competency', gap.competencyIdentityId)}</Link><StatusBadge label={`${label(gap.severity)} · ${label(gap.comparisonStatus)}`} tone={gap.comparisonStatus === 'unknown' ? 'unknown' : 'neutral'} /></div><p className="mt-2 text-sm text-ink/70">{gap.reasonCodes.map(reasonLabel).join(', ')}</p><AuditDisclosure label="Target and subject identifiers"><p>Target: {gap.targetIdentityId}</p><p>Competency: {gap.competencyIdentityId}</p><p>Dimension: {gap.dimensionKey ?? 'Overall'}</p><p>Reason codes: {gap.reasonCodes.join(', ') || 'None'}</p></AuditDisclosure></article>)}</div> : <p className="mt-4 text-sm text-ink/65">No target gaps are present.</p>}</Surface>
        {selected.unknownMarkers.length ? <Surface className="p-5"><SectionHeader title="Unknown inputs" description="Unknown remains an explicit state, not an error or zero." /><ul className="mt-4 space-y-2">{selected.unknownMarkers.map((marker) => <li className="rounded-xl bg-copper/5 p-3" key={`${marker.fieldPath}:${marker.subjectId}:${marker.reasonCode}`}><p className="font-medium">{subjectTitle(marker.subjectType, marker.subjectId)}</p><p className="mt-1 text-sm text-ink/70">{reasonLabel(marker.reasonCode)}</p><AuditDisclosure><p>Field: {marker.fieldPath}</p><p>Subject ID: {marker.subjectId}</p><p>Reason code: {marker.reasonCode}</p></AuditDisclosure></li>)}</ul></Surface> : null}
        <Surface className="p-5"><SectionHeader title="Normalized diagnostic facts" description="Frozen inputs are retained for audit, not promoted as new explanation." /><div className="mt-4 space-y-2">{selected.normalizedFacts.map((fact) => <AuditDisclosure key={fact.stable_key} label={`${label(fact.fact_type)} · ${subjectTitle(fact.subject_type, fact.subject_id)}`}><p>Subject ID: {fact.subject_id}</p><pre className="mt-2 overflow-auto">{JSON.stringify(fact.payload, null, 2)}</pre></AuditDisclosure>)}</div></Surface>
      </div>
      <aside className="space-y-5"><Surface className="p-5"><div className="mb-4 flex items-center gap-2"><History className="size-4 text-moss" /><h2 className="font-display text-lg font-semibold">Snapshot history</h2></div><ul className="space-y-2">{history.map((item) => <li key={item.id}><button aria-pressed={selected.id === item.id} className={`w-full rounded-xl border p-3 text-left ${selected.id === item.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`} disabled={Boolean(inspecting)} onClick={() => void inspect(item.id)}><span className="block text-sm font-medium capitalize">{label(item.purpose)}</span><span className="mt-1 flex items-center gap-1 text-xs text-ink/65"><Clock3 className="size-3" />{new Date(item.generatedAt).toLocaleString()}</span><span className="mt-1 block text-xs capitalize text-ink/65">{label(item.completeness)}{selected.id === item.id ? ' · Selected' : ''}</span></button></li>)}</ul></Surface><AuditDisclosure label="Policy and snapshot lineage"><p>{selected.lineage?.analysis_algorithm_version ?? 'Algorithm version Unknown'}</p><p>{selected.lineage?.analysis_policy_version ?? 'Policy version Unknown'}</p><p>{selected.lineage?.normalization_schema_version ?? 'Schema version Unknown'}</p><p className="mt-2 font-mono">Output {selected.outputHash}</p><p className="font-mono">Input {selected.inputHash}</p></AuditDisclosure></aside>
    </div>
  </div>
}

function Fact({ label: title, value }: { label: string; value: string }) { return <div><p className="eyebrow">{title}</p><p className="mt-2 text-sm font-medium capitalize">{value}</p></div> }

function historyItem(snapshot: AnalysisSnapshot): AnalysisHistoryItem {
  return {
    id: snapshot.id,
    runId: snapshot.runId,
    purpose: snapshot.purpose,
    generatedAt: snapshot.generatedAt,
    cutoffAt: snapshot.cutoffAt,
    completeness: snapshot.completeness,
    outputHash: snapshot.outputHash,
  }
}
