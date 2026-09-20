import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TodayV2Page } from './features/today/TodayV2Page'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const suggestion = {
  id: 'suggestion-1', recommendationRunId: 'recommendation-run-1', recommendationId: 'recommendation-1', candidateId: 'candidate-1', portfolioRole: 'primary', advisoryDurationMs: 1_800_000, durationRangeMs: [900_000, 1_800_000, 2_700_000],
  presentation: { title: 'Practice JavaScript functions', description: 'Complete the authored exercise.', candidateType: 'curriculum_unit', reasonSummary: 'Addresses the current capability gap.', reasons: [{ code: 'CAPABILITY_GAP', title: 'Capability gap', text: 'A required capability gap is open.' }], source: { type: 'curriculum_unit', entityId: 'unit-1', versionId: 'curriculum-version-1' } },
  timezone: 'UTC', expiresAt: '2026-09-20T00:00:00Z', presentationExpired: false, status: 'suggested', terminal: false, interactions: [], activityRelations: [],
}
const generation = { id: 'generation-1', localDate: '2026-09-19', timezone: 'UTC', generationSequence: 1, generatedAt: '2026-09-19T08:00:00Z', regeneration: false, suggestions: [suggestion] }

function reflectionResponse(url: string) {
  if (url.endsWith('/api/v1/sessions?limit=100')) return json({ items: [] })
  if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [{ curriculumId: 'curriculum-1', unitDefinitionId: 'unit-1', title: 'Practice JavaScript functions' }] })
  if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
  if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
  if (url.endsWith('/api/v1/reflections/2026-09-19')) return json({ reflection: null })
  return null
}

