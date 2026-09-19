import { ArrowRight, Flag, History, Route, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiV2 } from '../../api'
import { CapabilitySummary, EmptyState, ErrorState, LoadingState, PageHeader, SectionError, SectionHeader, StatusBadge, Surface, UnknownValue } from '../../shared/components'
import { activityHandoffPath } from '../../shared/contracts/learningReferences'
import type { CurriculumCatalogUnit, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import { adaptProjectCatalogCandidate } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection, RoadmapProjectionNode } from '../../shared/contracts/roadmapProjection'
import { paths } from '../../shared/navigation/paths'
import type { Capability, CapabilityHistory, EvidenceRecord, ProfileSummary, ProfileVersion, SemanticDefinition } from './model'
import { projectionCapability, projectionTargetFor } from './model'

type Analysis = { configured: boolean; snapshot: { normalizedFacts: { fact_type: string; payload: { readinessGates?: { gateId: string; state: string }[] } }[] } | null }
type EvidenceList = { items: EvidenceRecord[] }
type RelatedData = { curriculumUnits: CurriculumCatalogUnit[]; projectCandidates: ReturnType<typeof adaptProjectCatalogCandidate>[] }
type DetailData = { capability: Capability | null; history: CapabilityHistory | null; definitions: SemanticDefinition[]; evidence: EvidenceRecord[] }

const gateTone = (state: string) => state === 'met' ? 'success' : state === 'not_met' ? 'critical' : 'unknown'
const titleCase = (value: string | null | undefined, fallback = 'Unknown') => value ? value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) : fallback

