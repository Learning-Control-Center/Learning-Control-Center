import { Check, Link2, Pause, Play, Square, TimerReset, XCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, api, apiV2, formatDuration } from '../../api'
import { Button, ErrorState, LiveNotice, LoadingState, MutationError, PageHeader, SectionError, SectionHeader, SelectField, Surface, TextAreaField, TextField } from '../../shared/components'
import { activityResultPath, parseActivityHandoff, type ActualWorkHandoff, type ActualWorkReference } from '../../shared/contracts/learningReferences'
import type { CurriculumCatalogUnit, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection } from '../../shared/contracts/roadmapProjection'
import type { Session } from '../../types'
import type { Activity } from './model'
import { referenceOptions } from './model'
import { sessionElapsedDuration, useActiveSession } from '../../shared/session/ActiveSessionProvider'
import { DailyReflectionEditor } from '../reflection'
import { paths } from '../../shared/navigation/paths'

type AssessmentActivityItem = { id: string; activityId: string; sessionId: string; sessionState: string; reviewRequired: boolean; task: { unitTitle: string } }

const categories = ['learning', 'reading', 'practice', 'coding', 'debugging', 'project', 'review', 'verification', 'research']
const assistance = ['none', 'docs_only', 'ai_hint', 'ai_assisted', 'agent_led']

function contributionFor(reference: ActualWorkReference | null) {
  if (!reference || reference.kind === 'curriculum_unit' || reference.kind === 'unlinked') return []
  if (reference.kind === 'competency') return [{ target_type: 'competency', competency_identity_id: reference.competencyIdentityId, relevance: 'primary', provenance: 'user_selected' }]
  return [{ target_type: 'project', project_id: reference.projectId, project_version_id: reference.projectVersionId, project_task_definition_id: reference.taskDefinitionId, relevance: 'primary', provenance: 'user_selected' }]
}

