import { Check, Clock3, Play, RefreshCw, SkipForward } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, api, apiV2, formatDuration } from '../../api'
import { useActiveSession } from '../../shared/session/ActiveSessionProvider'
import { AuditDisclosure, Button, EmptyState, ErrorState, LiveNotice, LoadingState, MutationError, PageHeader, ReasonList, SectionError, SectionHeader, StatusBadge, Surface } from '../../shared/components'
import { activityHandoffPath } from '../../shared/contracts/learningReferences'
import type { CurriculumCatalogUnit, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import type { RoadmapProjection } from '../../shared/contracts/roadmapProjection'
import { paths } from '../../shared/navigation/paths'
import type { Session } from '../../types'
import { DailyReflectionEditor } from '../reflection'
import type { CurrentToday, TodaySuggestion } from './model'

type CurrentAnalysis = { status: string; snapshot: { id: string } | null }
type SourceDestination = { href?: string; label: string }
type AssessmentCriterion = { criterionDefinitionId: string; criterionStableKey: string; description: string; rubricCheck: string }
type AssessmentTask = { unitDefinitionId: string; opportunityId: string; unitTitle: string; action: { instructions?: string; verificationMethod?: string }; criteria: AssessmentCriterion[]; requiresArtifact: boolean; intendedStrengths: string[] }
type AssessmentExecution = { id: string; sessionId: string; sessionState: string; sessionOutcome: string | null; assistanceMode: string; reviewRequired: boolean; task: AssessmentTask; reviews: { id: string }[] }
type LegacyAssessment = { kind: 'legacy_started'; suggestionId: string; sessionId: string; message: string }
const label = (value: string) => value.replaceAll('_', ' ')

export function TodayV2Page() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { active, refresh: refreshActiveSession } = useActiveSession()
  const [today, setToday] = useState<CurrentToday>({ generation: null, continuingStartedSuggestions: [] })
  const [availableMinutes, setAvailableMinutes] = useState('')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState('')
  const [loadError, setLoadError] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [notice, setNotice] = useState('')
  const [generationOptionsOpen, setGenerationOptionsOpen] = useState(false)
  const [sessions, setSessions] = useState<Session[]>([])
  const [sourceLinks, setSourceLinks] = useState<Map<string, SourceDestination>>(new Map())
  const replacementSuggestionId = searchParams.get('replaceSuggestion')
  const returnedActivityId = searchParams.get('activityResultStatus') === 'selected' ? searchParams.get('activityResult') : null

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setLoadError('')
    try {
      const [todayResult, sessionsResult, curriculumResult, projectResult, roadmapResult] = await Promise.allSettled([
        apiV2<CurrentToday>('/today/current', { signal }),
        api<{ items: Session[] }>('/sessions?limit=100', { signal }),
        apiV2<{ units: CurriculumCatalogUnit[] }>('/curricula/catalog/active', { signal }),
        apiV2<{ candidates: ProjectCatalogCandidateApi[] }>('/projects/catalog/current', { signal }),
        apiV2<RoadmapProjection>('/roadmap-projection/current', { signal }),
      ])
      if (todayResult.status === 'rejected') throw todayResult.reason
      setToday(todayResult.value)
      if (sessionsResult.status === 'fulfilled') setSessions(sessionsResult.value.items)
      const links = new Map<string, SourceDestination>()
      if (curriculumResult.status === 'fulfilled') for (const unit of curriculumResult.value.units) {
        const destination = { href: paths.curriculum(unit.curriculumId), label: `Open learning unit: ${unit.title}` }
        links.set(`curriculum_unit:${unit.unitDefinitionId}`, destination)
        if (unit.curriculumVersionId) links.set(`curriculum_version:${unit.curriculumVersionId}`, { href: paths.curriculum(unit.curriculumId), label: 'Open source Curriculum' })
      }
      if (projectResult.status === 'fulfilled') for (const task of projectResult.value.candidates) {
        links.set(`project_task:${task.task_definition_id}`, { href: paths.project(task.project_id), label: `Open project task: ${task.title}` })
        if (task.project_version_id) links.set(`project_version:${task.project_version_id}`, { href: paths.project(task.project_id), label: `Open source Project: ${task.title}` })
      }
      if (roadmapResult.status === 'fulfilled') for (const node of roadmapResult.value.nodes ?? []) for (const target of node.profileTargets ?? (node.profileTarget ? [node.profileTarget] : [])) {
        links.set(`competency:${node.id}`, { href: paths.competency(node.id), label: `Open competency: ${node.title}` })
        for (const targetId of [target.id, target.identityId].filter((value): value is string => Boolean(value))) links.set(`analysis_target:${targetId}`, { href: paths.competency(node.id), label: `Open competency: ${node.title}` })
      }
      setSourceLinks(links)
    }
    catch (caught) { if ((caught as Error).name !== 'AbortError') setLoadError(caught instanceof ApiError ? caught.message : 'Today could not be loaded.') }
    finally { if (!signal?.aborted) setLoading(false) }
  }, [])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])

  const allSuggestions = useMemo(() => [...(today.generation?.suggestions ?? []), ...today.continuingStartedSuggestions], [today])
  const replacementSuggestion = allSuggestions.find((item) => item.id === replacementSuggestionId)
  const clearReplacement = () => setSearchParams({}, { replace: true })

  const generationCommand = async () => {
    if (working) return
    setWorking('generation'); setMutationError(''); setNotice('')
    try {
      const analysis = await apiV2<CurrentAnalysis>('/analysis/current')
      if (!analysis.snapshot || analysis.status !== 'current') throw new Error('A current Analysis V3 snapshot is required before generating Today.')
      const parsed = availableMinutes.trim() ? Number(availableMinutes) : null
      if (parsed !== null && (!Number.isInteger(parsed) || parsed < 0)) throw new Error('Available time must be a whole number of minutes.')
      const generation = await apiV2<NonNullable<CurrentToday['generation']>>(today.generation ? '/today/regenerations' : '/today/generations', { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), analysis_snapshot_id: analysis.snapshot.id, available_time_ms: parsed === null ? null : parsed * 60_000 }) })
      setToday((current) => ({ ...current, generation }))
      setNotice(today.generation ? 'Today was explicitly regenerated. Expired or omitted items created no debt.' : 'Today was generated explicitly.')
      await load()
    } catch (caught) { setMutationError(caught instanceof Error ? caught.message : 'Today could not be generated.') }
    finally { setWorking('') }
  }

  const command = async (suggestion: TodaySuggestion, action: string, body: object = {}) => {
    if (working) return false
    setWorking(`${suggestion.id}:${action}`); setMutationError(''); setNotice('')
    try {
      const updated = await apiV2<TodaySuggestion>(`/today/suggestions/${suggestion.id}/${action}`, { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), ...body }) })
      setToday((current) => updateSuggestion(current, updated))
      if (['start', 'restart-legacy-assessment', 'completed', 'partially-completed'].includes(action)) await refreshActiveSession()
      setNotice(action === 'skipped' ? 'Suggestion skipped. No debt or completion was created.' : `Today status updated: ${label(action)}.`)
      await load()
      return true
    } catch (caught) {
      setMutationError(caught instanceof ApiError ? caught.message : 'The Today action failed.')
      return false
    }
    finally { setWorking('') }
  }

  const confirmReplacement = async () => {
    if (!replacementSuggestion || !returnedActivityId || working) return
    if (await command(replacementSuggestion, 'replace', { activity_id: returnedActivityId, reason_code: 'worked_on_something_else' })) clearReplacement()
  }

  const correctLatestInteraction = async (suggestion: TodaySuggestion) => {
    const latest = suggestion.interactions.at(-1)
    if (!latest || latest.correction || working) return
    setWorking(`${suggestion.id}:correct-interaction`); setMutationError(''); setNotice('')
    try { await apiV2(`/today/interactions/${latest.id}/corrections`, { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), reason: 'Corrected from Today' }) }); setNotice('The latest Today status was corrected.'); await load() }
    catch (caught) { setMutationError(caught instanceof ApiError ? caught.message : 'The status correction failed.') }
    finally { setWorking('') }
  }

  const correctRelation = async (suggestion: TodaySuggestion, relationId: string) => {
    if (working) return
    setWorking(`${suggestion.id}:correct-relation`); setMutationError(''); setNotice('')
    try { await apiV2(`/today/relations/${relationId}/corrections`, { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), correction_type: 'retracted', reason: 'Corrected from Today' }) }); setNotice('The Activity relation was retracted without rewriting history.'); await load() }
    catch (caught) { setMutationError(caught instanceof ApiError ? caught.message : 'The relation correction failed.') }
    finally { setWorking('') }
  }

  if (loading) return <LoadingState label="Loading advisory Today state" />
  if (loadError && !today.generation && !today.continuingStartedSuggestions.length) return <div className="space-y-6"><PageHeader eyebrow="Daily control loop" title="Today" description="Advisory work for the current local day." /><ErrorState message={loadError} retry={() => void load()} /></div>

  return <div className="mx-auto w-full max-w-[86rem] space-y-6">
    <LiveNotice>{notice}</LiveNotice>
    <PageHeader eyebrow="Daily control loop" title="Today" description="Choose a sensible next action, continue actual work, or do something else. Skipping, replacement, and expiry create no debt." actions={<div className="w-full sm:w-auto"><Button className="w-full justify-between sm:hidden" variant="secondary" aria-expanded={generationOptionsOpen} aria-controls="today-generation-options" onClick={() => setGenerationOptionsOpen((open) => !open)}><RefreshCw className="size-4" />Generation options</Button><div id="today-generation-options" className={`${generationOptionsOpen ? 'mt-3 flex' : 'hidden'} flex-wrap items-end gap-2 sm:mt-0 sm:flex`}><label className="text-xs font-medium text-ink/65">Available minutes (optional)<input className="mt-1 block w-44 rounded-xl border border-ink/15 bg-white px-3 py-2 text-sm" min="0" step="5" type="number" value={availableMinutes} placeholder="Unknown" onChange={(event) => setAvailableMinutes(event.target.value)} /></label><Button disabled={Boolean(working)} aria-busy={working === 'generation'} onClick={() => void generationCommand()}><RefreshCw className={`size-4 ${working === 'generation' ? 'animate-spin' : ''}`} />{today.generation ? 'Regenerate explicitly' : 'Generate Today'}</Button></div></div>} />
    {mutationError ? <MutationError>{mutationError}</MutationError> : null}{loadError ? <SectionError message={loadError} retry={() => void load()} /> : null}
    {returnedActivityId ? <Surface className="border-moss/30 bg-moss/5 p-5"><SectionHeader title={replacementSuggestion ? `Confirm replacement for ${replacementSuggestion.presentation.title}` : 'Replacement context unavailable'} description={replacementSuggestion ? 'The Activity was selected, but no relation or Today status has changed. Confirm to record truthful actual work as the replacement.' : 'The originating suggestion is no longer present in current or continuing Today state. No change was made.'} /><div className="mt-4 flex flex-wrap gap-2"><Button disabled={!replacementSuggestion || Boolean(working)} onClick={() => void confirmReplacement()}>Confirm replacement</Button><Button variant="secondary" onClick={clearReplacement}>Cancel</Button></div></Surface> : null}
    {today.continuingStartedSuggestions.length ? <Surface className="border-l-4 border-copper p-5"><SectionHeader title="Continue actual work" description="Started Sessions remain actual work even when a newer Today generation exists." /><div className="mt-4 space-y-3">{today.continuingStartedSuggestions.map((suggestion) => <SuggestionCard key={suggestion.id} suggestion={suggestion} sessions={sessions} sourceLink={sourceDestination(suggestion, sourceLinks)} activeSessionId={active?.id ?? null} working={working} command={command} correct={correctLatestInteraction} />)}</div></Surface> : null}
    {!today.generation && !today.continuingStartedSuggestions.length ? <EmptyState title="Today has not been generated" detail="Generation is explicit. Reading or refreshing this page never creates a recommendation, interaction, completion, or debt." /> : null}
    {today.generation ? <><section aria-labelledby="today-portfolio-heading"><SectionHeader headingId="today-portfolio-heading" title="What makes sense now" description="Primary, complementary, and maintenance roles come from Recommendation V2 policy. They are advice, not assignments." /><div className="mt-4 space-y-4">{today.generation.suggestions.map((suggestion) => <SuggestionCard key={suggestion.id} suggestion={suggestion} sessions={sessions} sourceLink={sourceDestination(suggestion, sourceLinks)} activeSessionId={active?.id ?? null} working={working} command={command} correct={correctLatestInteraction} />)}{!today.generation.suggestions.length ? <EmptyState title="No useful eligible work" detail="The empty advisory portfolio is preserved truthfully; no recommendation was manufactured." /> : null}</div></section><Surface className="grid gap-4 p-5 sm:grid-cols-3"><div><p className="eyebrow">Local day</p><p className="mt-1 text-sm">{today.generation.localDate} · {today.generation.timezone}</p></div><div><p className="eyebrow">Generation</p><p className="mt-1 text-sm">#{today.generation.generationSequence}{today.generation.regeneration ? ' · explicit regeneration' : ''}</p></div><div><p className="eyebrow">Advisory portfolio</p><p className="mt-1 text-sm">{today.generation.suggestions.length} item{today.generation.suggestions.length === 1 ? '' : 's'}</p></div></Surface></> : null}
    {allSuggestions.some((suggestion) => suggestion.status === 'suggested' || (suggestion.terminal && suggestion.interactions.length && !suggestion.interactions.at(-1)?.correction)) ? <Surface className="p-5"><SectionHeader title="Status review and correction" description="Viewing is explicit but optional. Corrections append history and do not create work or debt." /><ul className="mt-4 space-y-2">{allSuggestions.filter((suggestion) => suggestion.status === 'suggested' || (suggestion.terminal && suggestion.interactions.length && !suggestion.interactions.at(-1)?.correction)).map((suggestion) => <li className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink/10 p-3" key={suggestion.id}><p className="font-medium">{suggestion.presentation.title}</p>{suggestion.status === 'suggested' ? <Button variant="secondary" disabled={Boolean(working) || suggestion.presentationExpired} onClick={() => void command(suggestion, 'viewed')}>Mark viewed</Button> : <Button variant="secondary" disabled={Boolean(working)} onClick={() => void correctLatestInteraction(suggestion)}>Correct terminal status</Button>}</li>)}</ul></Surface> : null}
    {allSuggestions.some((suggestion) => suggestion.activityRelations.length) ? <Surface className="p-5"><SectionHeader title="Correct Activity relations" description="Corrections append history; they never rewrite the original relation or logged work." /><ul className="mt-4 space-y-2">{allSuggestions.flatMap((suggestion) => suggestion.activityRelations.map((relation) => <li className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink/10 p-3" key={relation.id}><div><p className="font-medium">{suggestion.presentation.title}</p><p className="text-sm text-ink/65">{label(relation.type)} Activity relation</p></div><Button variant="secondary" disabled={Boolean(working)} onClick={() => void correctRelation(suggestion, relation.id)}>Retract relation</Button></li>))}</ul></Surface> : null}
    <DailyReflectionEditor localDate={requestedReflectionDate(searchParams.get('reflectionDate')) ?? today.generation?.localDate} />
  </div>
}

function requestedReflectionDate(value: string | null) { return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null }

function updateSuggestion(current: CurrentToday, updated: TodaySuggestion): CurrentToday {
  const replace = (items: TodaySuggestion[]) => items.map((item) => item.id === updated.id ? updated : item)
  return {
    generation: current.generation ? { ...current.generation, suggestions: replace(current.generation.suggestions) } : null,
    continuingStartedSuggestions: replace(current.continuingStartedSuggestions),
  }
}

function sourceDestination(suggestion: TodaySuggestion, links: Map<string, SourceDestination>): SourceDestination {
  const source = suggestion.presentation.source
  const exact = source ? links.get(`${source.type}:${source.entityId}`) : undefined
  if (exact) return exact
  if (source?.type === 'assessment_rubric' && source.versionId) {
    const curriculum = links.get(`curriculum_version:${source.versionId}`)
    if (curriculum) return curriculum
  }
  if (source?.type === 'project_blocker' && source.versionId) {
    const project = links.get(`project_version:${source.versionId}`)
    if (project) return project
  }
  if (suggestion.presentation.competencyIdentityId) {
    const competency = links.get(`competency:${suggestion.presentation.competencyIdentityId}`)
    if (competency) return competency
  }
  for (const targetId of [suggestion.presentation.targetIdentityId, ...(suggestion.presentation.servedTargetIdentityIds ?? [])]) {
    if (!targetId) continue
    const target = links.get(`analysis_target:${targetId}`)
    if (target) return target
  }
  return { label: 'Relevant source unavailable from current catalogs' }
}

function SuggestionCard({ suggestion, sessions, sourceLink, activeSessionId, working, command, correct }: { suggestion: TodaySuggestion; sessions: Session[]; sourceLink: SourceDestination; activeSessionId: string | null; working: string; command: (suggestion: TodaySuggestion, action: string, body?: object) => Promise<boolean>; correct: (suggestion: TodaySuggestion) => Promise<void> }) {
  const busy = working.startsWith(`${suggestion.id}:`)
  const sessionId = [...suggestion.interactions].reverse().find((item) => item.type === 'started')?.sessionId
  const session = sessions.find((item) => item.id === sessionId)
  const sessionIsActive = Boolean(sessionId && activeSessionId === sessionId) || session?.timedState === 'running' || session?.timedState === 'paused'
  const sessionIsFinal = session?.sessionMode === 'manual' || session?.timedState === 'completed'
  const handoff = activityHandoffPath({ origin: 'today', returnTo: `${paths.today}?replaceSuggestion=${encodeURIComponent(suggestion.id)}`, reference: { kind: 'unlinked', title: 'Choose actual work' } })
  const rationale = `${paths.recommendations}?run=${encodeURIComponent(suggestion.recommendationRunId)}&candidate=${encodeURIComponent(suggestion.candidateId)}`

  return <article className={`surface p-5 ${suggestion.portfolioRole === 'primary' ? 'border-l-4 border-moss' : ''}`}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">{suggestion.portfolioRole === 'primary' ? 'Primary action' : label(suggestion.portfolioRole)}</p><h2 className="mt-1 font-display text-xl font-semibold">{suggestion.presentation.title}</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-ink/65">{suggestion.presentation.description}</p></div><StatusBadge label={label(suggestion.status)} tone={suggestion.terminal ? 'neutral' : suggestion.status === 'started' ? 'warning' : 'info'} /></div>
    <div className="mt-4 grid gap-3 sm:grid-cols-3"><div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Advisory duration</p><p className="mt-1 text-sm">{suggestion.advisoryDurationMs == null ? 'Unknown' : formatDuration(suggestion.advisoryDurationMs)}</p></div><div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Work type</p><p className="mt-1 text-sm capitalize">{label(suggestion.presentation.candidateType)}</p></div><div className="rounded-xl bg-ink/5 p-3"><p className="eyebrow">Expires</p><p className="mt-1 flex items-center gap-1 text-sm"><Clock3 className="size-3" />{new Date(suggestion.expiresAt).toLocaleString(undefined, { timeZone: suggestion.timezone })}</p></div></div>
    <details className="mt-4 rounded-xl border border-ink/10 p-3"><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-moss">Why this is suggested</summary><p className="mt-2 text-sm text-ink/70">{suggestion.presentation.reasonSummary}</p><div className="mt-3"><ReasonList reasons={suggestion.presentation.reasons} /></div><div className="mt-3 flex flex-wrap gap-3"><Link className="text-sm font-medium underline" to={rationale}>Open exact Recommendation rationale</Link>{sourceLink.href ? <Link className="text-sm font-medium underline" to={sourceLink.href}>{sourceLink.label}</Link> : <span className="text-sm text-ink/65">{sourceLink.label}</span>}</div></details>
    {!suggestion.terminal ? <div className="mt-4 flex flex-wrap gap-2">
      {['suggested', 'viewed'].includes(suggestion.status) ? <Button variant="secondary" disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'accepted')}><Check className="size-4" />Accept</Button> : null}
      {['suggested', 'viewed', 'accepted'].includes(suggestion.status) && suggestion.presentation.candidateType !== 'assessment' ? <Button disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'start', { assistance_mode: 'none', contributions: [] })}><Play className="size-4" />Start actual work</Button> : null}
      {suggestion.status === 'started' && sessionId && sessionIsActive ? <Link className="button-primary" to={paths.activity}>Open and continue Session</Link> : null}
      {suggestion.presentation.candidateType !== 'assessment' && suggestion.status === 'started' && sessionId && sessionIsFinal && session?.outcome === 'completed' ? <Button disabled={busy} onClick={() => void command(suggestion, 'completed', { session_id: sessionId })}>Complete from finalized Session</Button> : null}
      {suggestion.presentation.candidateType !== 'assessment' && suggestion.status === 'started' && sessionId && sessionIsFinal && ['completed', 'partial', 'blocked'].includes(session?.outcome ?? '') ? <Button variant="secondary" disabled={busy} onClick={() => void command(suggestion, 'partially-completed', { session_id: sessionId })}>Record partial outcome</Button> : null}
      {suggestion.status === 'started' && sessionId && !sessionIsActive && !sessionIsFinal ? <Link className="button-secondary" to={paths.activity}>Review Session state</Link> : null}
      {['suggested', 'viewed', 'accepted'].includes(suggestion.status) ? <Button variant="secondary" disabled={busy || suggestion.presentationExpired} onClick={() => void command(suggestion, 'skipped', { reason_code: 'user_skipped' })}><SkipForward className="size-4" />Skip without debt</Button> : null}
      <Link className="button-secondary" to={handoff}>Do something else</Link>
      {suggestion.interactions.length && !suggestion.interactions.at(-1)?.correction ? <Button variant="quiet" disabled={busy} onClick={() => void correct(suggestion)}>Correct latest status</Button> : null}
    </div> : null}
    {suggestion.presentation.candidateType === 'assessment' ? <AssessmentFlow suggestion={suggestion} command={command} /> : null}
    {suggestion.presentationExpired ? <p className="mt-3 text-sm text-ink/65">This presentation expired. Expiry creates no debt, completion, or Evidence.</p> : null}
    {suggestion.activityRelations.length ? <AuditDisclosure label="Active Activity relations"><ul className="space-y-2">{suggestion.activityRelations.map((relation) => <li key={relation.id}>{label(relation.type)} Activity · append-only corrections remain in Today audit history</li>)}</ul></AuditDisclosure> : null}
  </article>
}

const assessmentAssistance = ['none', 'docs_only', 'ai_hint', 'ai_assisted', 'agent_led']
function AssessmentFlow({ suggestion, command }: { suggestion: TodaySuggestion; command: (suggestion: TodaySuggestion, action: string, body?: object) => Promise<boolean> }) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<AssessmentTask[]>([])
  const [selected, setSelected] = useState('')
  const [assistance, setAssistance] = useState('')
  const [execution, setExecution] = useState<AssessmentExecution | null>(null)
  const [legacy, setLegacy] = useState<LegacyAssessment | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const startable = (['suggested', 'viewed', 'accepted'].includes(suggestion.status) && !suggestion.presentationExpired) || Boolean(legacy)
  const chosen = options.find((item) => `${item.unitDefinitionId}:${item.opportunityId}` === selected)

  useEffect(() => {
    if (!suggestion.interactions.some((item) => item.type === 'started')) return
    let cancelled = false
    void apiV2<AssessmentExecution | LegacyAssessment>(`/assessment-executions/by-suggestion/${suggestion.id}`).then((item) => {
      if (cancelled) return
      if ('kind' in item && item.kind === 'legacy_started') { setLegacy(item); setExecution(null) }
      else { setExecution(item as AssessmentExecution); setLegacy(null) }
      setError('')
    }).catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : 'Assessment execution could not be loaded.') })
    return () => { cancelled = true }
  }, [suggestion.id, suggestion.interactions])

  const loadOptions = async () => {
    if (open) { setOpen(false); return }
    setBusy(true); setError('')
    try {
      const response = await apiV2<{ items: AssessmentTask[] }>(`/today/suggestions/${suggestion.id}/assessment-options`)
      setOptions(response.items)
      setSelected(response.items[0] ? `${response.items[0].unitDefinitionId}:${response.items[0].opportunityId}` : '')
      setOpen(true)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Assessment tasks could not be loaded.') }
    finally { setBusy(false) }
  }
  const start = async () => {
    if (!chosen || !assistance) return
    setBusy(true)
    const action = legacy ? 'restart-legacy-assessment' : 'start'
    const succeeded = await command(suggestion, action, { assistance_mode: assistance, contributions: [], assessment_unit_definition_id: chosen.unitDefinitionId, assessment_opportunity_id: chosen.opportunityId })
    if (succeeded) { setOpen(false); setLegacy(null) }
    setBusy(false)
  }

  return <section className="mt-4 rounded-xl border border-moss/25 bg-moss/5 p-4" aria-label="Assessment execution">
    {error ? <MutationError>{error}</MutationError> : null}
    {legacy ? <div className="mb-4 rounded-xl bg-white p-4"><p className="font-semibold">Earlier assessment has no task binding</p><p className="mt-2 text-sm">{legacy.message}</p><p className="mt-2 text-sm">Session {legacy.sessionId} remains actual work history. Finish or cancel it before starting another timed Session. The new attempt creates a distinct Activity, Session, and assessment execution.</p></div> : null}
    {startable ? <><Button disabled={busy} onClick={() => void loadOptions()}>{open ? 'Close assessment task choices' : legacy ? 'Start new reviewed assessment attempt' : 'Choose assessment task and start'}</Button>
      {open ? <div className="mt-4 space-y-4"><p className="text-sm">Choose an eligible authored task. Each task can assess only its listed criteria.</p>{options.length ? <>
        <label className="block text-sm font-medium">Authored task<select className="mt-1 block w-full rounded-xl border border-ink/15 bg-white p-2" value={selected} onChange={(event) => setSelected(event.target.value)}>{options.map((item) => <option key={`${item.unitDefinitionId}:${item.opportunityId}`} value={`${item.unitDefinitionId}:${item.opportunityId}`}>{item.unitTitle}</option>)}</select></label>
        {chosen ? <div className="rounded-xl bg-white p-3"><p className="font-semibold">{chosen.unitTitle}</p><p className="mt-2 whitespace-pre-wrap text-sm">{chosen.action.instructions ?? chosen.action.verificationMethod}</p><p className="mt-2 text-xs">Covered: {chosen.criteria.map((item) => item.criterionStableKey).join(', ')} · {chosen.requiresArtifact ? 'Actual artifact required' : 'Captured task output required for Medium confidence'}</p></div> : null}
        <label className="block text-sm font-medium">Actual assistance<select className="mt-1 block w-full rounded-xl border border-ink/15 bg-white p-2" value={assistance} onChange={(event) => setAssistance(event.target.value)}><option value="">Select assistance used</option>{assessmentAssistance.map((mode) => <option key={mode} value={mode}>{label(mode)}</option>)}</select></label>
        <Button disabled={!chosen || !assistance || busy} onClick={() => void start()}><Play className="size-4" />Start actual assessment work</Button>
      </> : <p className="text-sm">No authored assessment task is eligible now. Starting is unavailable; no Activity or Session was created. {legacy ? 'Refresh Analysis and regenerate Today explicitly for a current assessment suggestion.' : ''}</p>}</div> : null}</> : null}
    {execution ? <div className="mt-4 space-y-3"><div className="rounded-xl bg-white p-3"><h3 className="font-semibold">Actual task: {execution.task.unitTitle}</h3><p className="mt-2 whitespace-pre-wrap text-sm">{execution.task.action.instructions ?? execution.task.action.verificationMethod}</p><p className="mt-2 text-xs">Session {label(execution.sessionState)} · assistance {label(execution.assistanceMode)} · {execution.task.requiresArtifact ? 'artifact required' : 'artifact optional for review'}</p></div>
      <p className="text-sm">{execution.reviewRequired ? 'Assessment awaits review. Session completion alone does not establish capability.' : 'Assessment reviewed. Earlier reviews remain in history.'}</p>
      <Link className="button-primary" to={paths.assessmentExecution(execution.id)}>{execution.reviewRequired ? 'Review assessment' : 'Open assessment result and corrections'}</Link>
      {suggestion.status === 'started' && !execution.reviewRequired && execution.sessionState === 'completed' && ['completed', 'partial'].includes(execution.sessionOutcome ?? '') ? <Button variant="secondary" disabled={busy} onClick={() => void command(suggestion, execution.sessionOutcome === 'completed' ? 'completed' : 'partially-completed', { session_id: execution.sessionId })}>Complete reviewed Today assessment</Button> : null}
    </div> : null}
  </section>
}
