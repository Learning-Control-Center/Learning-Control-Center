import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'

import { AssessmentExecutionPage } from './AssessmentExecutionPage'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

it('reviews a finalized execution by ID without Today or recent Session state', async () => {
  const base = {
    id: 'execution-1', suggestionId: 'old-suggestion', activityId: 'activity-1', sessionId: 'old-session',
    sessionState: 'completed', sessionOutcome: 'completed', assistanceMode: 'docs_only', todayStatus: 'started', reviewRequired: true,
    task: { unitTitle: 'Trace a pipeline', action: { instructions: 'Use disposable files and compare pipeline status.' }, requiresArtifact: true,
      criteria: [{ criterionDefinitionId: 'criterion-1', criterionStableKey: 'trace-pipeline-status', description: 'Trace the pipeline.', rubricCheck: 'Compare output and status.' }] },
    artifacts: [], reviews: [], latestResult: null,
  }
  const result = { snapshot: { rows: [{ criterionDefinitionId: 'criterion-1', state: 'demonstrated', derivedStrength: 'moderate', derivedSourceConfidence: 'medium', evidenceId: 'evidence-1' }] }, capability: { levelKey: null, confidence: 'medium' }, criterionStates: [{ criterionStableKey: 'trace-pipeline-status', state: 'unknown' }], analysisStatus: 'stale' }
  let reviewed = false
  let uploaded = false
  const artifact = { id: 'artifact-1', criterionDefinitionId: 'criterion-1', filename: 'task-output.txt', sizeBytes: 46, sha256: 'a'.repeat(64), captureMethod: 'server_received_bytes/v1' }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/v2/assessment-executions/execution-1/artifacts?') && init?.method === 'POST') { uploaded = true; return json(artifact, 201) }
    if (url.endsWith('/api/v2/assessment-executions/execution-1/reviews') && init?.method === 'POST') { reviewed = true; return json({ id: 'review-1', ...result }, 201) }
    if (url.endsWith('/api/v2/assessment-executions/execution-1')) return json(reviewed ? { ...base, artifacts: [artifact], reviewRequired: false, reviews: [{ id: 'review-1', snapshot: result.snapshot }], latestResult: result } : { ...base, artifacts: uploaded ? [artifact] : [] })
    throw new Error(`Unexpected URL: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<MemoryRouter initialEntries={['/assessments/execution-1']}><Routes><Route path="/assessments/:executionId" element={<AssessmentExecutionPage />} /></Routes></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: 'Trace a pipeline' })).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText('Observed state'), 'demonstrated')
  for (const name of ['Task setup and inputs', 'Expected result', 'Actual observed output', 'Criterion comparison and judgment', 'Optional excerpt or review notes (narrative only)']) {
    fireEvent.change(screen.getByRole('textbox', { name }), { target: { value: `Actual task output and comparison for ${name} in the disposable fixture.` } })
  }
  expect(screen.getByText(/No server-stored artifact selected/)).toBeInTheDocument()
  await userEvent.upload(screen.getByLabelText(/Upload actual task artifact/), new File(['Actual task output: pipeline exit statuses 0 and 1.'], 'task-output.txt', { type: 'text/plain' }))
  await waitFor(() => expect(screen.getByText(/Server-stored artifact selected/)).toBeInTheDocument())
  for (const checkbox of screen.getAllByRole('checkbox')) await userEvent.click(checkbox)
  await userEvent.click(screen.getByRole('button', { name: 'Confirm and record reviewed Evidence' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith('/reviews') && init?.method === 'POST')).toBe(true))
  const reviewCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith('/reviews') && init?.method === 'POST')
  const body = JSON.parse(String(reviewCall?.[1]?.body))
  expect(body.observations).toHaveLength(1)
  expect(body.observations[0].criterion_definition_id).toBe('criterion-1')
  expect(body.observations[0].corroboration).toEqual({ kind: 'server_stored_artifact', artifact_id: 'artifact-1' })
  expect(body).not.toHaveProperty('source_confidence')
  expect(fetchMock.mock.calls.some(([input, init]) => String(input).includes('/artifacts?') && init?.headers instanceof Headers && init.headers.get('Content-Type') === 'application/octet-stream')).toBe(true)
  expect(await screen.findByText(/moderate strength · medium source confidence/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.every(([input]) => !String(input).includes('/today/current') && !String(input).includes('/sessions?'))).toBe(true)
})
