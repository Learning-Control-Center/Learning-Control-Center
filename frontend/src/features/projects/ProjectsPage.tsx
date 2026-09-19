import { AlertTriangle, CheckCircle2, Clock3, FolderKanban, Play, Route, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiV2, formatDuration } from '../../api'
import { Button, EmptyState, ErrorState, LiveNotice, LoadingState, PageHeader, SectionError, SectionHeader, StatusBadge, Surface, UnknownValue } from '../../shared/components'
import { activityHandoffPath } from '../../shared/contracts/learningReferences'
import type { ProjectCatalogCandidate, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import { adaptProjectCatalogCandidate } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection } from '../../shared/contracts/roadmapProjection'
import { paths } from '../../shared/navigation/paths'
import type { Evaluation, Project, ProjectEvent, ProjectVersion } from './model'

const stateTone = (state: string) => state === 'met' || state === 'completed' || state === 'demonstrated' ? 'success' : state === 'not_met' || state === 'blocked' ? 'critical' : state === 'unknown' ? 'unknown' : 'neutral'

export function ProjectsPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[]>([])
  const [candidates, setCandidates] = useState<ProjectCatalogCandidate[]>([])
  const [projection, setProjection] = useState<RoadmapProjection | null>(null)
  const [relatedError, setRelatedError] = useState('')
  const [selectedId, setSelectedId] = useState(projectId ?? '')
  const [versionsByProject, setVersionsByProject] = useState<Record<string, ProjectVersion[]>>({})
  const [versionsError, setVersionsError] = useState('')
  const [events, setEvents] = useState<ProjectEvent[]>([])
  const [evaluations, setEvaluations] = useState<Record<string, Evaluation[]>>({})
  const [evaluationErrors, setEvaluationErrors] = useState<string[]>([])
  const [detailError, setDetailError] = useState('')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(''); setVersionsError(''); setRelatedError('')
    try {
      const [items, catalog] = await Promise.all([
        apiV2<Project[]>('/projects', { signal }),
        apiV2<{ candidates: ProjectCatalogCandidateApi[] }>('/projects/catalog/current', { signal }),
      ])
      setProjects(items); setCandidates(catalog.candidates.map(adaptProjectCatalogCandidate))
      setSelectedId((current) => { const requested = projectId ?? current; return items.some((item) => item.id === requested) ? requested : projectId ? '' : (items[0]?.id ?? '') })
      void Promise.allSettled(items.map((item) => apiV2<ProjectVersion[]>(`/projects/${item.id}/versions`, { signal }).then((versions) => [item.id, versions] as const))).then((results) => {
        if (signal?.aborted) return
        setVersionsByProject(Object.fromEntries(results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])))
        if (results.some((result) => result.status === 'rejected')) setVersionsError('Some Project titles or immutable version details could not be loaded.')
      })
      void apiV2<RoadmapProjection>('/roadmap-projection/current', { signal }).then(setProjection).catch((caught) => {
        if ((caught as Error).name !== 'AbortError') setRelatedError('Related competency and Roadmap links could not be loaded. Project state remains available.')
      })
    } catch (caught) { if ((caught as Error).name !== 'AbortError') setError(caught instanceof ApiError ? caught.message : 'Projects could not be loaded.') }
    finally { if (!signal?.aborted) setLoading(false) }
  }, [projectId])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])

  const selected = projects.find((item) => item.id === selectedId)
  const versions = versionsByProject[selectedId] ?? []
  const activeVersion = versions.find((item) => item.id === selected?.activeVersionId)
  const projectCandidates = useMemo(() => candidates.filter((item) => item.projectId === selectedId), [candidates, selectedId])
  const titleByProject = useMemo(() => Object.fromEntries(projects.map((item) => { const versions = versionsByProject[item.id] ?? []; return [item.id, versions.find((version) => version.id === item.activeVersionId)?.title ?? versions.at(-1)?.title ?? 'Project title unavailable'] })), [projects, versionsByProject])
  const competencyBySemantic = useMemo(() => new Map((projection?.nodes ?? []).map((node) => [node.semanticDefinitionId, node])), [projection])

  useEffect(() => {
    if (!selectedId) { setEvents([]); setEvaluations({}); setEvaluationErrors([]); return }
    const controller = new AbortController(); setDetailError(''); setEvaluationErrors([])
    void apiV2<ProjectEvent[]>(`/projects/${selectedId}/events`, { signal: controller.signal }).then(setEvents).catch((caught) => { if ((caught as Error).name !== 'AbortError') setDetailError(caught instanceof ApiError ? caught.message : 'Project history could not be loaded.') })
    if (!activeVersion) { setEvaluations({}); return () => controller.abort() }
    void Promise.allSettled(activeVersion.criteria.map((criterion) => apiV2<Evaluation[]>(`/projects/criteria/${criterion.id}/evaluations`, { signal: controller.signal }).then((items) => [criterion.id, items] as const))).then((results) => {
      if (controller.signal.aborted) return
      setEvaluations(Object.fromEntries(results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])))
      setEvaluationErrors(results.flatMap((result, index) => result.status === 'rejected' ? [activeVersion.criteria[index].title] : []))
    })
    return () => controller.abort()
  }, [activeVersion, selectedId])

  const appendTaskState = async (candidate: ProjectCatalogCandidate, state: 'started' | 'completed') => {
    if (!selected || busy) return
    setBusy(candidate.taskDefinitionId); setError(''); setNotice('')
    try {
      await apiV2(`/projects/${selected.id}/events`, { method: 'POST', body: JSON.stringify({ event_type: 'task_lifecycle', task_identity_id: candidate.taskIdentityId, task_lifecycle_state: state, source: 'user', idempotency_key: crypto.randomUUID() }) })
      setNotice(`${candidate.title} recorded as ${state.replaceAll('_', ' ')}. This task state does not assign capability or Evidence.`); await load()
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Task state could not be recorded.') }
    finally { setBusy('') }
  }

  if (loading) return <LoadingState label="Loading canonical Projects" />
  if (error && !projects.length) return <div className="space-y-6"><PageHeader eyebrow="Outcome work and Evidence sources" title="Projects" description="Projects organize intended outcomes, actual work, and Evidence without merging them." /><ErrorState message={error} retry={() => void load()} /></div>
  if (!projects.length) return <div className="space-y-6"><PageHeader eyebrow="Outcome work and Evidence sources" title="Projects" description="Projects organize intended outcomes, actual work, and Evidence without merging them." /><EmptyState title="No Projects yet" detail="Create and activate a versioned Project through the V2 API to track outcome work here." /></div>
  if (projectId && !selected) return <div className="space-y-6"><PageHeader eyebrow="Outcome work and Evidence sources" title="Project unavailable" description="The requested Project is not present." /><EmptyState title="No matching Project" detail="Choose an available Project from the Projects view." action={<Link className="button-secondary" to={paths.projects}>Back to Projects</Link>} /></div>

  return <div className="mx-auto w-full max-w-[88rem] space-y-6">
    <LiveNotice>{notice}</LiveNotice>
    <PageHeader eyebrow="Outcome work and Evidence sources" title={projectId ? (activeVersion?.title ?? titleByProject[selectedId]) : 'Projects'} description="Projects organize intended outcomes and actual work. Task completion is neither Evidence nor capability by itself." actions={projectId ? <Link className="button-secondary" to={paths.projects}>All Projects</Link> : undefined} />
    {error ? <ErrorState message={error} retry={() => void load()} /> : null}
    <div className="grid gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
      <Surface className="h-fit p-5"><SectionHeader title="Projects" /><ul className="mt-4 space-y-2">{projects.map((project) => <li key={project.id}><button aria-pressed={selectedId === project.id} className={`w-full rounded-xl border p-3 text-left ${selectedId === project.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`} onClick={() => { setSelectedId(project.id); if (projectId) void navigate(paths.project(project.id)) }} type="button"><span className="font-medium">{titleByProject[project.id]}</span><span className="mt-1 block text-xs text-ink/65">{selectedId === project.id ? 'Selected · ' : ''}{project.lifecycleState.replaceAll('_', ' ')} · {project.activeVersionId ? 'active definition' : 'no active definition'}</span></button></li>)}</ul><div className="mt-6 rounded-xl border border-ink/10 bg-white/60 p-3 text-xs leading-5 text-ink/65"><FolderKanban className="mb-2 size-4 text-moss" />Actual time, task state, and Evidence are distinct records.</div></Surface>
      <div className="space-y-5">
        {versionsError ? <SectionError message={versionsError} /> : null}{detailError ? <SectionError message={detailError} /> : null}{relatedError ? <SectionError message={relatedError} /> : null}
        <Surface className="p-5"><SectionHeader title={activeVersion?.title ?? titleByProject[selectedId]} description={activeVersion?.description || 'Activate an immutable Project definition to expose outcome work.'} actions={<div className="flex gap-2"><StatusBadge label={selected?.lifecycleState.replaceAll('_', ' ') ?? 'unknown'} tone={stateTone(selected?.lifecycleState ?? 'unknown')} />{selected?.activeVersionId ? <StatusBadge label={`Definition v${activeVersion?.version ?? '?'}`} /> : null}</div>} /></Surface>
        <section aria-labelledby="project-tasks-heading"><SectionHeader headingId="project-tasks-heading" title="Outcome tasks and blockers" description="Lifecycle controls record task state. Start or log actual work creates an explicit Activity relationship." />{projectCandidates.length ? <div className="mt-4 grid gap-4 xl:grid-cols-2">{projectCandidates.map((candidate) => {
          const task = activeVersion?.tasks.find((item) => item.id === candidate.taskDefinitionId)
          const relatedNodes = (activeVersion?.targets ?? []).filter((target) => target.taskDefinitionId === candidate.taskDefinitionId).map((target) => competencyBySemantic.get(target.semanticDefinitionId)).filter(Boolean)
          return <article className="surface p-5" key={candidate.taskDefinitionId}><div className="flex flex-wrap items-center justify-between gap-2"><StatusBadge label={candidate.lifecycleState.replaceAll('_', ' ')} tone={stateTone(candidate.lifecycleState)} /><StatusBadge label={`Usability ${candidate.usabilityState}`} tone={stateTone(candidate.usabilityState)} /></div><h3 className="mt-3 font-display text-xl font-semibold">{candidate.title}</h3><p className="mt-2 text-sm text-ink/65">{task?.description || 'No description.'}</p><div className="mt-3 flex flex-wrap gap-2 text-xs text-ink/65"><span className="inline-flex items-center gap-1"><Clock3 className="size-3" />{candidate.durationRangeMs ? formatDuration(candidate.durationRangeMs[1]) : 'Duration Unknown'}</span><span>Availability {candidate.availabilityState}</span><span>Readiness {candidate.readinessState}</span></div>{candidate.blockerKeys.length ? <div className="mt-3 rounded-xl border border-status-critical/25 bg-status-critical/5 p-3"><p className="inline-flex items-center gap-1 text-xs font-semibold text-status-critical"><AlertTriangle className="size-3.5" />Actionable blockers</p><ul className="mt-1 text-xs text-ink/65">{candidate.blockerKeys.map((key) => <li key={key}>{key.replaceAll('_', ' ')}</li>)}</ul></div> : null}<div className="mt-4 flex flex-wrap gap-2">{candidate.lifecycleState === 'not_started' ? <Button variant="secondary" disabled={Boolean(busy)} onClick={() => void appendTaskState(candidate, 'started')}><Play className="size-4" />{busy === candidate.taskDefinitionId ? 'Recording…' : 'Start task'}</Button> : candidate.lifecycleState === 'started' ? <Button variant="secondary" disabled={Boolean(busy)} onClick={() => void appendTaskState(candidate, 'completed')}><CheckCircle2 className="size-4" />{busy === candidate.taskDefinitionId ? 'Recording…' : 'Record completion'}</Button> : null}<Link className="button-primary" to={activityHandoffPath({ origin: 'projects', returnTo: paths.project(selectedId), reference: { kind: 'project_task', projectId: selectedId, projectVersionId: activeVersion?.id, taskDefinitionId: candidate.taskDefinitionId, semanticDefinitionId: activeVersion?.targets?.find((target) => target.taskDefinitionId === candidate.taskDefinitionId)?.semanticDefinitionId, title: candidate.title } })}>Start or log actual work</Link><a className="button-quiet" href="#project-evidence">View Evidence criteria</a></div>{relatedNodes.length ? <div className="mt-4 border-t border-ink/10 pt-3"><p className="text-xs font-semibold uppercase tracking-wide text-ink/65">Intended competency outcomes</p><div className="mt-2 flex flex-wrap gap-2">{relatedNodes.map((node) => node ? <span className="inline-flex flex-wrap gap-1" key={node.id}><Link className="button-quiet text-xs" to={paths.competency(node.id)}>{node.title}</Link><Link className="button-quiet text-xs" to={`${paths.roadmap}?focus=${encodeURIComponent(node.id)}`}><Route className="size-3" />Roadmap</Link></span> : null)}</div></div> : null}</article>
        })}</div> : <EmptyState className="mt-4" title="No active Project tasks" detail={selected?.activeVersionId ? 'This Project definition contains no current tasks.' : 'Activate an immutable Project version to expose tasks.'} />}</section>
        <div className="grid gap-5 xl:grid-cols-2"><Surface id="project-evidence" className="scroll-mt-24 p-5"><SectionHeader title="Evidence-backed criteria" description="Criterion evaluation is separate from task completion." />{evaluationErrors.length ? <div className="mt-3"><SectionError message={`Evaluation history unavailable for: ${evaluationErrors.join(', ')}.`} /></div> : null}{activeVersion?.criteria.length ? <div className="mt-3">{activeVersion.criteria.map((criterion) => { const history = evaluations[criterion.id] ?? []; const latest = history.at(-1); return <article className="border-t border-ink/10 py-3 first:border-0" key={criterion.id}><p className="inline-flex items-center gap-2 text-sm font-medium"><ShieldCheck className="size-4 text-moss" />{criterion.title}</p><p className="mt-1 text-xs text-ink/65">{latest ? `${latest.state.replaceAll('_', ' ')} · ${latest.evidenceIds.length} Evidence record${latest.evidenceIds.length === 1 ? '' : 's'}` : <UnknownValue reason="no Evidence-backed evaluation" />}</p>{latest ? <p className="mt-1 text-xs text-ink/65">Evaluated {new Date(latest.evaluatedAt).toLocaleString()} · {history.length} immutable evaluation{history.length === 1 ? '' : 's'}</p> : null}</article> })}</div> : <p className="mt-4 text-sm text-ink/65">No criteria in the active version.</p>}</Surface><Surface className="p-5"><SectionHeader title="Append-only Project history" description="Lifecycle and blocker events remain distinct from Activities and Evidence." />{events.length ? <ol className="mt-4 space-y-3">{events.slice(-8).reverse().map((event) => <li className="rounded-xl border border-ink/10 p-3 text-xs text-ink/65" key={event.id}><span className="font-medium text-ink">{event.eventType.replaceAll('_', ' ')}</span>{event.taskLifecycleState ? ` · ${event.taskLifecycleState.replaceAll('_', ' ')}` : ''}{event.blockerKey ? ` · ${event.blockerKey.replaceAll('_', ' ')}` : ''}<span className="mt-1 block">{new Date(event.occurredAt).toLocaleString()}</span></li>)}</ol> : <p className="mt-4 text-sm text-ink/65">No lifecycle events yet.</p>}</Surface></div>
        {versions.length ? <details className="surface p-5"><summary className="cursor-pointer font-semibold">Immutable Project definition history</summary><ul className="mt-4 space-y-2">{versions.map((version) => <li className="rounded-xl border border-ink/10 p-3 text-sm" key={version.id}>v{version.version} · {version.title} · effective {new Date(version.effectiveAt).toLocaleDateString()}</li>)}</ul></details> : null}
      </div>
    </div>
  </div>
}
