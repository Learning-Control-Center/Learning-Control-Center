import { BookOpen, CheckCircle2, Clock3, Route } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiV2, formatDuration } from '../../api'
import { Button, Dialog, EmptyState, ErrorState, LiveNotice, LoadingState, MutationError, PageHeader, SectionError, SectionHeader, StatusBadge, Surface, UnknownValue } from '../../shared/components'
import { activityHandoffPath } from '../../shared/contracts/learningReferences'
import type { CurriculumAvailability, CurriculumCatalogUnit } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection } from '../../shared/contracts/roadmapProjection'
import { paths } from '../../shared/navigation/paths'
import type { Catalog, Curriculum, CurriculumVersion, LearningGroup } from './model'
import { learningGroup } from './model'

const stateTone = (state: string) => state === 'met' ? 'success' : state === 'not_met' ? 'critical' : 'unknown'
const groupCopy: Record<LearningGroup, { title: string; description: string }> = {
  available: { title: 'Available to learn', description: 'Requirements are met at this cutoff. This is availability, not a recommendation or capability result.' },
  blocked: { title: 'Blocked', description: 'At least one declared requirement is not met. The reasons below are authoritative Curriculum availability facts.' },
  unknown: { title: 'Availability unknown', description: 'Required inputs are missing, incomplete, or could not be loaded. Unknown is not treated as available.' },
}

