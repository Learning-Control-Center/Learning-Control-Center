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
      if (url.endsWith('/api/v2/projects')) return json([{ id: 'project-1', stableKey: 'ship-python', activeVersionId: 'version-1', lifecycleState: 'active' }, { id: 'project-2', stableKey: 'internal-empty-key', activeVersionId: 'version-empty', lifecycleState: 'active' }])
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [{ project_id: 'project-1', project_version_id: 'version-1', task_definition_id: 'task-def-1', task_identity_id: 'task-1', task_stable_key: 'build', title: 'Build service', lifecycle_state: 'not_started', availability_state: 'met', readiness_state: 'met', candidate_usability_state: 'met', actionable_blocker_keys: [], duration_range_ms: [300000, 600000, 900000] }, { project_id: 'project-1', project_version_id: 'version-1', task_definition_id: 'task-def-2', task_identity_id: 'task-2', task_stable_key: 'review', title: 'Review delivery', lifecycle_state: 'completed', availability_state: 'met', readiness_state: 'met', candidate_usability_state: 'met', actionable_blocker_keys: [], duration_range_ms: null }, { project_id: 'project-1', project_version_id: 'version-1', task_definition_id: 'task-def-3', task_identity_id: 'task-3', task_stable_key: 'release', title: 'Release service', lifecycle_state: 'blocked', availability_state: 'not_met', readiness_state: 'not_met', candidate_usability_state: 'not_met', actionable_blocker_keys: ['missing_review'], duration_range_ms: null }] })
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/projects/project-1/versions')) return json([{ id: 'version-1', version: 1, title: 'Ship a Python Service', description: 'Outcome work', effectiveAt: '2026-01-01T00:00:00Z', tasks: [{ id: 'task-def-1', identityId: 'task-1', stableKey: 'build', title: 'Build service', description: '', preferredDurationMs: 600000 }, { id: 'task-def-2', identityId: 'task-2', stableKey: 'review', title: 'Review delivery', description: '', preferredDurationMs: null }, { id: 'task-def-3', identityId: 'task-3', stableKey: 'release', title: 'Release service', description: '', preferredDurationMs: null }], criteria: [{ id: 'criterion-1', stableKey: 'tests-pass', title: 'Tests pass' }] }])
      if (url.endsWith('/api/v2/projects/project-2/versions')) return json([{ id: 'version-empty', version: 1, title: 'Empty outcome project', description: 'No tasks yet', effectiveAt: '2026-01-01T00:00:00Z', tasks: [], criteria: [] }])
      if (url.endsWith('/api/v2/projects/project-1/events') && init?.method === 'POST') return json({ id: 'event-2' }, 201)
      if (url.endsWith('/api/v2/projects/project-1/events')) return json([{ id: 'event-1', eventType: 'project_lifecycle', taskIdentityId: null, taskLifecycleState: null, blockerKey: null, occurredAt: '2026-01-01T00:00:00Z' }])
      if (url.endsWith('/api/v2/projects/criteria/criterion-1/evaluations')) return json([{ id: 'evaluation-1', state: 'demonstrated', evaluatedAt: '2026-01-02T00:00:00Z', evidenceIds: ['evidence-1'] }])
      return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
    }))
  })

  it('shows actual-work boundaries, criterion Evidence, history, and starts a task', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><ProjectsPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Projects', level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('Build service')).toBeInTheDocument()
    expect(await screen.findByText('Review delivery')).toBeInTheDocument()
    expect(await screen.findByText('Release service')).toBeInTheDocument()
    expect(screen.getByText('missing review')).toBeInTheDocument()
    expect(screen.getByText('Empty outcome project')).toBeInTheDocument()
    expect(screen.queryByText('internal-empty-key')).not.toBeInTheDocument()
    expect(await screen.findByText('demonstrated · 1 Evidence record')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Start or log actual work' })[0]).toHaveAttribute('href', expect.stringContaining('/activity?'))

    await user.click(screen.getByRole('button', { name: 'Start task' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/api/v2/projects/project-1/events',
      expect.objectContaining({ method: 'POST' }),
    ))
    await user.click(screen.getByRole('button', { name: /Empty outcome project/ }))
    expect(await screen.findByText('No active Project tasks')).toBeInTheDocument()
  })
})
