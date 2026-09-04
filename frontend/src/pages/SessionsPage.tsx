import { Pause, Play, Square, TimerReset, XCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, api, formatDuration } from '../api'
import { ErrorState, LoadingState } from '../components/PageState'
import type { Competency, Roadmap, Session } from '../types'

const activities = ['learning', 'reading', 'practice', 'coding', 'debugging', 'project', 'review', 'verification', 'research']
const assistance = ['none', 'docs_only', 'ai_hint', 'ai_assisted', 'agent_led']

export function SessionsPage() {
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null)
  const [active, setActive] = useState<Session | null>(null)
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tick, setTick] = useState(Date.now())

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [roadmapResponse, activeResponse, listResponse] = await Promise.all([
        api<{ configured: boolean; roadmap?: Roadmap }>('/roadmap/current'),
        api<{ active: boolean; session: Session | null }>('/sessions/active'),
        api<{ items: Session[] }>('/sessions?limit=30'),
      ])
      setRoadmap(roadmapResponse.roadmap ?? null)
      setActive(activeResponse.session)
      setSessions(listResponse.items)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Sessions could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (active?.timedState !== 'running') return
    const interval = window.setInterval(() => setTick(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [active?.timedState])

  const currentDuration = useMemo(() => {
    if (!active) return 0
    if (active.timedState === 'running' && active.activeSince) return active.accumulatedDurationMs + Math.max(0, tick - Date.parse(active.activeSince))
    return active.durationMs ?? active.accumulatedDurationMs
  }, [active, tick])

  const mutateTimer = async (action: 'pause' | 'resume' | 'cancel' | 'complete') => {
    if (!active) return
    try {
      await api(`/sessions/${active.id}/${action}`, {
        method: 'POST',
        body: action === 'complete' ? JSON.stringify({ outcome: 'completed', notes: active.notes }) : undefined,
      })
      await load()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The timer could not be updated.')
    }
  }

  if (loading) return <LoadingState label="Reconstructing session state" />
  if (error && !roadmap && !active) return <ErrorState message={error} retry={() => void load()} />
  const competencies = roadmap?.phases.flatMap((phase) => phase.tracks.flatMap((track) => track.competencies)) ?? []

  return (
    <div className="mx-auto w-full max-w-[90rem]">
      <header className="mb-7"><p className="eyebrow mb-2">Practice record</p><h1 className="page-title">Log & sessions</h1><p className="mt-2 text-sm text-ink/60">Capture work in under a minute. The server remains the timer authority.</p></header>
      {error ? <p className="mb-5 rounded-xl bg-rose-50 p-3 text-sm text-rose-800" role="alert">{error}</p> : null}
      <div className="grid gap-5 xl:grid-cols-[minmax(20rem,0.75fr)_minmax(0,1.25fr)]">
        <section className="surface p-5 sm:p-6">
          <p className="eyebrow">Active timer</p>
          {active ? (
            <div className="mt-6">
              <p className="font-mono text-5xl font-semibold tracking-[-0.06em] sm:text-6xl" aria-live="polite">{formatDuration(currentDuration, true)}</p>
              <p className="mt-3 text-sm capitalize text-ink/55">{active.activityType} · {active.assistanceMode.replaceAll('_', ' ')} · {active.timedState}</p>
              <div className="mt-7 flex flex-wrap gap-3">
                {active.timedState === 'running' ? <button className="button-secondary" onClick={() => void mutateTimer('pause')}><Pause className="size-4" /> Pause</button> : <button className="button-secondary" onClick={() => void mutateTimer('resume')}><Play className="size-4" /> Resume</button>}
                <button className="button-primary" onClick={() => void mutateTimer('complete')}><Square className="size-4 fill-current" /> Complete</button>
                <button className="button-secondary text-rose-700" onClick={() => void mutateTimer('cancel')}><XCircle className="size-4" /> Cancel</button>
              </div>
              <p className="mt-5 text-xs leading-5 text-ink/45">Cancelling preserves measured time in history but excludes it from learning work and evidence.</p>
            </div>
          ) : (
            <TimerStart competencies={competencies} onDone={load} onError={setError} />
          )}
        </section>
        <section className="surface p-5 sm:p-6">
          <p className="eyebrow">Manual entry</p>
          <ManualForm competencies={competencies} onDone={load} onError={setError} />
        </section>
      </div>
      <section className="surface mt-5 overflow-hidden">
        <div className="border-b border-ink/10 p-5 sm:p-6"><p className="eyebrow">Recent history</p></div>
        <div className="divide-y divide-ink/10">
          {sessions.length ? sessions.map((session) => {
            const competency = competencies.find((item) => item.identityId === session.competencyIdentityId)
            return <article className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6" key={session.id}><div><p className="font-semibold">{competency?.title ?? 'Unlinked learning session'}</p><p className="mt-1 text-sm capitalize text-ink/50">{session.activityType} · {session.assistanceMode.replaceAll('_', ' ')} · {session.outcome ?? session.timedState ?? 'recorded'}</p></div><div className="text-left sm:text-right"><p className="font-mono font-semibold">{formatDuration(session.durationMs, true)}</p><p className="mt-1 text-xs text-ink/40">{new Date(session.startedAt).toLocaleString()}</p></div></article>
          }) : <p className="p-6 text-sm text-ink/50">No session history yet.</p>}
        </div>
      </section>
    </div>
  )
}