export function CurriculumPage() {
  const { curriculumId } = useParams()
  const navigate = useNavigate()
  const [curricula, setCurricula] = useState<Curriculum[]>([])
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [projection, setProjection] = useState<RoadmapProjection | null>(null)
  const [relatedError, setRelatedError] = useState('')
  const [selectedId, setSelectedId] = useState(curriculumId ?? '')
  const [versionsByCurriculum, setVersionsByCurriculum] = useState<Record<string, CurriculumVersion[]>>({})
  const [versionsError, setVersionsError] = useState('')
  const [availability, setAvailability] = useState<Record<string, CurriculumAvailability>>({})
  const [availabilityErrors, setAvailabilityErrors] = useState<Record<string, string>>({})
  const [availabilityLoading, setAvailabilityLoading] = useState(false)
  const [notice, setNotice] = useState('')
  const [activationCandidate, setActivationCandidate] = useState<CurriculumVersion | null>(null)
  const [mutationError, setMutationError] = useState('')
  const [activating, setActivating] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(''); setVersionsError(''); setRelatedError('')
    try {
      const [items, activeCatalog] = await Promise.all([
        apiV2<Curriculum[]>('/curricula', { signal }),
        apiV2<Catalog>('/curricula/catalog/active', { signal }),
      ])
      setCurricula(items); setCatalog(activeCatalog)
      setSelectedId((current) => { const requested = curriculumId ?? current; return items.some((item) => item.id === requested) ? requested : curriculumId ? '' : (items[0]?.id ?? '') })
      void Promise.allSettled(items.map((item) => apiV2<CurriculumVersion[]>(`/curricula/${item.id}/versions`, { signal }).then((versions) => [item.id, versions] as const))).then((results) => {
        if (signal?.aborted) return
        setVersionsByCurriculum(Object.fromEntries(results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])))
        if (results.some((result) => result.status === 'rejected')) setVersionsError('Some Curriculum version titles or management history could not be loaded.')
      })
      void apiV2<RoadmapProjection>('/roadmap-projection/current', { signal }).then(setProjection).catch((caught) => {
        if ((caught as Error).name !== 'AbortError') setRelatedError('Related competency links could not be loaded. Learning availability remains available.')
      })
    } catch (caught) {
      if ((caught as Error).name !== 'AbortError') setError(caught instanceof ApiError ? caught.message : 'Curriculum could not be loaded.')
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [curriculumId])

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])
  const selected = curricula.find((item) => item.id === selectedId)
  const versions = versionsByCurriculum[selectedId] ?? []
  const activeVersion = versions.find((item) => item.id === selected?.activeVersionId)
  const units = useMemo(() => (catalog?.units ?? []).filter((unit) => unit.curriculumId === selectedId).sort((a, b) => (a.orderIndex ?? 0) - (b.orderIndex ?? 0)), [catalog, selectedId])

  useEffect(() => {
    const controller = new AbortController()
    setAvailability({}); setAvailabilityErrors({}); setNotice('')
    if (!units.length) { setAvailabilityLoading(false); return () => controller.abort() }
    setAvailabilityLoading(true)
    void Promise.allSettled(units.map((unit) => apiV2<CurriculumAvailability>(`/curricula/units/${unit.unitDefinitionId}/availability`, { signal: controller.signal }).then((result) => [unit.unitDefinitionId, result] as const))).then((results) => {
      if (controller.signal.aborted) return
      setAvailability(Object.fromEntries(results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])))
      setAvailabilityErrors(Object.fromEntries(results.flatMap((result, index) => result.status === 'rejected' ? [[units[index].unitDefinitionId, result.reason instanceof ApiError ? result.reason.message : 'Availability could not be loaded.']] : [])))
      setNotice(`Availability loaded for ${results.filter((result) => result.status === 'fulfilled').length} of ${units.length} learning actions.`)
    }).finally(() => { if (!controller.signal.aborted) setAvailabilityLoading(false) })
    return () => controller.abort()
  }, [units])

  const titleByCurriculum = useMemo(() => Object.fromEntries(curricula.map((item) => {
    const versions = versionsByCurriculum[item.id] ?? []
    return [item.id, versions.find((version) => version.id === item.activeVersionId)?.title ?? versions.at(-1)?.title ?? 'Curriculum title unavailable']
  })), [curricula, versionsByCurriculum])
  const competencyBySemantic = useMemo(() => new Map((projection?.nodes ?? []).map((node) => [node.semanticDefinitionId, node])), [projection])
  const grouped = useMemo(() => ({
    available: units.filter((unit) => learningGroup(availability[unit.unitDefinitionId]) === 'available'),
    blocked: units.filter((unit) => learningGroup(availability[unit.unitDefinitionId]) === 'blocked'),
    unknown: units.filter((unit) => learningGroup(availability[unit.unitDefinitionId]) === 'unknown'),
  }), [availability, units])

  const selectCurriculum = (id: string) => { setSelectedId(id); if (curriculumId) void navigate(paths.curriculum(id)) }
  const activate = async () => {
    if (!selected || !activationCandidate) return
    setActivating(true); setMutationError('')
    try {
      await apiV2(`/curricula/${selected.id}/versions/${activationCandidate.id}/activate`, { method: 'POST', body: JSON.stringify({ reason: `Activate ${activationCandidate.title} from Learn after explicit confirmation`, source: 'user', idempotency_key: crypto.randomUUID() }) })
      const activated = activationCandidate.title; setActivationCandidate(null); setNotice(`${activated} activated. Learning availability will refresh.`); await load()
    } catch (caught) { setMutationError(caught instanceof ApiError ? caught.message : 'Curriculum activation failed.') }
    finally { setActivating(false) }
  }

  if (loading) return <LoadingState label="Loading canonical learning options" />
  if (error && !curricula.length) return <div className="space-y-6"><PageHeader eyebrow="Canonical learning options" title="Learn" description="Authored learning actions and their current availability." /><ErrorState message={error} retry={() => void load()} /></div>
  if (!curricula.length) return <div className="space-y-6"><PageHeader eyebrow="Canonical learning options" title="Learn" description="Authored learning actions and their current availability." /><EmptyState title="No Curriculum yet" detail="Create and activate an immutable Curriculum version through the V2 API to expose learning actions here." /></div>
  if (curriculumId && !selected) return <div className="space-y-6"><PageHeader eyebrow="Canonical learning options" title="Curriculum unavailable" description="The requested Curriculum is not present." /><EmptyState title="No matching Curriculum" detail="Choose an available Curriculum from Learn." action={<Link className="button-secondary" to={paths.learn}>Back to Learn</Link>} /></div>

  return <div className="mx-auto w-full max-w-[86rem] space-y-6">
    <LiveNotice>{notice}</LiveNotice>
    <PageHeader eyebrow="Canonical learning options" title={curriculumId ? (activeVersion?.title ?? titleByCurriculum[selectedId]) : 'Learn'} description="Actions remain in canonical authored order within each availability state. This page does not infer a best, next, or recommended action." actions={curriculumId ? <Link className="button-secondary" to={paths.learn}>All Curricula</Link> : undefined} />
    {error ? <ErrorState message={error} retry={() => void load()} /> : null}
    <div className="grid gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
      <Surface className="h-fit p-5"><SectionHeader title="Curricula" /><ul className="mt-4 space-y-2">{curricula.map((item) => <li key={item.id}><button aria-pressed={selectedId === item.id} className={`w-full rounded-xl border p-3 text-left ${selectedId === item.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`} onClick={() => selectCurriculum(item.id)} type="button"><span className="font-medium">{titleByCurriculum[item.id]}</span><span className="mt-1 block text-xs text-ink/65">{selectedId === item.id ? 'Selected · ' : ''}{item.activeVersionId ? 'Active immutable version' : 'No active version'}</span></button></li>)}</ul></Surface>
      <div className="space-y-6">
        <Surface className="p-5"><SectionHeader title={activeVersion?.title ?? titleByCurriculum[selectedId]} description={activeVersion?.description || 'Activate a version to expose its canonically ordered actions.'} actions={<StatusBadge label={selected?.activeVersionId ? 'Active version' : 'Setup required'} tone={selected?.activeVersionId ? 'success' : 'warning'} />} /></Surface>
        {relatedError ? <SectionError message={relatedError} /> : null}
        {!activeVersion ? <EmptyState title="No active Curriculum version" detail="Activate an immutable Curriculum version in management below to expose learning actions." /> : <>{availabilityLoading ? <LoadingState label="Evaluating learning availability" /> : null}{(['available', 'blocked', 'unknown'] as const).map((group) => <LearningSection key={group} group={group} units={grouped[group]} availability={availability} availabilityErrors={availabilityErrors} competencyBySemantic={competencyBySemantic} />)}</>}
        <details className="surface p-5"><summary className="cursor-pointer font-semibold">Curriculum version management</summary><p className="mt-3 text-sm text-ink/65">Activation atomically advances the active immutable version. Prior versions remain historical; current availability and downstream projections then use the new version.</p>{versionsError ? <div className="mt-3"><SectionError message={versionsError} /></div> : <ul className="mt-4 space-y-2">{versions.map((version) => <li className="rounded-xl border border-ink/10 bg-white/60 p-3" key={version.id}><p className="text-sm font-medium">v{version.version} · {version.title}</p><p className="mt-1 text-xs text-ink/65">Effective {new Date(version.effectiveAt).toLocaleString()}</p>{selected?.activeVersionId === version.id ? <span className="mt-2 inline-flex items-center gap-1 text-xs text-moss"><CheckCircle2 className="size-3" />Active</span> : <Button variant="secondary" className="mt-2 text-xs" onClick={() => { setMutationError(''); setActivationCandidate(version) }}>Review activation</Button>}</li>)}</ul>}</details>
      </div>
    </div>
    <Dialog open={Boolean(activationCandidate)} label="Confirm Curriculum activation" onDismiss={() => !activating && setActivationCandidate(null)} className="absolute left-1/2 top-1/2 max-h-[calc(100dvh-1rem)] w-[min(92vw,34rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"><h2 className="font-display text-2xl font-semibold">Activate {activationCandidate?.title}?</h2><p className="mt-3 text-sm leading-6 text-ink/65">This atomically moves the active pointer forward to immutable version {activationCandidate?.version}. Current Curriculum availability and downstream projections will use that version; prior versions remain historical.</p>{mutationError ? <div className="mt-4"><MutationError>{mutationError}</MutationError></div> : null}<div className="mt-6 flex justify-end gap-2"><Button variant="secondary" disabled={activating} onClick={() => setActivationCandidate(null)}>Cancel</Button><Button disabled={activating} onClick={() => void activate()}>{activating ? 'Activating…' : 'Activate version'}</Button></div></Dialog>
  </div>
}

