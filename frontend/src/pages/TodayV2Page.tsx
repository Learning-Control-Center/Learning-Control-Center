import { Check, Clock3, Play, RefreshCw, SkipForward, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiV2, formatDuration } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/PageState'

type TodayInteraction = {
  id: string
  type: string
  sessionId: string | null
  correction: { id: string } | null
}

type ActivityRelation = {
  id: string
  activityId: string
  type: 'matched' | 'partially_matched' | 'replaced'
}

type TodaySuggestion = {
  id: string
  portfolioRole: 'primary' | 'complementary' | 'maintenance'
  advisoryDurationMs: number | null
  durationRangeMs: [number, number, number] | null
  presentation: {
    title: string
    description: string
    candidateType: string
    reasonSummary: string
    reasons: { code: string; text: string }[]
  }
  timezone: string
  expiresAt: string
  presentationExpired: boolean
  status: string
  terminal: boolean
  interactions: TodayInteraction[]
  activityRelations: ActivityRelation[]
}

type TodayGeneration = {
  id: string
  localDate: string
  timezone: string
  generationSequence: number
  generationKey: string
  generatedAt: string
  regeneration: boolean
  suggestions: TodaySuggestion[]
}

type CurrentToday = {
  generation: TodayGeneration | null
  continuingStartedSuggestions: TodaySuggestion[]
}
type CurrentAnalysis = { status: string; snapshot: { id: string } | null }

const titleCase = (value: string) => value.replaceAll('_', ' ')

