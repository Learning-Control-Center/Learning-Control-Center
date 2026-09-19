import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SessionsPage } from './pages/SessionsPage'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

describe('V2 Activity handoff', () => {
  it('does not mutate on refresh and creates then relates only after confirmation', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'handoff-key' })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/activities') && init?.method === 'POST') return json({ id: 'activity-1', title: 'Build service', description: null, categoryStableKey: 'project', occurredAt: null, createdAt: '2026-01-01T00:00:00Z' }, 201)
      if (url.endsWith('/api/v2/activities')) return json([])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.endsWith('/api/v1/sessions/active')) return json({ active: false, session: null })
      if (url.includes('/api/v1/sessions?')) return json({ items: [] })
      if (url.endsWith('/api/v2/projects/activity-links') && init?.method === 'POST') return json({ id: 'link-1' }, 201)
      return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter initialEntries={['/activity?origin=projects&returnTo=%2Fprojects%2Fproject-1&kind=project_task&title=Build+service&projectId=project-1&projectVersionId=version-1&taskDefinitionId=task-1']}><SessionsPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Continue: Build service' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input, init]) => String(input).includes('/roadmap/current') || init?.method === 'POST')).toBe(false)
    expect(screen.queryByLabelText(/project id/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Create and relate Activity' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v2/activities', expect.objectContaining({ method: 'POST' })))
    expect(fetchMock).toHaveBeenCalledWith('/api/v2/projects/activity-links', expect.objectContaining({ method: 'POST' }))
    expect(await screen.findByText(/Relationship recorded/)).toBeInTheDocument()
  })

  it('keeps related Session controls disabled until confirmation and recovers a failed relation without duplicate Activity creation', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'retry-key' })
    let relationAttempts = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/activities') && init?.method === 'POST') return json({ id: 'activity-1', title: 'Practice unit', description: null, categoryStableKey: 'practice', occurredAt: null, createdAt: '2026-01-01T00:00:00Z' }, 201)
      if (url.endsWith('/api/v2/activities')) return json([])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.endsWith('/api/v1/sessions/active')) return json({ active: false, session: null })
      if (url.includes('/api/v1/sessions?')) return json({ items: [] })
      if (url.endsWith('/api/v2/curricula/activity-links') && init?.method === 'POST') {
        relationAttempts += 1
        return relationAttempts === 1 ? json({ error: { code: 'CONFLICT', message: 'Already changed' } }, 409) : json({ id: 'link-1' }, 201)
      }
      return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter initialEntries={['/activity?origin=learn&returnTo=%2Flearn%2Fcurricula%2Fcurr-1&kind=curriculum_unit&title=Practice+unit&curriculumId=curr-1&unitDefinitionId=unit-1']}><SessionsPage /></MemoryRouter>)

    expect(await screen.findByRole('button', { name: 'Start timer' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Create and relate Activity' }))
    expect(await screen.findByText(/Activity remains created, but the relationship failed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start timer' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm relationship' }))
    expect(await screen.findByText(/Relationship recorded/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start timer' })).toBeEnabled()
    expect(fetchMock.mock.calls.filter(([input, init]) => String(input).endsWith('/api/v2/activities') && init?.method === 'POST')).toHaveLength(1)
    expect(fetchMock.mock.calls.filter(([input, init]) => String(input).endsWith('/api/v2/curricula/activity-links') && init?.method === 'POST')).toHaveLength(2)
  })

  it('describes competency handoff as a Session contribution instead of a persisted Activity relation', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init
      const url = String(input)
      if (url.endsWith('/api/v2/activities')) return json([{ id: 'activity-1', title: 'Practice', description: null, categoryStableKey: 'practice', occurredAt: null, createdAt: '2026-01-01T00:00:00Z' }])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.endsWith('/api/v1/sessions/active')) return json({ active: false, session: null })
      if (url.includes('/api/v1/sessions?')) return json({ items: [] })
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter initialEntries={['/activity?origin=profile&returnTo=%2Fprofile%2Fcompetencies%2Fcompetency-1&kind=competency&title=Python&competencyIdentityId=competency-1']}><SessionsPage /></MemoryRouter>)

    await userEvent.click(await screen.findByRole('button', { name: 'Use Activity' }))
    expect(await screen.findByText(/competency contribution is recorded only with a Session/)).toBeInTheDocument()
    expect(screen.queryByText(/Relationship recorded/)).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('requires fresh confirmation and resets create defaults when the handoff query changes', async () => {
    const secondHandoff = '/activity?origin=learn&returnTo=%2Flearn%2Fcurricula%2Fcurr-2&kind=curriculum_unit&title=Second+learning+unit&curriculumId=curr-2&unitDefinitionId=unit-2'
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/activities')) return json([{ id: 'activity-1', title: 'Existing practice', description: null, categoryStableKey: 'practice', occurredAt: null, createdAt: '2026-01-01T00:00:00Z' }])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.endsWith('/api/v1/sessions/active')) return json({ active: false, session: null })
      if (url.includes('/api/v1/sessions?')) return json({ items: [] })
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    function QuerySwitchHarness() {
      const navigate = useNavigate()
      return <><button onClick={() => void navigate(secondHandoff)}>Switch handoff</button><SessionsPage /></>
    }

    render(<MemoryRouter initialEntries={['/activity?origin=profile&returnTo=%2Fprofile%2Fcompetencies%2Fcompetency-1&kind=competency&title=First+competency&competencyIdentityId=competency-1']}><QuerySwitchHarness /></MemoryRouter>)

    await userEvent.click(await screen.findByRole('button', { name: 'Use Activity' }))
    expect(screen.getByRole('button', { name: 'Start timer' })).toBeEnabled()
    expect(screen.getByLabelText('Activity title')).toHaveValue('First competency')

    await userEvent.click(screen.getByRole('button', { name: 'Switch handoff' }))
    expect(await screen.findByRole('heading', { name: 'Continue: Second learning unit' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start timer' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Confirm relationship' })).toBeEnabled()
    expect(screen.getByLabelText('Activity title')).toHaveValue('Second learning unit')
  })
})
