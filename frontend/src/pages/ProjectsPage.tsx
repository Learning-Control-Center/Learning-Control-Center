import { AlertTriangle, CheckCircle2, Clock3, FolderKanban, Play, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiV2, formatDuration } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type Project = {
  id: string
  stableKey: string
  activeVersionId: string | null
  lifecycleState: string
}

type ProjectTask = {
  id: string
  identityId: string
  stableKey: string
  title: string
  description: string
  preferredDurationMs: number | null
}

type ProjectCriterion = {
  id: string
  stableKey: string
  title: string
}

type ProjectVersion = {
  id: string
  version: number
  title: string
  description: string
  effectiveAt: string
  tasks: ProjectTask[]
  criteria: ProjectCriterion[]
}

type Candidate = {
  project_id: string
  task_definition_id: string
  task_identity_id: string
  task_stable_key: string
  title: string
  lifecycle_state: string
  availability_state: string
  readiness_state: string
  candidate_usability_state: string
  actionable_blocker_keys: string[]
  duration_range_ms: [number, number, number] | null
}

type ProjectEvent = {
  id: string
  eventType: string
  taskIdentityId: string | null
  taskLifecycleState: string | null
  blockerKey: string | null
  occurredAt: string
}

type Evaluation = {
  id: string
  state: string
  evaluatedAt: string
  evidenceIds: string[]
}

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [versions, setVersions] = useState<ProjectVersion[]>([])
  const [events, setEvents] = useState<ProjectEvent[]>([])
  const [evaluations, setEvaluations] = useState<Record<string, Evaluation[]>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [items, catalog] = await Promise.all([
        apiV2<Project[]>('/projects'),
        apiV2<{ candidates: Candidate[] }>('/projects/catalog/current'),
      ])
      setProjects(items)
      setCandidates(catalog.candidates)
      setSelectedId((current) =>
        items.some((item) => item.id === current) ? current : (items[0]?.id ?? ''),
      )
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Projects could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])
  useEffect(() => {
    if (!selectedId) {
      setVersions([])
      setEvents([])
      setEvaluations({})
      return
    }
    setError('')
    void Promise.all([
      apiV2<ProjectVersion[]>(`/projects/${selectedId}/versions`),
      apiV2<ProjectEvent[]>(`/projects/${selectedId}/events`),
    ])
      .then(async ([versionItems, eventItems]) => {
        setVersions(versionItems)
        setEvents(eventItems)
        const active = versionItems.find(
          (item) => item.id === projects.find((project) => project.id === selectedId)?.activeVersionId,
        )
        if (!active) {
          setEvaluations({})
          return
        }
        const histories = await Promise.all(
          active.criteria.map(async (criterion) => [
            criterion.id,
            await apiV2<Evaluation[]>(`/projects/criteria/${criterion.id}/evaluations`),
          ] as const),
        )
        setEvaluations(Object.fromEntries(histories))
      })
      .catch((caught) =>
        setError(caught instanceof ApiError ? caught.message : 'Project detail could not be loaded.'),
      )
  }, [projects, selectedId])

  const selected = projects.find((item) => item.id === selectedId)
  const activeVersion = versions.find((item) => item.id === selected?.activeVersionId)
  const projectCandidates = useMemo(
    () => candidates.filter((item) => item.project_id === selectedId),
    [candidates, selectedId],
  )

  const appendTaskState = async (candidate: Candidate, state: 'started' | 'completed') => {
    if (!selected) return
    setBusy(candidate.task_definition_id)
    setError('')
    try {
      await apiV2(`/projects/${selected.id}/events`, {
        method: 'POST',
        body: JSON.stringify({
          event_type: 'task_lifecycle',
          task_identity_id: candidate.task_identity_id,
          task_lifecycle_state: state,
          source: 'user',
          idempotency_key: crypto.randomUUID(),
        }),
      })
      await load()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Task state could not be recorded.')
    } finally {
      setBusy('')
    }
  }

  if (loading) return <LoadingState label="Loading canonical Projects" />
  if (error && !projects.length) return <ErrorState message={error} retry={() => void load()} />
  if (!projects.length) {
    return (
      <EmptyState
        title="No Projects yet"
        detail="Create and activate a versioned Project through the V2 API to track outcome work here."
      />
    )
  }

  return (
    <div className="mx-auto w-full max-w-[82rem]">
      <header className="mb-7">
        <p className="eyebrow mb-2">Outcome and evidence sources</p>
        <h1 className="page-title">Projects</h1>
        <p className="mt-2 text-sm text-ink/60">
          Project progress organizes actual work. Completion never assigns capability by itself.
        </p>
      </header>
      {error ? <div className="mb-5"><ErrorState message={error} retry={() => void load()} /></div> : null}
      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="surface p-5">
          <p className="eyebrow mb-4">Projects</p>
          <ul className="space-y-2">
            {projects.map((project) => (
              <li key={project.id}>
                <button
                  className={`w-full rounded-xl border p-3 text-left ${selectedId === project.id ? 'border-moss/40 bg-moss/10' : 'border-ink/10 bg-white/60'}`}
                  onClick={() => setSelectedId(project.id)}
                  type="button"
                >
                  <span className="font-medium">{project.stableKey}</span>
                  <span className="mt-1 block text-xs text-ink/50">
                    {project.lifecycleState.replaceAll('_', ' ')} · {project.activeVersionId ? 'active definition' : 'no active definition'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-6 rounded-xl border border-ink/10 bg-white/60 p-3 text-xs leading-5 text-ink/60">
            <FolderKanban className="mb-2 size-4 text-moss" aria-hidden="true" />
            Actual time and outcomes are logged separately. Planned progress is not evidence.
          </div>
        </aside>
        <section className="space-y-5">
          <div className="surface p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="eyebrow">Active definition</p>
                <h2 className="mt-2 font-display text-xl font-semibold">{activeVersion?.title ?? 'No active version'}</h2>
                <p className="mt-1 text-sm text-ink/60">{activeVersion?.description || 'No description.'}</p>
              </div>
              <Link className="rounded-lg border border-ink/15 px-3 py-2 text-xs" to="/sessions">
                Log actual work
              </Link>
            </div>
          </div>
          <div className="surface p-5">
            <div className="mb-4 flex items-center justify-between">
              <p className="eyebrow">Tasks and blockers</p>
              <span className="font-mono text-xs text-ink/45">{projectCandidates.length} tasks</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {projectCandidates.map((candidate) => (
                <article className="rounded-2xl border border-ink/10 bg-white/70 p-5" key={candidate.task_definition_id}>
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-mono uppercase tracking-wide text-moss">{candidate.lifecycle_state.replaceAll('_', ' ')}</span>
                    <span>{candidate.candidate_usability_state}</span>
                  </div>
                  <h3 className="mt-3 font-display text-lg font-semibold">{candidate.title}</h3>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink/55">
                    <span className="inline-flex items-center gap-1"><Clock3 className="size-3" />{candidate.duration_range_ms ? formatDuration(candidate.duration_range_ms[1]) : 'Duration unknown'}</span>
                    <span>availability {candidate.availability_state}</span>
                    <span>readiness {candidate.readiness_state}</span>
                  </div>
                  {candidate.actionable_blocker_keys.length ? (
                    <p className="mt-3 inline-flex items-center gap-1 text-xs text-rust">
                      <AlertTriangle className="size-3.5" />Blocked: {candidate.actionable_blocker_keys.join(', ')}
                    </p>
                  ) : null}
                  <div className="mt-4 flex gap-2">
                    {candidate.lifecycle_state === 'not_started' ? (
                      <button className="inline-flex items-center gap-1 rounded-lg border border-ink/15 px-3 py-1.5 text-xs disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void appendTaskState(candidate, 'started')} type="button">
                        <Play className="size-3" />Start task
                      </button>
                    ) : candidate.lifecycle_state === 'started' ? (
                      <button className="inline-flex items-center gap-1 rounded-lg border border-ink/15 px-3 py-1.5 text-xs disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void appendTaskState(candidate, 'completed')} type="button">
                        <CheckCircle2 className="size-3" />Record completion
                      </button>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          </div>
          <div className="grid gap-5 md:grid-cols-2">
            <div className="surface p-5">
              <p className="eyebrow mb-3">Evidence-backed criteria</p>
              {activeVersion?.criteria.length ? activeVersion.criteria.map((criterion) => {
                const history = evaluations[criterion.id] ?? []
                const latest = history.at(-1)
                return <div className="border-t border-ink/10 py-3 first:border-0" key={criterion.id}>
                  <p className="inline-flex items-center gap-2 text-sm font-medium"><ShieldCheck className="size-4 text-moss" />{criterion.title}</p>
                  <p className="mt-1 text-xs text-ink/55">{latest ? `${latest.state} · ${latest.evidenceIds.length} Evidence records` : 'Unknown · no Evidence-backed evaluation'}</p>
                </div>
              }) : <p className="text-sm text-ink/60">No criteria in the active version.</p>}
            </div>
            <div className="surface p-5">
              <p className="eyebrow mb-3">Append-only history</p>
              {events.length ? <ol className="space-y-2">{events.slice(-6).reverse().map((event) => <li className="text-xs text-ink/60" key={event.id}><span className="font-medium text-ink">{event.eventType.replaceAll('_', ' ')}</span>{event.taskLifecycleState ? ` · ${event.taskLifecycleState}` : ''}{event.blockerKey ? ` · ${event.blockerKey}` : ''}<span className="block text-ink/40">{new Date(event.occurredAt).toLocaleString()}</span></li>)}</ol> : <p className="text-sm text-ink/60">No lifecycle events yet.</p>}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
