import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { apiV2 } from '../../api'
import { Button, ErrorState, LoadingState, MutationError, PageHeader, Surface } from '../../shared/components'
import { paths } from '../../shared/navigation/paths'

type Criterion = { criterionDefinitionId: string; criterionStableKey: string; description: string; rubricCheck: string }
type Row = { criterionDefinitionId: string; state: string; taskSetup?: string | null; expectedResult?: string | null; observedOutput?: string | null; comparison?: string | null; artifactContent?: string | null; artifactReference?: string | null; corroborationId?: string | null; additionalAssistanceMode?: string | null; evidenceId?: string | null; derivedStrength?: string; derivedSourceConfidence?: string; evidenceActive?: boolean }
type Result = { snapshot: { rows: Row[] }; capability: { levelKey: string | null; confidence: string } | null; criterionStates: { criterionStableKey: string; state: string }[]; analysisStatus: string }
type Artifact = { id: string; criterionDefinitionId: string; filename: string; sizeBytes: number; sha256: string }
type Execution = { id: string; suggestionId: string; activityId: string; sessionId: string; sessionState: string; sessionOutcome: string | null; assistanceMode: string; todayStatus: string | null; reviewRequired: boolean; task: { unitTitle: string; action: { instructions?: string; verificationMethod?: string }; requiresArtifact: boolean; criteria: Criterion[] }; artifacts: Artifact[]; reviews: { id: string; snapshot: { rows: Row[] } }[]; latestResult: Result | null }
const assistanceModes = ['none', 'docs_only', 'ai_hint', 'ai_assisted', 'agent_led']
const emptyRow = (criterionDefinitionId: string): Row => ({ criterionDefinitionId, state: 'unobserved' })
const label = (value: string) => value.replaceAll('_', ' ')

