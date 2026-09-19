import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { SessionsPage } from './pages/SessionsPage'
import { CurriculumPage } from './pages/CurriculumPage'
import { ProfileCapabilityPage } from './pages/ProfileCapabilityPage'
import { ProjectsPage } from './pages/ProjectsPage'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const failed = (message: string) => json({ error: { code: 'DEPENDENCY_FAILED', message } }, 503)

afterEach(() => vi.unstubAllGlobals())

describe('Checkpoint 4 section isolation and cancellation', () => {
  it('keeps Profile capability truth available when related Curriculum and Project catalogs fail', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/target-profiles')) return json([{ id: 'p1', stableKey: 'profile', activeVersionId: 'v1', versions: [{ versionId: 'v1', version: 1, title: 'Engineer', description: '', targets: [{ id: 't1', stableKey: 'target', competencyIdentityId: 'c1', dimensionKey: null, domainStableKey: 'engineering', targetLevelStableKey: 'independent', priority: 'core' }] }] }])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [{ id: 'c1', semanticDefinitionId: 'd1', stableKey: 'competency', title: 'Service delivery', profileTargets: [{ id: 't1', dimensionKey: null, targetLevelTitle: 'Independent', priority: 'core', targetLevelOrdinal: 3 }], capability: { scopes: [] } }], edges: [] })
      if (url.endsWith('/api/v2/analysis/current')) return json({ configured: false, snapshot: null })
      if (url.includes('/curricula/catalog/') || url.includes('/projects/catalog/')) return failed('Auxiliary catalog unavailable')
      return json({})
    }))
    render(<MemoryRouter><ProfileCapabilityPage /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'Profile', level: 1 })).toBeInTheDocument()
    expect(screen.getByText('Service delivery')).toBeInTheDocument()
  })

  it('keeps Learn actions available when Roadmap cross-links fail', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/curricula')) return json([{ id: 'curr-1', stableKey: 'curriculum', activeVersionId: 'version-1' }])
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [{ curriculumId: 'curr-1', unitDefinitionId: 'unit-1', unitStableKey: 'practice', kind: 'practice_task', title: 'Practice service delivery', description: '', durationRangeMs: null, requirements: [], evidenceOpportunities: [] }] })
      if (url.endsWith('/api/v2/curricula/curr-1/versions')) return json([{ id: 'version-1', version: 1, title: 'Service curriculum', description: '', contentHash: 'hash', effectiveAt: '2026-01-01T00:00:00Z' }])
      if (url.endsWith('/api/v2/curricula/units/unit-1/availability')) return json({ availabilityState: 'met', readinessState: 'met', candidateUsabilityState: 'met', requirements: [], targetSuitability: [] })
      if (url.endsWith('/api/v2/roadmap-projection/current')) return failed('Roadmap unavailable')
      return json({})
    }))
    render(<MemoryRouter><CurriculumPage /></MemoryRouter>)
    expect(await screen.findByText('Practice service delivery')).toBeInTheDocument()
    expect(screen.getByText(/Related competency links could not be loaded/)).toBeInTheDocument()
  })

  it('keeps Project tasks available when Roadmap cross-links fail', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/projects')) return json([{ id: 'project-1', stableKey: 'project', activeVersionId: 'version-1', lifecycleState: 'active' }])
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [{ project_id: 'project-1', project_version_id: 'version-1', task_definition_id: 'task-1', task_identity_id: 'task-identity-1', task_stable_key: 'ship', title: 'Ship service', lifecycle_state: 'not_started', availability_state: 'met', readiness_state: 'met', candidate_usability_state: 'met', actionable_blocker_keys: [], duration_range_ms: null }] })
      if (url.endsWith('/api/v2/projects/project-1/versions')) return json([{ id: 'version-1', version: 1, title: 'Service outcome', description: '', effectiveAt: '2026-01-01T00:00:00Z', tasks: [{ id: 'task-1', identityId: 'task-identity-1', stableKey: 'ship', title: 'Ship service', description: '', preferredDurationMs: null }], criteria: [] }])
      if (url.endsWith('/api/v2/projects/project-1/events')) return json([])
      if (url.endsWith('/api/v2/roadmap-projection/current')) return failed('Roadmap unavailable')
      return json({})
    }))
    render(<MemoryRouter><ProjectsPage /></MemoryRouter>)
    expect(await screen.findByText('Ship service')).toBeInTheDocument()
    expect(screen.getByText(/Related competency and Roadmap links could not be loaded/)).toBeInTheDocument()
  })

  it('keeps unlinked Activity and Session work available when optional canonical reference sources fail', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v2/activities')) return json([])
      if (url.endsWith('/api/v1/sessions/active')) return json({ active: false, session: null })
      if (url.includes('/api/v1/sessions?')) return json({ items: [] })
      if (url.includes('/roadmap-projection/') || url.includes('/curricula/catalog/') || url.includes('/projects/catalog/')) return failed('Optional source unavailable')
      return json({})
    }))
    render(<MemoryRouter><SessionsPage /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'Activity', level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/optional canonical reference sources could not be loaded/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Activity title'), 'Independent work')
    expect(screen.getByRole('button', { name: 'Create Activity' })).toBeEnabled()
  })

  it('aborts in-flight section requests when a product surface unmounts', async () => {
    let aborted = false
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/v2/curricula')) return Promise.resolve(json([]))
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => { aborted = true; reject(new DOMException('Aborted', 'AbortError')) })
      })
    }))
    const view = render(<MemoryRouter><CurriculumPage /></MemoryRouter>)
    view.unmount()
    await vi.waitFor(() => expect(aborted).toBe(true))
  })
})