export function ProfileCapabilityPage() {
  const { competencyIdentityId } = useParams()
  const [profile, setProfile] = useState<ProfileSummary | null>(null)
  const [version, setVersion] = useState<ProfileVersion | null>(null)
  const [projection, setProjection] = useState<RoadmapProjection | null>(null)
  const [related, setRelated] = useState<RelatedData>({ curriculumUnits: [], projectCandidates: [] })
  const [relatedError, setRelatedError] = useState('')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [analysisError, setAnalysisError] = useState('')
  const [detail, setDetail] = useState<DetailData>({ capability: null, history: null, definitions: [], evidence: [] })
  const [detailErrors, setDetailErrors] = useState<string[]>([])
  const [detailLoading, setDetailLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(''); setRelatedError(''); setAnalysisError('')
    try {
      const [profiles, roadmap] = await Promise.all([
        apiV2<ProfileSummary[]>('/target-profiles', { signal }),
        apiV2<RoadmapProjection>('/roadmap-projection/current', { signal }),
      ])
      const activeProfile = profiles.find((item) => item.activeVersionId !== null) ?? null
      const activeVersion = activeProfile?.versions.find((item) => item.versionId === activeProfile.activeVersionId) ?? null
      setProfile(activeProfile); setVersion(activeVersion); setProjection(roadmap)
      void Promise.allSettled([
        apiV2<{ units: CurriculumCatalogUnit[] }>('/curricula/catalog/active', { signal }),
        apiV2<{ candidates: ProjectCatalogCandidateApi[] }>('/projects/catalog/current', { signal }),
      ]).then(([curricula, projects]) => {
        if (signal?.aborted) return
        setRelated({ curriculumUnits: curricula.status === 'fulfilled' ? curricula.value.units : [], projectCandidates: projects.status === 'fulfilled' ? projects.value.candidates.map(adaptProjectCatalogCandidate) : [] })
        const failed = [curricula, projects].filter((item) => item.status === 'rejected').length
        setRelatedError(failed ? `${failed === 2 ? 'Learning and Project' : 'Some related'} links could not be loaded. Profile and capability truth remain available.` : '')
      })
      void apiV2<Analysis>('/analysis/current', { signal }).then(setAnalysis).catch((caught) => {
        if ((caught as Error).name !== 'AbortError') setAnalysisError(caught instanceof ApiError ? caught.message : 'Readiness evaluation could not be loaded.')
      })
    } catch (caught) {
      if ((caught as Error).name !== 'AbortError') setError(caught instanceof ApiError ? caught.message : 'Profile and capability could not be loaded.')
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [])

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])
  const nodes = useMemo(() => (projection?.nodes ?? []) as RoadmapProjectionNode[], [projection])
  const nodeByCompetency = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const selectedNode = competencyIdentityId ? nodeByCompetency.get(competencyIdentityId) : undefined

  useEffect(() => {
    if (!competencyIdentityId || !selectedNode) { setDetail({ capability: null, history: null, definitions: [], evidence: [] }); setDetailErrors([]); setDetailLoading(false); return }
    const controller = new AbortController(); setDetailLoading(true); setDetailErrors([])
    void Promise.allSettled([
      apiV2<Capability>(`/capabilities/${competencyIdentityId}`, { signal: controller.signal }),
      apiV2<CapabilityHistory>(`/capabilities/${competencyIdentityId}/history?limit=20`, { signal: controller.signal }),
      apiV2<SemanticDefinition[]>(`/competencies/${competencyIdentityId}/definitions`, { signal: controller.signal }),
      apiV2<EvidenceList>(`/evidence?competency_identity_id=${encodeURIComponent(competencyIdentityId)}&limit=20`, { signal: controller.signal }),
    ]).then(([capability, history, definitions, evidence]) => {
      if (controller.signal.aborted) return
      setDetail({ capability: capability.status === 'fulfilled' ? capability.value : null, history: history.status === 'fulfilled' ? history.value : null, definitions: definitions.status === 'fulfilled' ? definitions.value : [], evidence: evidence.status === 'fulfilled' ? evidence.value.items : [] })
      setDetailErrors([capability.status === 'rejected' ? 'Current capability detail' : '', history.status === 'rejected' ? 'Capability history' : '', definitions.status === 'rejected' ? 'Competency definition' : '', evidence.status === 'rejected' ? 'Evidence history' : ''].filter(Boolean))
    }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [competencyIdentityId, selectedNode])

  const selectedTargets = version?.targets.filter((target) => !competencyIdentityId || target.competencyIdentityId === competencyIdentityId) ?? []
  const relatedUnits = selectedNode ? related.curriculumUnits.filter((unit) => unit.targets?.some((target) => target.semanticDefinitionId === selectedNode.semanticDefinitionId)) : []
  const relatedProjects = selectedNode ? related.projectCandidates.filter((candidate) => candidate.semanticDefinitionIds.includes(selectedNode.semanticDefinitionId)) : []
  const gateStates = useMemo(() => new Map((analysis?.snapshot?.normalizedFacts ?? []).flatMap((fact) => fact.fact_type === 'target_state' ? (fact.payload.readinessGates ?? []).map((gate) => [gate.gateId, gate.state] as const) : [])), [analysis])
  const activeDefinition = detail.definitions.find((item) => item.id === selectedNode?.semanticDefinitionId) ?? detail.definitions.at(-1)
  const criterionTitle = new Map((activeDefinition?.criteria ?? []).map((item) => [item.id, item.description || titleCase(item.stableKey)]))

  if (loading) return <LoadingState label="Loading target and capability context" />
  if (error) return <div className="space-y-6"><PageHeader eyebrow="Intent and Evidence-derived capability" title={competencyIdentityId ? 'Competency detail' : 'Profile'} description="Current and target context could not be loaded." /><ErrorState message={error} retry={() => void load()} /></div>
  if (!profile || !version || !projection?.configured) return <div className="space-y-6"><PageHeader eyebrow="Intent and Evidence-derived capability" title="Profile" description="Where current Evidence-derived capability meets immutable target intent." /><EmptyState title="No active Target Profile" detail="Activate an immutable Target Profile version to expose target intent and Evidence-derived capability." /></div>
  if (competencyIdentityId && !selectedNode) return <div className="space-y-6"><PageHeader eyebrow="Capability detail" title="Competency unavailable" description="This competency is not present in the active learning journey." /><EmptyState title="No current competency context" detail="The requested reference is not a target or prerequisite-path node in the active projection." action={<Link className="button-secondary" to={paths.profile}>Back to Profile</Link>} /></div>

  return <div className="mx-auto w-full max-w-[86rem] space-y-6">
    <PageHeader eyebrow="Intent and Evidence-derived capability" title={selectedNode?.title ?? (competencyIdentityId ? 'Competency detail' : 'Profile')} description={`${version.title} · immutable version ${version.version}. Targets describe intent; capability remains independently evaluated from Evidence.`} actions={competencyIdentityId ? <Link className="button-secondary" to={paths.profile}>All Profile targets</Link> : undefined} />
    {!competencyIdentityId ? <DomainOverview version={version} nodeByCompetency={nodeByCompetency} /> : null}
    <section aria-labelledby="profile-targets-heading"><SectionHeader headingId="profile-targets-heading" title={competencyIdentityId ? 'Current versus target' : 'Current capability by target'} description={selectedTargets.length ? 'Each row keeps target intent separate from the current Evidence-derived capability state.' : 'This competency supports the learning path but is not a direct Profile target.'} />{selectedTargets.length ? <div className="mt-4 grid gap-4 lg:grid-cols-2">{selectedTargets.map((target) => {
      const node = nodeByCompetency.get(target.competencyIdentityId)
      const state = projectionCapability(node, target.dimensionKey)
      const projectedTarget = projectionTargetFor(node, target)
      const domain = version.domains?.find((item) => item.stableKey === target.domainStableKey)
      return <article key={target.id} className="surface p-5"><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wide text-ink/65">{domain?.title ?? node?.profileDomain?.title ?? 'Profile domain unavailable'}</p><h2 className="mt-1 font-display text-xl font-semibold">{node?.title ?? 'Competency title unavailable'}</h2></div><ShieldCheck className="size-5 text-fern" aria-hidden="true" /></div><dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-ink/65">Target level</dt><dd className="font-medium">{projectedTarget?.targetLevelTitle ?? titleCase(projectedTarget?.targetLevelKey ?? target.targetLevelStableKey)}</dd></div><div><dt className="text-ink/65">Scope</dt><dd className="font-medium">{projectedTarget?.dimensionTitle ?? titleCase(target.dimensionKey, 'Overall')}</dd></div><div><dt className="text-ink/65">Priority</dt><dd><StatusBadge label={titleCase(target.priority)} tone={target.priority === 'core' ? 'info' : 'neutral'} /></dd></div><div><dt className="text-ink/65">Current capability</dt><dd>{state ? state.levelTitle ?? titleCase(state.capabilityLevelKey, titleCase(state.assessmentStatus)) : <UnknownValue />}</dd></div></dl><div className="mt-4"><CapabilitySummary state={state} /></div><div className="mt-4 flex flex-wrap gap-2">{!competencyIdentityId ? <Link className="button-secondary" to={paths.competency(target.competencyIdentityId)}>View competency <ArrowRight className="size-4" /></Link> : null}<Link className="button-secondary" to={`${paths.roadmap}?focus=${encodeURIComponent(target.competencyIdentityId)}`}>View on Roadmap</Link><Link className="button-primary" to={activityHandoffPath({ origin: 'profile', returnTo: paths.competency(target.competencyIdentityId), reference: { kind: 'competency', competencyIdentityId: target.competencyIdentityId, semanticDefinitionId: node?.semanticDefinitionId, title: node?.title ?? 'Profile competency' } })}>Log related work</Link></div></article>
    })}</div> : selectedNode ? <Surface className="mt-4 p-5"><p className="text-sm text-ink/65">{selectedNode.title} is a prerequisite or supporting capability in the active journey. It has no direct target level, priority, or deadline in this Profile.</p><div className="mt-4"><CapabilitySummary state={projectionCapability(selectedNode, null) ?? (selectedNode.capability.scopes[0] ? { ...selectedNode.capability.scopes[0], aggregateConfidence: selectedNode.capability.scopes[0].confidence, capabilityLevelKey: selectedNode.capability.scopes[0].levelKey ?? null, dimensionKey: null } : null)} /></div></Surface> : null}</section>
    {competencyIdentityId && selectedNode ? <CompetencyDetail node={selectedNode} detail={detail} loading={detailLoading} errors={detailErrors} definition={activeDefinition} criterionTitle={criterionTitle} related={related} relatedError={relatedError} relatedUnits={relatedUnits} relatedProjects={relatedProjects} reload={load} /> : null}
    {!competencyIdentityId ? <MilestonesAndGates version={version} gateStates={gateStates} analysisError={analysisError} reload={load} /> : null}
  </div>
}

function DomainOverview({ version, nodeByCompetency }: { version: ProfileVersion; nodeByCompetency: Map<string, RoadmapProjectionNode> }) {
  return <Surface className="p-5"><SectionHeader title="Target horizon and domains" description={`${version.targetHorizon ? `Horizon: ${version.targetHorizon}. ` : ''}Domains group intent; they do not create capability or imply rank.`} /><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{(version.domains ?? []).sort((a, b) => a.orderIndex - b.orderIndex).map((domain) => { const targets = version.targets.filter((target) => target.domainStableKey === domain.stableKey); const attention = targets.filter((target) => { const state = projectionCapability(nodeByCompetency.get(target.competencyIdentityId), target.dimensionKey); return !state || state.reviewDue || ['unknown', 'unassessed'].includes(state.assessmentStatus) }).length; return <article key={domain.stableKey} className="rounded-xl border border-ink/10 p-4"><h3 className="font-semibold">{domain.title}</h3><p className="mt-1 text-sm text-ink/65">{domain.description || 'No description.'}</p><p className="mt-3 text-xs text-ink/65">{targets.length} targets · {attention ? `${attention} need evidence or review` : 'current states available'}</p>{domain.minimumPercent != null || domain.maximumPercent != null ? <p className="mt-1 text-xs text-ink/65">Allocation context: {domain.minimumPercent ?? 0}%–{domain.maximumPercent ?? 100}%</p> : null}</article> })}</div></Surface>
}

function CompetencyDetail({ node, detail, loading, errors, definition, criterionTitle, relatedError, relatedUnits, relatedProjects, reload }: { node: RoadmapProjectionNode; detail: DetailData; loading: boolean; errors: string[]; definition?: SemanticDefinition; criterionTitle: Map<string, string>; related: RelatedData; relatedError: string; relatedUnits: CurriculumCatalogUnit[]; relatedProjects: RelatedData['projectCandidates']; reload: (signal?: AbortSignal) => Promise<void> }) {
  const runOrder = new Map((detail.history?.runs ?? []).map((run, index) => [run.id, index]))
  const runById = new Map((detail.history?.runs ?? []).map((run) => [run.id, run]))
  const criterionHistory = [...(detail.history?.criterionResults ?? [])].sort((a, b) => (runOrder.get(a.runId) ?? Number.MAX_SAFE_INTEGER) - (runOrder.get(b.runId) ?? Number.MAX_SAFE_INTEGER) || a.id.localeCompare(b.id))
  return <>{loading ? <LoadingState label="Loading Evidence and capability history" /> : null}{errors.length ? <SectionError message={`${errors.join(', ')} could not be loaded. Available sections remain authoritative.`} retry={() => void reload()} /> : null}
    <div className="grid gap-5 lg:grid-cols-2"><Surface className="p-5"><SectionHeader title="Capability by scope" description="Current state, confidence, freshness, and review need are evaluated independently for each scope." />{detail.capability?.states.length ? <ul className="mt-4 space-y-3">{detail.capability.states.map((state) => { const projected = node.capability.scopes.find((scope) => scope.scopeKey === state.scopeKey); return <li key={state.scopeKey}><CapabilitySummary state={{ ...state, levelTitle: state.levelTitle ?? projected?.levelTitle ?? null }} /><p className="mt-2 text-xs text-ink/65">{state.reviewReasons?.length ? `Review reasons: ${state.reviewReasons.map((item) => titleCase(item)).join(', ')}` : 'No current review reason.'}{state.lastMeaningfulEvidenceAt ? ` Last meaningful Evidence: ${new Date(state.lastMeaningfulEvidenceAt).toLocaleString()}.` : ' Last meaningful Evidence is Unknown.'}</p></li> })}</ul> : <UnknownValue reason="current capability could not be evaluated or loaded" />}</Surface><Surface className="p-5"><SectionHeader title="Criterion result history" description="Dated results are linked to immutable capability evaluation runs; they are not presented as current state." />{criterionHistory.length ? <ul className="mt-4 space-y-3">{criterionHistory.slice(0, 12).map((item) => { const run = runById.get(item.runId); return <li className="rounded-xl border border-ink/10 p-3" key={item.id}><p className="font-medium">{criterionTitle.get(item.criterionDefinitionId) ?? 'Criterion from historical definition'}</p><p className="mt-1 text-sm text-ink/65">{titleCase(item.state)} · {item.decisiveEvidenceIds.length} decisive Evidence record{item.decisiveEvidenceIds.length === 1 ? '' : 's'}</p><p className="mt-1 text-xs text-ink/65">{run ? `Evaluation cutoff ${new Date(run.cutoffAt).toLocaleString()}` : 'Evaluation date Unknown'} · run {item.runId}</p></li> })}</ul> : <p className="mt-4 text-sm text-ink/65">No criterion evaluation history is available.</p>}</Surface></div>
    <div className="grid gap-5 lg:grid-cols-2"><EvidenceHistory evidence={detail.evidence} competencyIdentityId={node.id} /><CapabilityHistoryList history={detail.history} /></div>
    <Surface className="p-5"><SectionHeader title="Related learning and outcome work" description="These links use explicit semantic-definition associations. They do not derive capability or rank options." />{relatedError ? <div className="mt-4"><SectionError message={relatedError} /></div> : null}<div className="mt-4 grid gap-4 md:grid-cols-2"><div><h3 className="font-semibold">Learn</h3>{relatedUnits.length ? <ul className="mt-2 space-y-2">{relatedUnits.map((unit) => <li key={unit.unitDefinitionId}><Link className="button-secondary w-full justify-between" to={paths.curriculum(unit.curriculumId)}>{unit.title}<ArrowRight className="size-4" /></Link></li>)}</ul> : <p className="mt-2 text-sm text-ink/65">No active Curriculum action declares this competency.</p>}</div><div><h3 className="font-semibold">Projects</h3>{relatedProjects.length ? <ul className="mt-2 space-y-2">{relatedProjects.map((candidate) => <li key={candidate.taskDefinitionId}><Link className="button-secondary w-full justify-between" to={paths.project(candidate.projectId)}>{candidate.title}<ArrowRight className="size-4" /></Link></li>)}</ul> : <p className="mt-2 text-sm text-ink/65">No active Project task declares this competency.</p>}</div></div><div className="mt-4 flex flex-wrap gap-2"><Link className="button-secondary" to={`${paths.roadmap}?focus=${encodeURIComponent(node.id)}`}><Route className="size-4" />Focus on Roadmap</Link><Link className="button-primary" to={activityHandoffPath({ origin: 'profile', returnTo: paths.competency(node.id), reference: { kind: 'competency', competencyIdentityId: node.id, semanticDefinitionId: node.semanticDefinitionId, title: node.title } })}>Log related work</Link></div></Surface>
    <details className="surface p-5"><summary className="cursor-pointer font-semibold">Technical lineage</summary><dl className="mt-4 grid gap-2 break-all text-xs text-ink/65 sm:grid-cols-2"><div><dt className="font-semibold">Competency identity</dt><dd>{node.id}</dd></div><div><dt className="font-semibold">Semantic definition</dt><dd>{node.semanticDefinitionId}</dd></div><div><dt className="font-semibold">Definition version</dt><dd>{definition?.definitionVersion ?? 'Unknown'}</dd></div><div><dt className="font-semibold">Evaluation policy</dt><dd>{detail.history?.runs[0]?.capabilityPolicyVersion ?? 'Unknown'}</dd></div><div><dt className="font-semibold">Evidence policy</dt><dd>{detail.history?.runs[0]?.evidencePolicyVersion ?? 'Unknown'}</dd></div><div><dt className="font-semibold">Output hash</dt><dd>{detail.history?.runs[0]?.outputHash ?? 'Unknown'}</dd></div></dl></details>
  </>
}

function EvidenceHistory({ evidence, competencyIdentityId }: { evidence: EvidenceRecord[]; competencyIdentityId: string }) {
  return <Surface className="p-5"><SectionHeader title="Evidence history" description="Evidence record lifecycle and its competency attribution are shown separately; neither implies completion or target attainment." />{evidence.length ? <ol className="mt-4 space-y-3">{evidence.map((item) => { const matchingLinks = item.links?.filter((link) => link.competencyIdentityId === competencyIdentityId) ?? []; const hasActiveAttribution = matchingLinks.some((link) => !link.retracted); return <li className="rounded-xl border border-ink/10 p-4" key={item.id}><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-semibold">{item.title}</p><div className="flex flex-wrap gap-2"><StatusBadge label={item.invalidated ? 'Evidence invalidated' : item.retracted ? 'Evidence retracted' : 'Evidence record active'} tone={item.invalidated || item.retracted ? 'warning' : 'success'} /><StatusBadge label={hasActiveAttribution ? 'Competency attribution active' : 'Competency attribution retracted'} tone={hasActiveAttribution ? 'info' : 'warning'} /></div></div><p className="mt-1 text-sm text-ink/65">{titleCase(item.evidenceType)} · strength {titleCase(item.strength, 'Unknown')} · independence {titleCase(item.independence, 'Unknown')}</p><p className="mt-1 text-xs text-ink/65">{item.occurredAt ? new Date(item.occurredAt).toLocaleString() : `Occurred at Unknown${item.occurredAtUnknownReason ? ` — ${titleCase(item.occurredAtUnknownReason)}` : ''}`}</p></li> })}</ol> : <p className="mt-4 text-sm text-ink/65">No active or historical Evidence records are available for this competency.</p>}</Surface>
}

function CapabilityHistoryList({ history }: { history: CapabilityHistory | null }) {
  return <Surface className="p-5"><SectionHeader title="Capability history" description="Append-only evaluations explain lifecycle changes without rewriting prior results." />{history?.runs.length ? <ol className="mt-4 space-y-3">{history.runs.slice(0, 8).map((run) => <li className="rounded-xl border border-ink/10 p-4" key={run.id}><p className="inline-flex items-center gap-2 font-semibold"><History className="size-4 text-moss" />{titleCase(run.assessmentStatus)} · confidence {titleCase(run.aggregateConfidence)}</p><p className="mt-1 text-xs text-ink/65">Scope {titleCase(run.scopeKey)} · cutoff {new Date(run.cutoffAt).toLocaleString()} · {run.decisiveEvidenceIds.length} decisive Evidence record{run.decisiveEvidenceIds.length === 1 ? '' : 's'}</p>{run.reasons.length ? <p className="mt-1 text-xs text-ink/65">Reasons: {run.reasons.map((item) => titleCase(item)).join(', ')}</p> : null}</li>)}</ol> : <p className="mt-4 text-sm text-ink/65">No capability evaluation runs are available.</p>}</Surface>
}

function MilestonesAndGates({ version, gateStates, analysisError, reload }: { version: ProfileVersion; gateStates: Map<string, string>; analysisError: string; reload: (signal?: AbortSignal) => Promise<void> }) {
  return <div className="grid gap-5 lg:grid-cols-2"><Surface className="p-5"><SectionHeader title="Milestones" description="Named waypoints from the active Profile; they are not completion scores." />{version.milestones?.length ? <ol className="mt-4 space-y-3">{[...version.milestones].sort((a,b) => a.orderIndex - b.orderIndex).map((item) => <li key={item.id} className="rounded-xl border border-ink/10 p-4"><p className="flex items-center gap-2 font-semibold"><Flag className="size-4 text-moss" />{item.title}</p><p className="mt-1 text-sm text-ink/65">{item.description || 'No description.'}</p><p className="mt-2 text-xs text-ink/65">{item.targetStableKeys.length} linked targets{item.targetDate ? ` · target ${item.targetDate}` : ''}</p></li>)}</ol> : <p className="mt-4 text-sm text-ink/65">No milestones are defined in this Profile version.</p>}</Surface><Surface className="p-5"><SectionHeader title="Readiness gates" description="Current Analysis evaluates these declared gates. Missing or incomplete inputs stay Unknown." />{analysisError ? <div className="mt-4"><SectionError message={analysisError} retry={() => void reload()} /></div> : version.readinessGates?.length ? <ul className="mt-4 space-y-3">{[...version.readinessGates].sort((a,b) => a.orderIndex - b.orderIndex).map((gate) => { const state = gateStates.get(gate.id) ?? 'unknown'; return <li key={gate.id} className="rounded-xl border border-ink/10 p-4"><div className="flex items-center justify-between gap-3"><p className="font-semibold">{gate.title}</p><StatusBadge label={titleCase(state)} tone={gateTone(state)} /></div><p className="mt-2 text-xs text-ink/65">Effect: {titleCase(gate.effect)}{gate.milestoneStableKey ? ' · linked milestone' : ''}</p></li> })}</ul> : <p className="mt-4 text-sm text-ink/65">No readiness gates are defined in this Profile version.</p>}</Surface></div>
}