export function TodayV2Page() {
  const [today, setToday] = useState<CurrentToday>({
    generation: null,
    continuingStartedSuggestions: [],
  })
  const [availableMinutes, setAvailableMinutes] = useState('')
  const [activityIds, setActivityIds] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setToday(await apiV2<CurrentToday>('/today/current'))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Today V2 could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => void load(), [load])

  const generationCommand = async () => {
    setWorking('generation')
    setError('')
    try {
      const analysis = await apiV2<CurrentAnalysis>('/analysis/current')
      if (!analysis.snapshot || analysis.status !== 'current') {
        throw new Error('A current Analysis V3 snapshot is required before generating Today V2.')
      }
      const parsed = availableMinutes.trim() ? Number(availableMinutes) : null
      if (parsed !== null && (!Number.isInteger(parsed) || parsed < 0)) {
        throw new Error('Available time must be a whole number of minutes.')
      }
      await apiV2(today.generation ? '/today/regenerations' : '/today/generations', {
        method: 'POST',
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          analysis_snapshot_id: analysis.snapshot.id,
          available_time_ms: parsed === null ? null : parsed * 60_000,
        }),
      })
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Today V2 could not be generated.')
    } finally {
      setWorking('')
    }
  }

  const command = async (suggestion: TodaySuggestion, action: string, body: object = {}) => {
    setWorking(`${suggestion.id}:${action}`)
    setError('')
    try {
      await apiV2(`/today/suggestions/${suggestion.id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID(), ...body }),
      })
      await load()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The Today action failed.')
    } finally {
      setWorking('')
    }
  }

  const completionSession = (suggestion: TodaySuggestion) =>
    [...suggestion.interactions].reverse().find((item) => item.type === 'started')?.sessionId

  const correctLatestInteraction = async (suggestion: TodaySuggestion) => {
    const latest = suggestion.interactions.at(-1)
    if (!latest || latest.correction) return
    setWorking(`${suggestion.id}:correct-interaction`)
    setError('')
    try {
      await apiV2(`/today/interactions/${latest.id}/corrections`, {
        method: 'POST',
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          reason: 'Corrected from Today V2',
        }),
      })
      await load()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'The status correction failed.')
    } finally {
      setWorking('')
    }
  }

  const expiresLabel = (suggestion: TodaySuggestion) =>
    new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: suggestion.timezone,
    }).format(new Date(suggestion.expiresAt))

  if (loading) return <LoadingState label="Loading advisory Today history" />
  if (error && !today.generation && !today.continuingStartedSuggestions.length) {
    return <ErrorState message={error} retry={() => void load()} />
  }

  return (
    <div className="mx-auto w-full max-w-[86rem]">
      <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow mb-2">Advisory portfolio</p>
          <h1 className="page-title">Today V2</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink/60">
            Suggestions can be accepted, replaced, or ignored. Only actual logged work becomes
            Activity or Evidence; an expired or skipped suggestion creates no debt.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs font-medium text-ink/65">
            Available minutes (optional)
            <input
              className="mt-1 block w-44 rounded-xl border border-ink/15 bg-white px-3 py-2 text-sm"
              min="0"
              onChange={(event) => setAvailableMinutes(event.target.value)}
              placeholder="Unknown"
              step="5"
              type="number"
              value={availableMinutes}
            />
          </label>
          <button
            className="button-primary min-h-10"
            disabled={working === 'generation'}
            onClick={() => void generationCommand()}
            type="button"
          >
            <RefreshCw className={`size-4 ${working === 'generation' ? 'animate-spin' : ''}`} />
            {today.generation ? 'Regenerate explicitly' : 'Generate Today'}
          </button>
        </div>
      </header>

      {error ? <div className="mb-5"><ErrorState message={error} /></div> : null}
      {!today.generation && !today.continuingStartedSuggestions.length ? (
        <EmptyState
          title="Today V2 has not been generated"
          detail="Generation is explicit. Reading this page never creates recommendations or marks suggestions viewed."
        />
      ) : (
        <>
          {today.continuingStartedSuggestions.length ? (
            <section className="surface mb-5 border-l-4 border-copper p-5">
              <p className="eyebrow">Continuing actual work</p>
              <p className="mt-1 text-sm text-ink/60">
                These sessions were started from an earlier generation and remain actual work after regeneration.
              </p>
              <div className="mt-4 space-y-3">
                {today.continuingStartedSuggestions.map((suggestion) => {
                  const sessionId = completionSession(suggestion)
                  const busy = working.startsWith(`${suggestion.id}:`)
                  return (
                    <article className="rounded-xl border border-ink/10 p-4" key={suggestion.id}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h2 className="font-display text-lg font-semibold">{suggestion.presentation.title}</h2>
                        <span className="rounded-full bg-copper/10 px-3 py-1 text-xs font-medium text-copper">Started</span>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {sessionId ? (
                          <>
                            <button className="button-primary" disabled={busy} onClick={() => void command(suggestion, 'completed', { session_id: sessionId })} type="button">Complete from session</button>
                            <button className="button-secondary" disabled={busy} onClick={() => void command(suggestion, 'partially-completed', { session_id: sessionId })} type="button">Partially complete</button>
                          </>
                        ) : null}
                        <button className="button-secondary" disabled={busy} onClick={() => void correctLatestInteraction(suggestion)} type="button">Correct latest status</button>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <input aria-label={`Replacement Activity ID for continuing ${suggestion.presentation.title}`} className="min-w-64 flex-1 rounded-xl border border-ink/15 bg-white px-3 py-2" onChange={(event) => setActivityIds((items) => ({ ...items, [suggestion.id]: event.target.value }))} placeholder="Actual replacement Activity ID" value={activityIds[suggestion.id] ?? ''} />
                        <button className="button-secondary" disabled={busy || !activityIds[suggestion.id]?.trim()} onClick={() => void command(suggestion, 'replace', { activity_id: activityIds[suggestion.id].trim(), reason_code: 'worked_on_something_else' })} type="button">Record replacement</button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          ) : null}
          {today.generation ? (
            <>
          <section className="surface mb-5 grid gap-4 p-5 sm:grid-cols-3">
            <div><p className="eyebrow">Local day</p><p className="mt-1 text-sm">{today.generation.localDate} · {today.generation.timezone}</p></div>
            <div><p className="eyebrow">Generation</p><p className="mt-1 text-sm">#{today.generation.generationSequence}{today.generation.regeneration ? ' · explicit regeneration' : ''}</p></div>
            <div><p className="eyebrow">Portfolio</p><p className="mt-1 text-sm">{today.generation.suggestions.length} advisory item{today.generation.suggestions.length === 1 ? '' : 's'}</p></div>
          </section>
          <section className="space-y-4">
            {today.generation.suggestions.map((suggestion) => {
              const sessionId = completionSession(suggestion)
              const busy = working.startsWith(`${suggestion.id}:`)
              return (
                <article className="surface p-5" key={suggestion.id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="eyebrow capitalize">{suggestion.portfolioRole}</p>
                      <h2 className="mt-1 font-display text-xl font-semibold">{suggestion.presentation.title}</h2>
                      <p className="mt-2 text-sm text-ink/60">{suggestion.presentation.description}</p>
                    </div>
                    <span className="rounded-full bg-moss/10 px-3 py-1 text-xs font-medium capitalize text-moss">{titleCase(suggestion.status)}</span>
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Advisory duration</p><p className="mt-1 text-sm">{suggestion.advisoryDurationMs == null ? 'Unknown' : formatDuration(suggestion.advisoryDurationMs)}</p></div>
                    <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Candidate</p><p className="mt-1 text-sm capitalize">{titleCase(suggestion.presentation.candidateType)}</p></div>
                    <div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Expires</p><p className="mt-1 flex items-center gap-1 text-sm"><Clock3 className="size-3" />{expiresLabel(suggestion)} ({suggestion.timezone})</p></div>
                  </div>
                  <details className="mt-4 rounded-xl border border-ink/10 p-3">
                    <summary className="cursor-pointer text-sm font-medium">Why this is suggested</summary>
                    <p className="mt-2 text-sm text-ink/65">{suggestion.presentation.reasonSummary}</p>
                    <ul className="mt-2 space-y-1 text-xs text-ink/55">{suggestion.presentation.reasons.map((reason) => <li key={reason.code}>{reason.text}</li>)}</ul>
                  </details>
                  {!suggestion.terminal ? (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {suggestion.status === 'suggested' ? <button className="button-secondary" disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'viewed')} type="button"><Sparkles className="size-4" />Mark viewed</button> : null}
                      {['suggested', 'viewed'].includes(suggestion.status) ? <button className="button-secondary" disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'accepted')} type="button"><Check className="size-4" />Accept</button> : null}
                      {['suggested', 'viewed', 'accepted'].includes(suggestion.status) ? <button className="button-primary" disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'start', { assistance_mode: 'none', contributions: [] })} type="button"><Play className="size-4" />Start actual work</button> : null}
                      {suggestion.status === 'started' && sessionId ? <><button className="button-primary" disabled={busy} onClick={() => void command(suggestion, 'completed', { session_id: sessionId })} type="button"><Check className="size-4" />Complete from session</button><button className="button-secondary" disabled={busy} onClick={() => void command(suggestion, 'partially-completed', { session_id: sessionId })} type="button">Partially complete</button></> : null}
                      {['suggested', 'viewed', 'accepted'].includes(suggestion.status) ? <button className="button-secondary" disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'skipped', { reason_code: 'user_skipped' })} type="button"><SkipForward className="size-4" />Skip without debt</button> : null}
                      {suggestion.interactions.length && !suggestion.interactions.at(-1)?.correction ? <button className="button-secondary" disabled={busy} onClick={() => void correctLatestInteraction(suggestion)} type="button">Correct latest status</button> : null}
                    </div>
                  ) : null}
                  <div className="mt-4 rounded-xl border border-dashed border-ink/15 p-3 text-sm text-ink/60">
                    Doing something else? <Link className="font-medium text-moss underline" to="/sessions">Log the actual Activity</Link>, then optionally enter its ID below to record an explicit replacement—never fake suggested work. {suggestion.presentationExpired ? 'After expiry, replacement remains available only to reconcile actual work truthfully.' : ''}
                    {!suggestion.terminal ? <div className="mt-2 flex flex-wrap gap-2"><input aria-label={`Replacement Activity ID for ${suggestion.presentation.title}`} className="min-w-64 flex-1 rounded-xl border border-ink/15 bg-white px-3 py-2" onChange={(event) => setActivityIds((items) => ({ ...items, [suggestion.id]: event.target.value }))} placeholder="Actual Activity ID" value={activityIds[suggestion.id] ?? ''} /><button className="button-secondary" disabled={busy || !activityIds[suggestion.id]?.trim()} onClick={() => void command(suggestion, 'replace', { activity_id: activityIds[suggestion.id].trim(), reason_code: 'worked_on_something_else' })} type="button">Record replacement</button></div> : null}
                  </div>
                  {suggestion.activityRelations.length ? <div className="mt-4"><p className="eyebrow">Actual Activity relations</p><ul className="mt-2 space-y-2">{suggestion.activityRelations.map((relation) => <li className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-ink/5 p-3 text-xs" key={relation.id}><span>{titleCase(relation.type)} · {relation.activityId}</span><button className="text-copper underline" onClick={async () => { setWorking(`${suggestion.id}:correct`); try { await apiV2(`/today/relations/${relation.id}/corrections`, { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), correction_type: 'retracted', reason: 'Corrected from Today V2' }) }); await load() } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'The relation correction failed.') } finally { setWorking('') } }} type="button">Retract relation</button></li>)}</ul></div> : null}
                </article>
              )
            })}
          </section>
            </>
          ) : null}
        </>
      )}
    </div>
  )
}