describe('Today daily control loop', () => {
  beforeEach(() => cleanup())

  it.each([0, 1, 2, 3])('renders a truthful %i-item advisory portfolio without a read mutation', async (count) => {
    const suggestions = Array.from({ length: count }, (_, index) => ({ ...suggestion, id: `suggestion-${index}`, candidateId: `candidate-${index}`, portfolioRole: index === 0 ? 'primary' : index === 1 ? 'complementary' : 'maintenance', presentation: { ...suggestion.presentation, title: `Work option ${index + 1}` } }))
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation: { ...generation, suggestions }, continuingStartedSuggestions: [] })
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByRole('heading', { name: 'Today' })).toBeInTheDocument()
    expect(screen.getByText(`${count} item${count === 1 ? '' : 's'}`)).toBeInTheDocument()
    if (count === 0) expect(screen.getByText('No useful eligible work')).toBeInTheDocument()
    else expect(screen.getByRole('heading', { name: 'Work option 1' })).toBeInTheDocument()
    if (count > 0) expect(screen.getAllByRole('link', { name: 'Open exact Recommendation rationale' })[0]).toHaveAttribute('href', '/insights/recommendations?run=recommendation-run-1&candidate=candidate-0')
    if (count > 0) expect(screen.getAllByRole('link', { name: 'Open learning unit: Practice JavaScript functions' })[0]).toHaveAttribute('href', '/learn/curricula/curriculum-1')
    const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>
    expect(calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('keeps continuing started work actionable and expiry debt-free', async () => {
    const continuing = { ...suggestion, id: 'continuing-1', status: 'started', interactions: [{ id: 'started-interaction', type: 'started', sessionId: 'session-1', correction: null }] }
    const expired = { ...suggestion, presentationExpired: true }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation: { ...generation, suggestions: [expired] }, continuingStartedSuggestions: [continuing] })
      if (url.endsWith('/api/v1/sessions?limit=100')) return json({ items: [{ id: 'session-1', sessionMode: 'timed', timedState: 'completed', outcome: 'completed' }] })
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByRole('heading', { name: 'Continue actual work' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Complete from finalized Session' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record partial outcome' })).toBeInTheDocument()
    expect(screen.getByText(/Expiry creates no debt, completion, or Evidence/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Do something else' })[0]).toHaveAttribute('href', expect.stringContaining('kind=unlinked'))
  })

  it.each(['running', 'paused'])('routes a %s started Session to authoritative timer controls before Today completion', async (timedState) => {
    const continuing = { ...suggestion, status: 'started', interactions: [{ id: 'started-interaction', type: 'started', sessionId: 'session-1', correction: null }] }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation: { ...generation, suggestions: [] }, continuingStartedSuggestions: [continuing] })
      if (url.endsWith('/api/v1/sessions?limit=100')) return json({ items: [{ id: 'session-1', sessionMode: 'timed', timedState, outcome: null }] })
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByRole('link', { name: 'Open and continue Session' })).toHaveAttribute('href', '/activity')
    expect(screen.queryByRole('button', { name: /Complete from/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record partial outcome' })).not.toBeInTheDocument()
  })

  it.each(['completed', 'partially_completed', 'skipped', 'replaced', 'expired'])('renders terminal %s history without offering new work commands', async (status) => {
    const terminal = { ...suggestion, status, terminal: true, presentationExpired: status === 'expired' }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation: { ...generation, suggestions: [terminal] }, continuingStartedSuggestions: [] })
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByText(status.replaceAll('_', ' '))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start actual work' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Skip without debt' })).not.toBeInTheDocument()
  })

  it('requires explicit confirmation after an Activity handoff before recording replacement', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation, continuingStartedSuggestions: [] })
      if (url.endsWith('/api/v2/today/suggestions/suggestion-1/replace') && init?.method === 'POST') return json({ ...suggestion, status: 'replaced', terminal: true })
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter initialEntries={['/?replaceSuggestion=suggestion-1&activityResult=activity-1&activityResultStatus=selected']}><TodayV2Page /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Confirm replacement for Practice JavaScript functions' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith('/replace') && init?.method === 'POST')).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v2/today/suggestions/suggestion-1/replace', expect.objectContaining({ method: 'POST' })))
  })

  it('preserves replacement confirmation after failure and supports an explicit retry', async () => {
    let attempts = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation, continuingStartedSuggestions: [] })
      if (url.endsWith('/api/v2/today/suggestions/suggestion-1/replace') && init?.method === 'POST') {
        attempts += 1
        return attempts === 1
          ? json({ error: { code: 'CONFLICT', message: 'The Activity changed. Review and try again.' } }, 409)
          : json({ ...suggestion, status: 'replaced', terminal: true })
      }
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter initialEntries={['/?replaceSuggestion=suggestion-1&activityResult=activity-1&activityResultStatus=selected']}><TodayV2Page /></MemoryRouter>)

    const confirm = await screen.findByRole('button', { name: 'Confirm replacement' })
    await userEvent.click(confirm)
    expect(await screen.findByText('The Activity changed. Review and try again.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Confirm replacement for Practice JavaScript functions' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }))
    await waitFor(() => expect(attempts).toBe(2))
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Confirm replacement for Practice JavaScript functions' })).not.toBeInTheDocument())
  })

  it('records viewed only after its explicit optional command', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation, continuingStartedSuggestions: [] })
      if (url.endsWith('/api/v2/today/suggestions/suggestion-1/viewed') && init?.method === 'POST') return json({ ...suggestion, status: 'viewed' }, 201)
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<TodayV2Page />, { wrapper: MemoryRouter })
    const button = await screen.findByRole('button', { name: 'Mark viewed' })
    expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith('/viewed') && init?.method === 'POST')).toBe(false)
    await userEvent.click(button)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v2/today/suggestions/suggestion-1/viewed', expect.objectContaining({ method: 'POST' })))
  })

  it('generates explicitly and keeps ordinary refresh read-only', async () => {
    let currentCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) { currentCalls += 1; return json({ generation: currentCalls > 1 ? generation : null, continuingStartedSuggestions: [] }) }
      if (url.endsWith('/api/v2/analysis/current')) return json({ configured: true, status: 'current', snapshot: { id: 'analysis-1' } })
      if (url.endsWith('/api/v2/today/generations') && init?.method === 'POST') return json(generation, 201)
      if (url.endsWith('/api/v1/settings/discipline')) return json({ timezone: 'UTC' })
      if (url.includes('/api/v1/reflections/')) return json({ reflection: null })
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<TodayV2Page />, { wrapper: MemoryRouter })
    expect(await screen.findByText('Today has not been generated')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Generate Today' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v2/today/generations', expect.objectContaining({ method: 'POST' })))
    expect(currentCalls).toBeGreaterThan(1)
  })

  it('keeps an authoritative generated Today portfolio when its follow-up refresh fails', async () => {
    let currentCalls = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) {
        currentCalls += 1
        return currentCalls === 1
          ? json({ generation: null, continuingStartedSuggestions: [] })
          : json({ error: { code: 'UNAVAILABLE', message: 'Refresh failed after generation.' } }, 503)
      }
      if (url.endsWith('/api/v2/analysis/current')) return json({ configured: true, status: 'current', snapshot: { id: 'analysis-1' } })
      if (url.endsWith('/api/v2/today/generations') && init?.method === 'POST') return json(generation, 201)
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    await userEvent.click(await screen.findByRole('button', { name: 'Generate Today' }))
    expect(await screen.findByRole('heading', { name: 'Practice JavaScript functions' })).toBeInTheDocument()
    expect(screen.getByText('Refresh failed after generation.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate explicitly' })).toBeEnabled()
  })

  it('keeps an authoritative Today status transition when its follow-up refresh fails', async () => {
    let currentCalls = 0
    const skipped = { ...suggestion, status: 'skipped', terminal: true, interactions: [{ id: 'skip-1', type: 'skipped', sessionId: null, correction: null }] }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) {
        currentCalls += 1
        return currentCalls === 1
          ? json({ generation, continuingStartedSuggestions: [] })
          : json({ error: { code: 'UNAVAILABLE', message: 'Refresh failed after status update.' } }, 503)
      }
      if (url.endsWith('/api/v2/today/suggestions/suggestion-1/skipped') && init?.method === 'POST') return json(skipped, 201)
      const reflection = reflectionResponse(url); if (reflection) return reflection
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    await userEvent.click(await screen.findByRole('button', { name: 'Skip without debt' }))
    expect(await screen.findByText('Refresh failed after status update.')).toBeInTheDocument()
    expect(screen.getByText('skipped')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Skip without debt' })).not.toBeInTheDocument()
  })

  it('keeps exact relevant-subject navigation for assessment and project-blocker candidates', async () => {
    const assessment = { ...suggestion, id: 'assessment-suggestion', presentation: { ...suggestion.presentation, title: 'Assess capability', candidateType: 'assessment', source: { type: 'assessment_rubric', entityId: 'rubric-1', versionId: 'curriculum-version-1' }, competencyIdentityId: 'competency-1', targetIdentityId: 'target-1', servedTargetIdentityIds: ['target-1'] } }
    const blocker = { ...suggestion, id: 'blocker-suggestion', candidateId: 'candidate-2', portfolioRole: 'complementary', presentation: { ...suggestion.presentation, title: 'Unblock project work', candidateType: 'unblock_task', source: { type: 'project_blocker', entityId: 'blocker-event-1', versionId: 'project-version-1' }, competencyIdentityId: 'competency-1', targetIdentityId: 'target-1', servedTargetIdentityIds: ['target-1'] } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation: { ...generation, suggestions: [assessment, blocker] }, continuingStartedSuggestions: [] })
      if (url.endsWith('/api/v1/sessions?limit=100')) return json({ items: [] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [{ curriculumId: 'curriculum-1', curriculumVersionId: 'curriculum-version-1', unitDefinitionId: 'unit-1', title: 'Assessment source' }] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [{ project_id: 'project-1', project_version_id: 'project-version-1', task_definition_id: 'task-1', title: 'Blocked project task' }] })
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [{ id: 'competency-1', title: 'JavaScript functions', profileTargets: [{ id: 'target-definition-1', identityId: 'target-1' }] }], edges: [] })
      if (url.endsWith('/api/v1/reflections/2026-09-19')) return json({ reflection: null })
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByRole('link', { name: 'Open source Curriculum' })).toHaveAttribute('href', '/learn/curricula/curriculum-1')
    expect(screen.getByRole('link', { name: 'Open source Project: Blocked project task' })).toHaveAttribute('href', '/projects/project-1')
  })

  it('preserves empty-state generation controls and input after a command failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/today/current')) return json({ generation: null, continuingStartedSuggestions: [] })
      if (url.endsWith('/api/v2/analysis/current')) return json({ configured: true, status: 'current', snapshot: { id: 'analysis-1' } })
      if (url.endsWith('/api/v2/today/generations') && init?.method === 'POST') return json({ error: { code: 'UNAVAILABLE', message: 'Generation failed safely.' } }, 503)
      if (url.endsWith('/api/v1/settings/discipline')) return json({ timezone: 'UTC' })
      if (url.includes('/api/v1/reflections/')) return json({ reflection: null })
      throw new Error(`Unexpected URL: ${url}`)
    }))
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByText('Today has not been generated')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Available minutes (optional)'), '45')
    await userEvent.click(screen.getByRole('button', { name: 'Generate Today' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Generation failed safely.')
    expect(screen.getByLabelText('Available minutes (optional)')).toHaveValue(45)
    expect(screen.getByRole('button', { name: 'Generate Today' })).toBeEnabled()
  })
})