export function AssessmentExecutionPage() {
  const { executionId } = useParams()
  const [execution, setExecution] = useState<Execution | null>(null)
  const [rows, setRows] = useState<Record<string, Row>>({})
  const [checks, setChecks] = useState({ actual_session: false, assistance_complete: false, outputs_authentic: false, review_truthful: false })
  const [correction, setCorrection] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!executionId) return
    setLoading(true)
    try {
      const item = await apiV2<Execution>(`/assessment-executions/${executionId}`)
      setExecution(item)
      setRows((current) => Object.keys(current).length ? current : Object.fromEntries(item.task.criteria.map((criterion) => [criterion.criterionDefinitionId, emptyRow(criterion.criterionDefinitionId)])))
      setError('')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Assessment execution could not be loaded.') }
    finally { setLoading(false) }
  }, [executionId])
  useEffect(() => { void load() }, [load])

  const updateRow = (criterionId: string, field: keyof Row, value: string) => setRows((current) => ({ ...current, [criterionId]: { ...(current[criterionId] ?? emptyRow(criterionId)), [field]: value } }))
  const upload = async (criterionId: string, file: File) => {
    if (!execution || busy) return
    setBusy(true); setError('')
    try {
      const query = new URLSearchParams({ criterion_definition_id: criterionId, filename: file.name })
      const artifact = await apiV2<Artifact>(`/assessment-executions/${execution.id}/artifacts?${query}`, {
        method: 'POST', body: file,
        headers: { 'Content-Type': 'application/octet-stream', 'Idempotency-Key': crypto.randomUUID() },
      })
      updateRow(criterionId, 'corroborationId', artifact.id)
      await load()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Artifact could not be stored.') }
    finally { setBusy(false) }
  }
  const beginCorrection = () => {
    if (!execution?.reviews.length) return
    setRows(Object.fromEntries(execution.reviews.at(-1)!.snapshot.rows.map((row) => [row.criterionDefinitionId, { ...row }])))
    setCorrection(true)
    setChecks({ actual_session: false, assistance_complete: false, outputs_authentic: false, review_truthful: false })
  }
  const submit = async () => {
    if (!execution || busy) return
    setBusy(true); setError('')
    try {
      await apiV2(`/assessment-executions/${execution.id}/reviews`, { method: 'POST', body: JSON.stringify({
        idempotency_key: crypto.randomUUID(),
        supersedes_review_id: execution.reviews.length ? execution.reviews.at(-1)?.id : null,
        correction_reason: execution.reviews.length ? reason : null,
        observations: execution.task.criteria.map((criterion) => {
          const row = rows[criterion.criterionDefinitionId] ?? emptyRow(criterion.criterionDefinitionId)
          return { criterion_definition_id: row.criterionDefinitionId, state: row.state,
            task_setup: row.state === 'unobserved' ? null : row.taskSetup || null,
            expected_result: row.state === 'unobserved' ? null : row.expectedResult || null,
            observed_output: row.state === 'unobserved' ? null : row.observedOutput || null,
            comparison: row.state === 'unobserved' ? null : row.comparison || null,
            artifact_content: row.state === 'unobserved' ? null : row.artifactContent || null,
            artifact_reference: row.state === 'unobserved' ? null : row.artifactReference || null,
            corroboration: row.state === 'unobserved' || !row.corroborationId ? null : { kind: 'server_stored_artifact', artifact_id: row.corroborationId },
            additional_assistance_mode: row.state === 'unobserved' ? null : row.additionalAssistanceMode || null,
          }
        }), attestation: checks,
      }) })
      setCorrection(false); setReason('')
      await load()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Assessment review could not be recorded.') }
    finally { setBusy(false) }
  }
  const closeToday = async () => {
    if (!execution || busy) return
    setBusy(true); setError('')
    try {
      const action = execution.sessionOutcome === 'completed' ? 'completed' : 'partially-completed'
      await apiV2(`/today/suggestions/${execution.suggestionId}/${action}`, { method: 'POST', body: JSON.stringify({ idempotency_key: crypto.randomUUID(), session_id: execution.sessionId }) })
      await load()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Today assessment could not be closed.') }
    finally { setBusy(false) }
  }

  if (loading && !execution) return <LoadingState label="Loading assessment execution" />
  if (!execution) return <ErrorState message={error || 'Assessment execution unavailable.'} retry={() => void load()} />
  const reviewable = execution.sessionState === 'completed' && ['completed', 'partial'].includes(execution.sessionOutcome ?? '')
  const showForm = reviewable && (execution.reviewRequired || correction)
  return <div className="mx-auto max-w-4xl space-y-6">
    <PageHeader eyebrow="Actual assessment" title={execution.task.unitTitle} description="This route is tied to the actual Activity, Session, and authored assessment execution. Review remains available across Today generations and local days." />
    {error ? <MutationError>{error}</MutationError> : null}
    <Surface className="space-y-3 p-5"><p className="text-sm">Session {label(execution.sessionState)} · outcome {label(execution.sessionOutcome ?? 'unknown')} · assistance {label(execution.assistanceMode)}</p><p className="whitespace-pre-wrap text-sm">{execution.task.action.instructions ?? execution.task.action.verificationMethod}</p><p className="text-xs">{execution.task.requiresArtifact ? 'A stored task artifact is required for qualifying review Evidence.' : 'An artifact is optional for this task, but Medium source confidence requires a task artifact uploaded to LCC. Narrative or external references alone remain Low.'}</p><Link className="button-secondary" to={paths.activity}>Open Activity and Session</Link></Surface>
    <Surface className="space-y-4 p-5"><h2 className="font-semibold">Criteria this task can observe</h2>{execution.task.criteria.map((criterion) => <div className="rounded-xl border border-ink/10 p-4" key={criterion.criterionDefinitionId}><p className="font-semibold">{criterion.criterionStableKey}</p><p className="text-sm">{criterion.description}</p><p className="text-sm text-ink/65">Review check: {criterion.rubricCheck}</p></div>)}</Surface>
    {!reviewable && !execution.reviews.length ? <Surface className="p-5 text-sm">Finish the actual Session with a completed or partial outcome before review. A blocked or cancelled Session cannot produce qualifying assessment Evidence.</Surface> : null}
    {reviewable && execution.reviews.length && !showForm ? <Surface className="space-y-3 p-5"><p>Assessment reviewed. Earlier reviews remain in history.</p><Button variant="secondary" onClick={beginCorrection}>Correct latest review</Button><p className="text-xs">Correct Session assistance in Activity first; affected Evidence is retracted and a new review is required.</p></Surface> : null}
    {showForm ? <Surface className="space-y-4 p-5"><h2 className="font-semibold">{execution.reviews.length ? 'Correct assessment review' : 'Review actual assessment'}</h2><p className="text-sm">Record only what this Session actually showed. Narrative alone remains Low confidence.</p>
      {execution.task.criteria.map((criterion) => { const row = rows[criterion.criterionDefinitionId] ?? emptyRow(criterion.criterionDefinitionId); const available = execution.artifacts.filter((item) => item.criterionDefinitionId === criterion.criterionDefinitionId); return <div className="space-y-3 rounded-xl border border-ink/10 p-4" key={criterion.criterionDefinitionId}><p className="font-semibold">{criterion.criterionStableKey}</p><label className="block text-sm">Observed state<select className="mt-1 block w-full rounded-xl border p-2" value={row.state} onChange={(event) => updateRow(criterion.criterionDefinitionId, 'state', event.target.value)}>{['unobserved', 'demonstrated', 'partial', 'contradicted'].map((state) => <option key={state} value={state}>{state}</option>)}</select></label>{row.state !== 'unobserved' ? <div className="grid gap-3">{([['taskSetup', 'Task setup and inputs'], ['expectedResult', 'Expected result'], ['observedOutput', 'Actual observed output'], ['comparison', 'Criterion comparison and judgment'], ['artifactContent', 'Optional excerpt or review notes (narrative only)']] as const).map(([field, text]) => <label className="text-sm" key={field}>{text}<textarea className="mt-1 block min-h-20 w-full rounded-xl border p-2" value={row[field] ?? ''} onChange={(event) => updateRow(criterion.criterionDefinitionId, field, event.target.value)} /></label>)}<label className="text-sm">External artifact reference (does not raise confidence)<input className="mt-1 block w-full rounded-xl border p-2" value={row.artifactReference ?? ''} onChange={(event) => updateRow(criterion.criterionDefinitionId, 'artifactReference', event.target.value)} /></label><label className="text-sm">Upload actual task artifact or preserved output file to LCC (256 KiB maximum)<input type="file" className="mt-1 block w-full" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(criterion.criterionDefinitionId, file) }} /></label><label className="text-sm">Stored corroborating artifact<select className="mt-1 block w-full rounded-xl border p-2" value={row.corroborationId ?? ''} onChange={(event) => updateRow(criterion.criterionDefinitionId, 'corroborationId', event.target.value)}><option value="">None — source confidence remains Low</option>{available.map((item) => <option key={item.id} value={item.id}>{item.filename} · {item.sizeBytes} bytes · SHA-256 {item.sha256.slice(0, 12)}…</option>)}</select></label><p className="text-xs">{row.corroborationId ? 'Server-stored artifact selected. Medium still depends on complete review, attestation, and assistance provenance.' : 'No server-stored artifact selected. This observation will remain Low confidence.'}</p><label className="text-sm">Additional assistance disclosed<select className="mt-1 block w-full rounded-xl border p-2" value={row.additionalAssistanceMode ?? ''} onChange={(event) => updateRow(criterion.criterionDefinitionId, 'additionalAssistanceMode', event.target.value)}><option value="">None beyond Session record</option>{assistanceModes.map((mode) => <option key={mode} value={mode}>{label(mode)}</option>)}</select></label></div> : null}</div> })}
      {execution.reviews.length ? <label className="block text-sm">Correction reason<input className="mt-1 block w-full rounded-xl border p-2" value={reason} onChange={(event) => setReason(event.target.value)} /></label> : null}
      <fieldset className="space-y-2 rounded-xl border p-4"><legend className="font-semibold">First-party review attestation</legend>{([['actual_session', 'These observations correspond to the actual Session.'], ['assistance_complete', 'Assistance disclosure is complete to the best of my knowledge.'], ['outputs_authentic', 'Outputs and artifacts are not fabricated.'], ['review_truthful', 'Criterion decisions reflect what I actually observed.']] as const).map(([key, text]) => <label className="flex gap-2 text-sm" key={key}><input type="checkbox" checked={checks[key]} onChange={(event) => setChecks((current) => ({ ...current, [key]: event.target.checked }))} />{text}</label>)}</fieldset>
      <Button disabled={busy || Object.values(checks).some((value) => !value) || (execution.reviews.length > 0 && !reason.trim())} onClick={() => void submit()}>{busy ? 'Recording review…' : 'Confirm and record reviewed Evidence'}</Button>
    </Surface> : null}
    {execution.latestResult ? <Surface className="space-y-3 p-5"><h2 className="font-semibold">Assessment result</h2><p className="text-sm">Capability {execution.latestResult.capability?.levelKey ?? 'Unknown'} · confidence {execution.latestResult.capability?.confidence ?? 'unknown'}</p><ul className="space-y-1 text-sm">{execution.latestResult.snapshot.rows.map((row) => <li key={row.criterionDefinitionId}>{execution.task.criteria.find((item) => item.criterionDefinitionId === row.criterionDefinitionId)?.criterionStableKey}: {label(row.state)}{row.derivedStrength ? ` · ${row.derivedStrength} strength · ${row.derivedSourceConfidence} source confidence` : ' · no Evidence'}{row.evidenceActive === false ? ' · Evidence retracted' : ''}{row.derivedSourceConfidence === 'low' ? ' · Does not qualify: a server-stored task artifact and complete provenance are required for Medium.' : null}{row.derivedStrength === 'weak' && row.derivedSourceConfidence === 'medium' ? ' · Does not qualify as Moderate support: check the observed state, assistance, and authored opportunity.' : null}</li>)}</ul><p className="text-sm">Current criteria: {execution.latestResult.criterionStates.map((item) => `${item.criterionStableKey} ${label(item.state)}`).join('; ')}</p><p className="text-sm">Unobserved and unmet criteria remain open. Analysis is {label(execution.latestResult.analysisStatus)} and requires explicit refresh.</p><Link className="button-secondary" to={paths.analysis}>Open Analysis</Link></Surface> : null}
    {reviewable && !execution.reviewRequired && execution.todayStatus === 'started' ? <Button disabled={busy} onClick={() => void closeToday()}>{busy ? 'Closing…' : 'Complete reviewed Today assessment'}</Button> : null}
  </div>
}