function TimerStart({ competencies, onDone, onError }: { competencies: Competency[]; onDone: () => Promise<void>; onError: (value: string) => void }) {
  const [competency, setCompetency] = useState(competencies[0]?.identityId ?? '')
  const [activity, setActivity] = useState('practice')
  const [mode, setMode] = useState('none')
  const start = async () => {
    try {
      await api('/sessions/timed', { method: 'POST', body: JSON.stringify({ competency_identity_id: competency || null, activity_type: activity, assistance_mode: mode }) })
      await onDone()
    } catch (caught) { onError(caught instanceof ApiError ? caught.message : 'The timer could not be started.') }
  }
  return <div className="mt-5 space-y-4"><Select label="Competency" value={competency} setValue={setCompetency} options={competencies.map((item) => ({ value: item.identityId, label: item.title }))} includeEmpty /><Select label="Activity" value={activity} setValue={setActivity} options={activities.map((item) => ({ value: item, label: item }))} /><Select label="Assistance" value={mode} setValue={setMode} options={assistance.map((item) => ({ value: item, label: item.replaceAll('_', ' ') }))} /><button className="button-primary w-full" onClick={() => void start()}><Play className="size-4 fill-current" /> Start timer</button></div>
}

function ManualForm({ competencies, onDone, onError }: { competencies: Competency[]; onDone: () => Promise<void>; onError: (value: string) => void }) {
  const [competency, setCompetency] = useState(competencies[0]?.identityId ?? '')
  const [activity, setActivity] = useState('practice')
  const [mode, setMode] = useState('none')
  const [outcome, setOutcome] = useState('completed')
  const [minutes, setMinutes] = useState('45')
  const [notes, setNotes] = useState('')
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await api('/sessions/manual', { method: 'POST', body: JSON.stringify({ competency_identity_id: competency || null, activity_type: activity, assistance_mode: mode, started_at: new Date(Date.now() - Number(minutes) * 60_000).toISOString(), duration_ms: Number(minutes) * 60_000, outcome, notes: notes || null }) })
      setNotes(''); await onDone()
    } catch (caught) { onError(caught instanceof ApiError ? caught.message : 'The session could not be logged.') }
  }
  return <form className="mt-5 grid gap-4 sm:grid-cols-2" onSubmit={(event) => void submit(event)}><div className="sm:col-span-2"><Select label="Competency" value={competency} setValue={setCompetency} options={competencies.map((item) => ({ value: item.identityId, label: item.title }))} includeEmpty /></div><Select label="Activity" value={activity} setValue={setActivity} options={activities.map((item) => ({ value: item, label: item }))} /><Select label="Assistance" value={mode} setValue={setMode} options={assistance.map((item) => ({ value: item, label: item.replaceAll('_', ' ') }))} /><label className="text-sm font-medium">Duration (minutes)<input className="field mt-2" type="number" min="1" inputMode="numeric" value={minutes} onChange={(event) => setMinutes(event.target.value)} required /></label><Select label="Outcome" value={outcome} setValue={setOutcome} options={['completed', 'partial', 'blocked'].map((item) => ({ value: item, label: item }))} /><label className="text-sm font-medium sm:col-span-2">Notes · optional<textarea className="field mt-2 min-h-24 resize-y" value={notes} onChange={(event) => setNotes(event.target.value)} /></label><button className="button-primary sm:col-span-2"><TimerReset className="size-4" /> Save session</button></form>
}

function Select({ label, value, setValue, options, includeEmpty = false }: { label: string; value: string; setValue: (value: string) => void; options: { value: string; label: string }[]; includeEmpty?: boolean }) { return <label className="block text-sm font-medium">{label}<select className="field mt-2 capitalize" value={value} onChange={(event) => setValue(event.target.value)}>{includeEmpty ? <option value="">Unlinked</option> : null}{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label> }
