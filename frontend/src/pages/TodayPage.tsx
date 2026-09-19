import { ArrowRight, BookOpen, CheckCircle2, Clock3, Lightbulb, Play, Save } from 'lucide-react'
import { motion } from 'motion/react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, api, formatDuration } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'
import type { Recommendation, RecommendationItem, Session } from '../types'

type AnalyticsSummary = {
  activeDays: number
  reviewDebt: { count: number; weightedCount: number }
  signals: { code: string; severity: string; facts: Record<string, unknown> }[]
}

const activityLabels: Record<string, string> = {
  learning: 'Learn the foundations',
  independent_practice: 'Practice independently',
  review: 'Review and retrieve',
  verification: 'Verify the competency',
  research: 'Resolve the blocker',
}

const concreteActivity: Record<string, string> = {
  learning: 'learning',
  independent_practice: 'practice',
  review: 'review',
  verification: 'verification',
  research: 'research',
}

export function TodayPage() {
  const navigate = useNavigate()
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null)
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null)
  const [activeSession, setActiveSession] = useState<Session | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [reflection, setReflection] = useState('')
  const [reflectionSaved, setReflectionSaved] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [next, metrics, active] = await Promise.all([
        api<Recommendation>('/recommendations/today'),
        api<AnalyticsSummary>('/analytics?range=30d'),
        api<{ active: boolean; session: Session | null }>('/sessions/active'),
      ])
      setRecommendation(next)
      setAnalytics(metrics)
      setActiveSession(active.session)
      if (next.localDate) {
        const daily = await api<{ reflection: { text: string } | null }>(`/reflections/${next.localDate}`)
        setReflection(daily.reflection?.text ?? '')
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Today could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const startItem = async (item: RecommendationItem, acceptedPrimary: boolean) => {
    setStarting(true)
    try {
      await api('/sessions/timed', {
        method: 'POST',
        body: JSON.stringify({
          competency_identity_id: item.competencyIdentityId,
          activity_type: concreteActivity[item.activity],
          assistance_mode: 'none',
        }),
      })
      if (recommendation?.snapshotId) {
        await api(`/recommendations/${recommendation.snapshotId}/decision`, {
          method: 'POST',
          body: JSON.stringify({
            accepted_primary: acceptedPrimary,
            chosen_competency_identity_id: item.competencyIdentityId,
          }),
        })
      }
      navigate('/activity')
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The session could not be started.')
    } finally {
      setStarting(false)
    }
  }

  const dismissRecommendation = async () => {
    if (!recommendation?.snapshotId) return
    try {
      await api(`/recommendations/${recommendation.snapshotId}/decision`, {
        method: 'POST',
        body: JSON.stringify({ accepted_primary: false, chosen_competency_identity_id: null }),
      })
      setDismissed(true)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The decision could not be recorded.')
    }
  }

  const saveReflection = async () => {
    if (!recommendation?.localDate) return
    try {
      await api(`/reflections/${recommendation.localDate}`, {
        method: 'PUT',
        body: JSON.stringify({ text: reflection }),
      })
      setReflectionSaved(true)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The reflection could not be saved.')
    }
  }

  if (loading) return <LoadingState label="Preparing today’s recommendation" />
  if (error && !recommendation) return <ErrorState message={error} retry={() => void load()} />
  if (!recommendation?.primary) {
    return (
      <div className="mx-auto w-full max-w-[88rem]">
        <Header />
        <EmptyState
          title={recommendation?.setupRequired ? 'A roadmap is needed' : 'No eligible competency right now'}
          detail={recommendation?.guidance ?? 'Configure a roadmap to begin the learning loop.'}
          action={
            <Link className="button-primary mt-2" to={recommendation?.setupRequired ? '/transfer' : '/roadmap'}>
              {recommendation?.setupRequired ? 'Import roadmap' : 'Review roadmap'}
            </Link>
          }
        />
      </div>
    )
  }

  const progress = recommendation.todayTargetDurationMs
    ? Math.min(100, ((recommendation.todayCompletedDurationMs ?? 0) / recommendation.todayTargetDurationMs) * 100)
    : 0

  return (
    <div className="mx-auto w-full max-w-[88rem]">
      <Header />
      {error ? <p className="mb-5 rounded-xl bg-rose-50 p-3 text-sm text-rose-800" role="alert">{error}</p> : null}
      {dismissed ? <p className="mb-5 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">Recommendation dismissed. The decision was recorded without changing roadmap state.</p> : null}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(19rem,0.65fr)]">
        <motion.section
          className="surface relative overflow-hidden p-6 sm:p-8"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="absolute right-0 top-0 size-52 -translate-y-1/3 translate-x-1/3 rounded-full bg-fern/15 blur-2xl" />
          <div className="relative">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="eyebrow">Primary focus</span>
              {recommendation.limitedTelemetry ? (
                <span className="rounded-full bg-copper/10 px-3 py-1 text-xs font-medium text-copper">Limited telemetry</span>
              ) : null}
            </div>
            <h2 className="mt-6 max-w-3xl font-display text-4xl font-semibold leading-tight tracking-[-0.03em] sm:text-5xl">
              {recommendation.primary.title}
            </h2>
            <div className="mt-5 flex flex-wrap items-center gap-3 text-sm">
              <span className="rounded-full bg-moss px-3 py-1.5 font-semibold text-white">
                {activityLabels[recommendation.primary.activity]}
              </span>
              <span className="flex items-center gap-2 text-ink/55">
                <Clock3 className="size-4" aria-hidden="true" />
                {formatDuration(recommendation.primary.suggestedDurationMs)} suggested
              </span>
            </div>
            <div className="mt-8 rounded-2xl border border-moss/15 bg-moss/[0.055] p-5">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <Lightbulb className="size-4 text-copper" aria-hidden="true" /> Why this?
              </div>
              <ul className="grid gap-2 text-sm leading-6 text-ink/65 sm:grid-cols-2">
                {recommendation.primary.reasonCodes.length ? (
                  recommendation.primary.reasonCodes.map((reason) => (
                    <li className="flex gap-2" key={reason}>
                      <CheckCircle2 className="mt-1 size-4 shrink-0 text-moss" aria-hidden="true" />
                      {reason.replaceAll('_', ' ').toLowerCase()}
                    </li>
                  ))
                ) : (
                  <li>The roadmap and current evidence make this the strongest eligible next step.</li>
                )}
              </ul>
            </div>
            <div className="mt-7 flex flex-wrap gap-3">
              {activeSession ? (
                <Link className="button-primary" to="/activity">
                  Return to active session <ArrowRight className="size-4" />
                </Link>
              ) : (
                <button className="button-primary" onClick={() => void startItem(recommendation.primary!, true)} disabled={starting || dismissed}>
                  <Play className="size-4 fill-current" aria-hidden="true" />
                  {starting ? 'Starting…' : 'Start focused session'}
                </button>
              )}
              <Link className="button-secondary" to="/activity">
                Log manually
              </Link>
              <button className="button-secondary" onClick={() => void dismissRecommendation()} disabled={dismissed}>Dismiss</button>
            </div>
          </div>
        </motion.section>

        <div className="grid gap-5">
          <section className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <p className="eyebrow">Today</p>
              <BookOpen className="size-4 text-moss" aria-hidden="true" />
            </div>
            <p className="mt-5 font-display text-3xl font-semibold">
              {formatDuration(recommendation.todayCompletedDurationMs ?? 0)}
            </p>
            <p className="mt-1 text-sm text-ink/50">
              of {formatDuration(recommendation.todayTargetDurationMs)} target
            </p>
            <div className="mt-5 h-2 overflow-hidden rounded-full bg-ink/10" aria-label={`${Math.round(progress)}% of target`}>
              <motion.div
                className="h-full rounded-full bg-moss"
                initial={{ width: 0 }}
                animate={{ width: `${progress}%` }}
                transition={{ duration: 0.5 }}
              />
            </div>
          </section>
          <section className="surface p-5 sm:p-6">
            <p className="eyebrow">Learning pulse</p>
            <dl className="mt-5 grid grid-cols-2 gap-4">
              <div>
                <dt className="text-xs text-ink/45">Active days · 30d</dt>
                <dd className="mt-1 font-display text-2xl font-semibold">{analytics?.activeDays ?? 0}</dd>
              </div>
              <div>
                <dt className="text-xs text-ink/45">Reviews due</dt>
                <dd className="mt-1 font-display text-2xl font-semibold">{analytics?.reviewDebt.count ?? 0}</dd>
              </div>
            </dl>
          </section>
        </div>
      </div>

      {recommendation.secondary ? (
        <section className="surface mt-5 flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
          <div>
            <p className="eyebrow">Secondary option</p>
            <h3 className="mt-2 font-display text-xl font-semibold">{recommendation.secondary.title}</h3>
            <p className="mt-1 text-sm text-ink/55">
              {activityLabels[recommendation.secondary.activity]} · {formatDuration(recommendation.secondary.suggestedDurationMs)}
            </p>
          </div>
          <button className="button-secondary" onClick={() => void startItem(recommendation.secondary!, false)} disabled={starting || Boolean(activeSession)}><Play className="size-4" />Choose secondary</button>
        </section>
      ) : null}
      <section className="surface mt-5 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="eyebrow">Daily reflection</p><h3 className="mt-2 font-display text-xl font-semibold">Keep your interpretation separate</h3></div>{reflectionSaved ? <span className="text-xs font-medium text-moss">Saved</span> : null}</div>
        <p className="mt-2 text-sm text-ink/50">Generated reports remain immutable; this note stays editable as your own reflection.</p>
        <textarea className="field mt-4 min-h-28 resize-y" value={reflection} onChange={(event) => { setReflection(event.target.value); setReflectionSaved(false) }} placeholder="What changed in your understanding today?" />
        <button className="button-primary mt-3" onClick={() => void saveReflection()}><Save className="size-4" />Save reflection</button>
      </section>
    </div>
  )
}

function Header() {
  return (
    <header className="mb-7 flex items-end justify-between gap-4">
      <div>
        <p className="eyebrow mb-2">Operational home</p>
        <h1 className="page-title">Today</h1>
        <p className="mt-2 text-sm text-ink/60">One useful next step, grounded in your roadmap and evidence.</p>
      </div>
    </header>
  )
}