function LearningSection({ group, units, availability, availabilityErrors, competencyBySemantic }: { group: LearningGroup; units: CurriculumCatalogUnit[]; availability: Record<string, CurriculumAvailability>; availabilityErrors: Record<string, string>; competencyBySemantic: Map<string, NonNullable<RoadmapProjection['nodes']>[number]> }) {
  const copy = groupCopy[group]
  return <section aria-labelledby={`learning-${group}-heading`}><SectionHeader headingId={`learning-${group}-heading`} title={copy.title} description={copy.description} />{units.length ? <ol className="mt-4 grid gap-4 xl:grid-cols-2">{units.map((unit, index) => <li key={unit.unitDefinitionId}><LearningCard unit={unit} authoredIndex={index + 1} readiness={availability[unit.unitDefinitionId]} error={availabilityErrors[unit.unitDefinitionId]} competencyBySemantic={competencyBySemantic} /></li>)}</ol> : <p className="mt-3 text-sm text-ink/65">No actions in this state.</p>}</section>
}

function LearningCard({ unit, authoredIndex, readiness, error, competencyBySemantic }: { unit: CurriculumCatalogUnit; authoredIndex: number; readiness?: CurriculumAvailability; error?: string; competencyBySemantic: Map<string, NonNullable<RoadmapProjection['nodes']>[number]> }) {
  const related = (unit.targets ?? []).map((target) => ({ target, node: competencyBySemantic.get(target.semanticDefinitionId) }))
  return <article className="surface h-full p-5"><div className="flex items-center justify-between gap-3"><span className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-moss"><BookOpen className="size-4" />Authored action {authoredIndex} · {unit.kind.replaceAll('_', ' ')}</span>{unit.status ? <StatusBadge label={unit.status} /> : null}</div><h3 className="mt-3 font-display text-xl font-semibold">{unit.title}</h3><p className="mt-2 text-sm leading-6 text-ink/65">{unit.description || 'No description.'}</p>{unit.action?.instructions ? <p className="mt-3 rounded-xl bg-ink/5 p-3 text-sm">{unit.action.instructions}</p> : null}<div className="mt-4 flex flex-wrap gap-2 text-xs text-ink/65"><span className="inline-flex items-center gap-1"><Clock3 className="size-3" />{unit.durationRangeMs ? formatDuration(unit.durationRangeMs[1]) : 'Duration Unknown'}</span><span>{unit.evidenceOpportunities.length ? `${unit.evidenceOpportunities.length} Evidence opportunit${unit.evidenceOpportunities.length === 1 ? 'y' : 'ies'}` : 'No declared Evidence opportunity'}</span></div>{error ? <div className="mt-3"><SectionError message={`${error} Availability remains Unknown.`} /></div> : readiness ? <div className="mt-4 rounded-xl border border-ink/10 p-3"><div className="flex flex-wrap gap-2"><StatusBadge label={`Availability ${readiness.availabilityState}`} tone={stateTone(readiness.availabilityState)} /><StatusBadge label={`Readiness ${readiness.readinessState}`} tone={stateTone(readiness.readinessState)} /><StatusBadge label={`Usability ${readiness.candidateUsabilityState}`} tone={stateTone(readiness.candidateUsabilityState)} /></div>{readiness.requirements.length ? <ul className="mt-3 space-y-1 text-xs text-ink/65">{readiness.requirements.map((item) => <li key={item.stableKey}>{item.state === 'unknown' ? <UnknownValue reason={item.reasonCode.replaceAll('_', ' ').toLowerCase()} /> : `${item.state.replaceAll('_', ' ')} — ${item.reasonCode.replaceAll('_', ' ').toLowerCase()}`}</li>)}</ul> : null}</div> : <div className="mt-4"><UnknownValue reason="availability has not been established" /></div>}<div className="mt-4 flex flex-wrap gap-2"><Link className="button-primary" to={activityHandoffPath({ origin: 'learn', returnTo: paths.curriculum(unit.curriculumId), reference: { kind: 'curriculum_unit', curriculumId: unit.curriculumId, unitDefinitionId: unit.unitDefinitionId, semanticDefinitionId: unit.targets?.[0]?.semanticDefinitionId, title: unit.title } })}>Start or log actual work</Link></div>{related.length ? <div className="mt-4 border-t border-ink/10 pt-3"><p className="text-xs font-semibold uppercase tracking-wide text-ink/65">Intended outcomes</p><ul className="mt-2 space-y-2 text-sm">{related.map(({ target, node }) => <li key={target.semanticDefinitionId}><span>{target.intendedLearningOutcome || 'Authored learning outcome'}</span>{node ? <Link className="button-quiet ml-2 text-xs" to={paths.competency(node.id)}><Route className="size-3" />Matches target: {node.title}</Link> : <span className="ml-2 text-xs text-ink/65">Related competency title unavailable</span>}</li>)}</ul></div> : null}{unit.evidenceOpportunities.length ? <details className="mt-4 text-sm"><summary className="cursor-pointer font-semibold">Evidence opportunities</summary><ul className="mt-2 space-y-1 text-ink/65">{unit.evidenceOpportunities.map((item) => <li key={item.stableKey}>{item.evidenceKind.replaceAll('_', ' ')}{item.requiresActualActivity ? ' · requires actual Activity' : ''}{item.requiresArtifact ? ' · requires artifact reference' : ''}</li>)}</ul></details> : null}</article>
}