export function SessionsPage() {
  const [searchParams] = useSearchParams()
  const handoff = useMemo(() => parseActivityHandoff(searchParams), [searchParams])
  const handoffIdentity = useMemo(() => handoff ? JSON.stringify(handoff) : '', [handoff])
  const currentHandoffIdentity = useRef(handoffIdentity)
  const { active, status: activeSessionStatus, error: activeSessionError, refresh: refreshActiveSession } = useActiveSession()
  const [activities, setActivities] = useState<Activity[]>([])
  const [projection, setProjection] = useState<RoadmapProjection | null>(null)
  const [curriculumUnits, setCurriculumUnits] = useState<CurriculumCatalogUnit[]>([])
  const [projectTasks, setProjectTasks] = useState<ProjectCatalogCandidateApi[]>([])
  const [referenceError, setReferenceError] = useState('')
  const [sessions, setSessions] = useState<Session[]>([])
  const [assessments, setAssessments] = useState<AssessmentActivityItem[]>([])
  const [assessmentError, setAssessmentError] = useState('')
  const [selectedActivityId, setSelectedActivityId] = useState('')
  const [selectedReferenceKey, setSelectedReferenceKey] = useState('unlinked')
  const [linkedHandoff, setLinkedHandoff] = useState(false)
  const [relationshipBusy, setRelationshipBusy] = useState(false)
  const [timerBusy, setTimerBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [tick, setTick] = useState(Date.now())

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setLoadError(''); setReferenceError('')
    try {
      const [activityItems, listResponse] = await Promise.all([
        apiV2<Activity[]>('/activities', { signal }),
        api<{ items: Session[] }>('/sessions?limit=30', { signal }),
      ])
      setActivities(activityItems); setSessions(listResponse.items)
      void apiV2<{ items: AssessmentActivityItem[] }>('/assessment-executions', { signal }).then(
        (response) => { if (!signal?.aborted) { setAssessments(response.items ?? []); setAssessmentError('') } },
        (caught) => { if (!signal?.aborted) setAssessmentError(caught instanceof Error ? caught.message : 'Assessment history could not be loaded.') },
      )
      setSelectedActivityId((current) => activityItems.some((item) => item.id === current) ? current : (activityItems[0]?.id ?? ''))
      void Promise.allSettled([
        apiV2<RoadmapProjection>('/roadmap-projection/current', { signal }),
        apiV2<{ units: CurriculumCatalogUnit[] }>('/curricula/catalog/active', { signal }),
        apiV2<{ candidates: ProjectCatalogCandidateApi[] }>('/projects/catalog/current', { signal }),
      ]).then(([roadmap, curricula, projects]) => {
        if (signal?.aborted) return
        setProjection(roadmap.status === 'fulfilled' ? roadmap.value : null)
        setCurriculumUnits(curricula.status === 'fulfilled' ? curricula.value.units : [])
        setProjectTasks(projects.status === 'fulfilled' ? projects.value.candidates : [])
        const failed = [roadmap, curricula, projects].filter((item) => item.status === 'rejected').length
        setReferenceError(failed ? `${failed} optional canonical reference source${failed === 1 ? '' : 's'} could not be loaded. Unlinked Activity and Session history remain available.` : '')
      })
    } catch (caught) { if ((caught as Error).name !== 'AbortError') setLoadError(caught instanceof ApiError ? caught.message : 'Activity and Session state could not be loaded.') }
    finally { if (!signal?.aborted) setLoading(false) }
  }, [])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])
  useEffect(() => { if (active?.timedState !== 'running') return; const interval = window.setInterval(() => setTick(Date.now()), 1000); return () => window.clearInterval(interval) }, [active?.timedState])
  useEffect(() => {
    currentHandoffIdentity.current = handoffIdentity
    setLinkedHandoff(false)
    setRelationshipBusy(false)
    setNotice('')
    setMutationError('')
  }, [handoffIdentity])

  const options = useMemo(() => referenceOptions(projection, curriculumUnits, projectTasks), [curriculumUnits, projectTasks, projection])
  const selectedReference = handoff ? (linkedHandoff ? handoff.reference : null) : options.find((item) => item.key === selectedReferenceKey)?.reference ?? null
  const authoritativeActive = activeSessionStatus === 'ready' ? active : null
  const currentDuration = useMemo(() => !authoritativeActive ? 0 : sessionElapsedDuration(authoritativeActive, tick), [authoritativeActive, tick])
  const titleByCompetency = useMemo(() => new Map((projection?.nodes ?? []).map((node) => [node.id, node.title])), [projection])

  const relateActivity = async (activityId: string, context: ActualWorkHandoff) => {
    if (context.reference.kind === 'curriculum_unit') await apiV2('/curricula/activity-links', { method: 'POST', body: JSON.stringify({ activity_id: activityId, learning_unit_definition_id: context.reference.unitDefinitionId, provenance: 'user_confirmed', idempotency_key: crypto.randomUUID() }) })
    if (context.reference.kind === 'project_task') await apiV2('/projects/activity-links', { method: 'POST', body: JSON.stringify({ activity_id: activityId, task_definition_id: context.reference.taskDefinitionId, provenance: 'user_confirmed', idempotency_key: crypto.randomUUID() }) })
  }
  const confirmHandoff = async (activityId = selectedActivityId) => {
    if (!handoff || !activityId || relationshipBusy) return false
    const confirmingIdentity = handoffIdentity
    setRelationshipBusy(true); setMutationError(''); setNotice('')
    try {
      await relateActivity(activityId, handoff)
      if (currentHandoffIdentity.current !== confirmingIdentity) return false
      setLinkedHandoff(true); setSelectedActivityId(activityId)
      setNotice(handoff.reference.kind === 'competency' ? 'Activity selected. The competency contribution will be recorded only when a Session is started or logged.' : handoff.reference.kind === 'unlinked' ? 'Activity selected. No Today suggestion or relation has changed.' : `Activity related to ${handoff.reference.title}.`)
      return true
    } catch (caught) {
      if (currentHandoffIdentity.current !== confirmingIdentity) return false
      setMutationError(caught instanceof ApiError ? `The Activity remains created, but the relationship failed: ${caught.message} Select it and retry the relationship.` : 'The Activity remains created, but the relationship failed. Select it and retry the relationship.')
      return false
    } finally { if (currentHandoffIdentity.current === confirmingIdentity) setRelationshipBusy(false) }
  }
  const mutateTimer = async (action: 'pause' | 'resume' | 'cancel' | 'complete') => {
    if (!authoritativeActive || activeSessionStatus !== 'ready' || timerBusy) return
    setTimerBusy(action); setMutationError(''); setNotice('')
    try { await apiV2(`/sessions/${authoritativeActive.id}/${action}`, { method: 'POST', body: action === 'complete' ? JSON.stringify({ outcome: 'completed', notes: authoritativeActive.notes }) : undefined }); setNotice(action === 'complete' ? 'Timer completed.' : action === 'cancel' ? 'Timer cancelled.' : ''); await Promise.all([load(), refreshActiveSession()]) }
    catch (caught) { setMutationError(caught instanceof ApiError ? caught.message : 'The timer could not be updated.') }
    finally { setTimerBusy('') }
  }

  if (loading) return <LoadingState label="Loading actual work and Session state" />
  if (loadError && !activities.length && !active && !sessions.length) return <div className="space-y-6"><PageHeader eyebrow="Actual work record" title="Activity" description="Create or select an Activity, then time or log a Session." /><ErrorState message={loadError} retry={() => void load()} /></div>

  return <div className="mx-auto w-full max-w-[90rem] space-y-6">
    <LiveNotice>{notice}</LiveNotice>
    <PageHeader eyebrow="Actual work record" title="Activity" description="Create or select a human-named Activity, then time or log a Session against explicit canonical references. The server remains timer authority." />
    {mutationError ? <MutationError>{mutationError}</MutationError> : null}{loadError ? <SectionError message={loadError} retry={() => void load()} /> : null}{referenceError ? <SectionError message={referenceError} /> : null}{assessmentError ? <SectionError message={assessmentError} retry={() => void load()} /> : null}
    {handoff ? <Surface className="border-moss/25 bg-moss/5 p-5"><SectionHeader title={`Continue: ${handoff.reference.title}`} description={handoff.reference.kind === 'unlinked' ? 'Choose or create the actual Activity. It remains unlinked until the originating Today suggestion explicitly confirms a replacement.' : `This ${handoff.reference.kind.replaceAll('_', ' ')} reference came from ${handoff.origin}. Nothing changes until you confirm below.`} /><div className="mt-4 grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]"><SelectField id="handoff-activity" label="Use an existing Activity" value={selectedActivityId} onChange={(event) => { setSelectedActivityId(event.target.value); setLinkedHandoff(false) }}><option value="">Select an Activity</option>{activities.map((item) => <option value={item.id} key={item.id}>{item.title} · {item.categoryStableKey}</option>)}</SelectField><div className="flex items-end gap-2"><Button aria-busy={relationshipBusy} disabled={!selectedActivityId || linkedHandoff || relationshipBusy} onClick={() => void confirmHandoff()}><Link2 className="size-4" />{relationshipBusy ? 'Confirming…' : linkedHandoff ? 'Confirmed' : ['competency', 'unlinked'].includes(handoff.reference.kind) ? 'Use Activity' : 'Confirm relationship'}</Button><Link className="button-secondary" to={handoff.returnTo}>Cancel</Link></div></div>{linkedHandoff ? <p className="mt-4 flex flex-wrap items-center gap-2 text-sm font-medium text-status-success" role="status"><Check className="size-4" />{handoff.reference.kind === 'competency' ? 'Activity selected. The competency contribution is recorded only with a Session.' : handoff.reference.kind === 'unlinked' ? 'Activity selected. No suggestion or canonical reference has been changed yet.' : 'Relationship recorded. You may log a Session below.'}<Link className="button-quiet" to={activityResultPath(handoff, selectedActivityId)}>Return selected Activity</Link></p> : null}</Surface> : null}
    <CreateActivityForm key={handoffIdentity || 'unlinked'} handoff={handoff} onCreated={async (activity) => { setActivities((current) => [activity, ...current]); setSelectedActivityId(activity.id); setNotice(`${activity.title} created.`); if (handoff) await confirmHandoff(activity.id) }} onError={setMutationError} />
    <div className="grid gap-5 xl:grid-cols-[minmax(20rem,0.75fr)_minmax(0,1.25fr)]">
      <Surface className="p-5 sm:p-6"><SectionHeader title="Active timer" />{activeSessionStatus === 'error' ? <div className="mt-4"><SectionError message={`Active Session state is unavailable: ${activeSessionError}`} retry={() => void refreshActiveSession()} /><p className="mt-3 text-sm text-ink/65">Timer commands are withheld until the server-authoritative active Session state is available.</p></div> : activeSessionStatus === 'loading' ? <p className="mt-4 text-sm text-ink/65" role="status">Checking server-authoritative Session state…</p> : authoritativeActive ? <div className="mt-6"><p className="font-mono text-5xl font-semibold tracking-[-0.06em] sm:text-6xl" aria-live="off">{formatDuration(currentDuration, true)}</p><p className="mt-3 text-sm capitalize text-ink/65">{authoritativeActive.activityType} · {authoritativeActive.assistanceMode.replaceAll('_', ' ')} · {authoritativeActive.timedState}</p><div className="mt-7 flex flex-wrap gap-3">{authoritativeActive.timedState === 'running' ? <Button variant="secondary" disabled={Boolean(timerBusy)} onClick={() => void mutateTimer('pause')}><Pause className="size-4" />{timerBusy === 'pause' ? 'Pausing…' : 'Pause'}</Button> : <Button variant="secondary" disabled={Boolean(timerBusy)} onClick={() => void mutateTimer('resume')}><Play className="size-4" />{timerBusy === 'resume' ? 'Resuming…' : 'Resume'}</Button>}<Button disabled={Boolean(timerBusy)} onClick={() => void mutateTimer('complete')}><Square className="size-4 fill-current" />{timerBusy === 'complete' ? 'Completing…' : 'Complete'}</Button><Button variant="secondary" className="text-rose-700" disabled={Boolean(timerBusy)} onClick={() => void mutateTimer('cancel')}><XCircle className="size-4" />{timerBusy === 'cancel' ? 'Cancelling…' : 'Cancel'}</Button></div><p className="mt-5 text-xs leading-5 text-ink/65">Cancelling preserves measured time in history but excludes it from learning work and Evidence.</p></div> : <TimerStart activities={activities} selectedActivityId={selectedActivityId} reference={selectedReference} disabled={Boolean(handoff && !linkedHandoff)} onDone={async () => { setNotice(''); await Promise.all([load(), refreshActiveSession()]) }} onError={setMutationError} />}</Surface>
      <Surface className="p-5 sm:p-6"><SectionHeader title="Manual Session" /><ManualForm activities={activities} selectedActivityId={selectedActivityId} reference={selectedReference} disabled={Boolean(handoff && !linkedHandoff)} onDone={async () => { setNotice('Manual Session saved.'); await load() }} onError={setMutationError} /></Surface>
    </div>
    {!handoff ? <Surface className="p-5"><SelectField label="Relate new Sessions to" description="Canonical Profile, Curriculum, and Project references replace the legacy Roadmap picker. Unlinked work remains valid." value={selectedReferenceKey} onChange={(event) => setSelectedReferenceKey(event.target.value)}>{options.map((option) => <option value={option.key} key={option.key}>{option.label}</option>)}</SelectField></Surface> : null}
    <DailyReflectionEditor />
    {assessments.length ? <Surface className="p-5"><SectionHeader title="Assessment executions" description="Every bound assessment remains reachable here, including Sessions older than recent history and assessments from earlier Today generations." /><div className="mt-4 space-y-3">{assessments.map((item) => <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink/10 p-3" key={item.id}><div><p className="font-semibold">{item.task.unitTitle}</p><p className="text-sm text-ink/65">Session {item.sessionId} · {item.sessionState} · {item.reviewRequired ? 'awaiting review' : 'reviewed'}</p></div><Link className="button-secondary" to={paths.assessmentExecution(item.id)}>{item.reviewRequired ? 'Review assessment' : 'Open assessment'}</Link></div>)}</div></Surface> : null}
    <Surface className="overflow-hidden"><div className="border-b border-ink/10 p-5 sm:p-6"><SectionHeader title="Recent Session history" description="Older and unlinked Sessions remain readable. Their source is labeled; no Profile domain is inferred from legacy Track data." /></div><div className="divide-y divide-ink/10">{sessions.length ? sessions.map((session) => <article className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6" key={session.id}><div><p className="font-semibold">{session.competencyIdentityId ? titleByCompetency.get(session.competencyIdentityId) ?? 'Legacy competency reference' : 'Unlinked or legacy Session'}</p><p className="mt-1 text-sm capitalize text-ink/65">{session.activityType} · {session.assistanceMode.replaceAll('_', ' ')} · {session.outcome ?? session.timedState ?? 'recorded'}</p></div><div className="text-left sm:text-right"><p className="font-mono font-semibold">{formatDuration(session.durationMs, true)}</p><p className="mt-1 text-xs text-ink/65">{new Date(session.startedAt).toLocaleString()} · Session history</p></div></article>) : <p className="p-6 text-sm text-ink/65">No Session history yet.</p>}</div></Surface>
  </div>
}

function CreateActivityForm({ handoff, onCreated, onError }: { handoff: ActualWorkHandoff | null; onCreated: (activity: Activity) => Promise<void>; onError: (message: string) => void }) {
  const [title, setTitle] = useState(handoff?.reference.title ?? '')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState(handoff?.reference.kind === 'project_task' ? 'project' : 'practice')
  const [busy, setBusy] = useState(false)
  const submit = async (event: React.FormEvent) => { event.preventDefault(); if (busy) return; setBusy(true); onError(''); try { const activity = await apiV2<Activity>('/activities', { method: 'POST', body: JSON.stringify({ title, description: description || null, category_stable_key: category }) }); await onCreated(activity); setDescription('') } catch (caught) { onError(caught instanceof ApiError ? caught.message : 'The Activity could not be created.') } finally { setBusy(false) } }
  const handoffDescription = handoff?.reference.kind === 'competency' ? 'Confirming creates this actual record. The competency contribution is recorded only when you start or log a Session; refresh alone does nothing.' : handoff?.reference.kind === 'unlinked' ? 'Confirming creates and selects this actual record. Returning does not replace a Today suggestion until you explicitly confirm there.' : handoff ? 'Confirming creates this actual record and explicitly relates it to the selected source. Refresh alone does nothing.' : 'Activities name actual work. Creating one does not imply completion, Evidence, or capability.'
  return <Surface className="p-5"><SectionHeader title="Create an Activity" description={handoffDescription} /><form className="mt-4 grid gap-4 md:grid-cols-3" onSubmit={(event) => void submit(event)}><TextField label="Activity title" value={title} onChange={(event) => setTitle(event.target.value)} required /><SelectField label="Category" value={category} onChange={(event) => setCategory(event.target.value)}>{categories.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</SelectField><div className="md:row-span-2"><TextAreaField label="Description · optional" value={description} onChange={(event) => setDescription(event.target.value)} /></div><Button aria-busy={busy} disabled={busy || !title.trim()}>{busy ? 'Creating…' : ['competency', 'unlinked'].includes(handoff?.reference.kind ?? '') ? 'Create and use Activity' : handoff ? 'Create and relate Activity' : 'Create Activity'}</Button></form></Surface>
}

function TimerStart({ activities, selectedActivityId, reference, disabled, onDone, onError }: { activities: Activity[]; selectedActivityId: string; reference: ActualWorkReference | null; disabled: boolean; onDone: () => Promise<void>; onError: (value: string) => void }) {
  const [activityId, setActivityId] = useState(selectedActivityId); const [mode, setMode] = useState('none'); const [busy, setBusy] = useState(false)
  useEffect(() => { if (selectedActivityId) setActivityId(selectedActivityId) }, [selectedActivityId])
  const start = async () => { if (busy || disabled) return; setBusy(true); try { await apiV2('/sessions/timed', { method: 'POST', body: JSON.stringify({ activity_id: activityId, assistance_mode: mode, contributions: contributionFor(reference) }) }); await onDone() } catch (caught) { onError(caught instanceof ApiError ? caught.message : 'The timer could not be started.') } finally { setBusy(false) } }
  return <div className="mt-5 space-y-4"><SelectField id="timer-activity" label="Activity" value={activityId} disabled={busy} onChange={(event) => setActivityId(event.target.value)}><option value="">Create or select an Activity</option>{activities.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</SelectField><SelectField id="timer-assistance" label="Assistance" value={mode} disabled={busy} onChange={(event) => setMode(event.target.value)}>{assistance.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</SelectField>{disabled ? <p className="text-sm text-ink/65">Confirm the handoff above before starting a related Session.</p> : null}<Button className="w-full" aria-busy={busy} disabled={!activityId || disabled || busy} onClick={() => void start()}><Play className="size-4 fill-current" />{busy ? 'Starting…' : 'Start timer'}</Button></div>
}

function ManualForm({ activities, selectedActivityId, reference, disabled, onDone, onError }: { activities: Activity[]; selectedActivityId: string; reference: ActualWorkReference | null; disabled: boolean; onDone: () => Promise<void>; onError: (value: string) => void }) {
  const [activityId, setActivityId] = useState(selectedActivityId); const [mode, setMode] = useState('none'); const [outcome, setOutcome] = useState('completed'); const [minutes, setMinutes] = useState('45'); const [notes, setNotes] = useState(''); const [busy, setBusy] = useState(false)
  useEffect(() => { if (selectedActivityId) setActivityId(selectedActivityId) }, [selectedActivityId])
  const submit = async (event: React.FormEvent) => { event.preventDefault(); if (busy || disabled) return; setBusy(true); try { await apiV2('/sessions/manual', { method: 'POST', body: JSON.stringify({ activity_id: activityId, assistance_mode: mode, started_at: new Date(Date.now() - Number(minutes) * 60_000).toISOString(), duration_ms: Number(minutes) * 60_000, outcome, notes: notes || null, contributions: contributionFor(reference) }) }); setNotes(''); await onDone() } catch (caught) { onError(caught instanceof ApiError ? caught.message : 'The Session could not be logged.') } finally { setBusy(false) } }
  return <form className="mt-5 grid gap-4 sm:grid-cols-2" onSubmit={(event) => void submit(event)}><div className="sm:col-span-2"><SelectField id="manual-activity" label="Activity" value={activityId} disabled={busy} onChange={(event) => setActivityId(event.target.value)}><option value="">Create or select an Activity</option>{activities.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</SelectField></div><SelectField id="manual-assistance" label="Assistance" value={mode} disabled={busy} onChange={(event) => setMode(event.target.value)}>{assistance.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</SelectField><SelectField id="manual-outcome" label="Outcome" value={outcome} disabled={busy} onChange={(event) => setOutcome(event.target.value)}>{['completed', 'partial', 'blocked'].map((item) => <option key={item} value={item}>{item}</option>)}</SelectField><TextField id="manual-duration" label="Duration (minutes)" type="number" min="1" inputMode="numeric" value={minutes} disabled={busy} onChange={(event) => setMinutes(event.target.value)} required /><div className="sm:col-span-2"><TextAreaField id="manual-notes" label="Notes · optional" value={notes} disabled={busy} onChange={(event) => setNotes(event.target.value)} /></div>{disabled ? <p className="text-sm text-ink/65 sm:col-span-2">Confirm the handoff above before saving a related Session.</p> : null}<Button className="sm:col-span-2" aria-busy={busy} disabled={!activityId || disabled || busy}><TimerReset className="size-4" />{busy ? 'Saving…' : 'Save Session'}</Button></form>
}
