import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ProjectsPage } from './pages/ProjectsPage'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

describe('Projects V2 thin workflow', () => {
  beforeEach(() => {
    vi.stubGlobal('crypto', { randomUUID: () => 'event-key' })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v2/projects')) return json([{ id: 'project-1', stableKey: 'ship-python', activeVersionId: 'version-1', lifecycleState: 'active' }])
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [{ project_id: 'project-1', task_definition_id: 'task-def-1', task_identity_id: 'task-1', task_stable_key: 'build', title: 'Build service', lifecycle_state: 'not_started', availability_state: 'met', readiness_state: 'met', candidate_usability_state: 'met', actionable_blocker_keys: [], duration_range_ms: [300000, 600000, 900000] }] })
      if (url.endsWith('/api/v2/projects/project-1/versions')) return json([{ id: 'version-1', version: 1, title: 'Ship a Python Service', description: 'Outcome work', effectiveAt: '2026-01-01T00:00:00Z', tasks: [{ id: 'task-def-1', identityId: 'task-1', stableKey: 'build', title: 'Build service', description: '', preferredDurationMs: 600000 }], criteria: [{ id: 'criterion-1', stableKey: 'tests-pass', title: 'Tests pass' }] }])
      if (url.endsWith('/api/v2/projects/project-1/events') && init?.method === 'POST') return json({ id: 'event-2' }, 201)
      if (url.endsWith('/api/v2/projects/project-1/events')) return json([{ id: 'event-1', eventType: 'project_lifecycle', taskIdentityId: null, taskLifecycleState: null, blockerKey: null, occurredAt: '2026-01-01T00:00:00Z' }])
      if (url.endsWith('/api/v2/projects/criteria/criterion-1/evaluations')) return json([{ id: 'evaluation-1', state: 'demonstrated', evaluatedAt: '2026-01-02T00:00:00Z', evidenceIds: ['evidence-1'] }])
      return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
    }))
  })

  it('shows actual-work boundaries, criterion Evidence, history, and starts a task', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><ProjectsPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Projects' })).toBeInTheDocument()
    expect(await screen.findByText('Build service')).toBeInTheDocument()
    expect(await screen.findByText('demonstrated · 1 Evidence records')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Log actual work' })).toHaveAttribute('href', '/activity')

    await user.click(screen.getByRole('button', { name: 'Start task' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/api/v2/projects/project-1/events',
      expect.objectContaining({ method: 'POST' }),
    ))
  })
})
