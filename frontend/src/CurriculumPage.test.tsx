import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { CurriculumPage } from './pages/CurriculumPage'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

describe('Curriculum V2 thin workflow', () => {
  beforeEach(() => {
    let activationAttempts = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/api/v2/curricula')) {
          return json([{ id: 'curr-1', stableKey: 'python', activeVersionId: 'version-1' }, { id: 'curr-2', stableKey: 'rust-internal-key', activeVersionId: null }])
        }
        if (url.endsWith('/api/v2/curricula/catalog/active')) {
          return json({
            activeVersionReferences: [
              { curriculumId: 'curr-1', versionId: 'version-1', version: 1 },
            ],
            units: [
              {
                curriculumId: 'curr-1',
                unitDefinitionId: 'unit-1',
                unitStableKey: 'variables',
                kind: 'practice_task',
                title: 'Practice variables',
                description: 'Write a program.',
                durationRangeMs: [1_800_000, 2_700_000, 3_600_000],
                requirements: [
                  { stableKey: 'editor', requirementType: 'resource_available', effect: 'hard' },
                ],
                evidenceOpportunities: [
                  { stableKey: 'output', evidenceKind: 'code' },
                ],
              },
              {
                curriculumId: 'curr-1', unitDefinitionId: 'unit-2', unitStableKey: 'functions', kind: 'practice_task', title: 'Practice functions', description: 'Write a function.', orderIndex: 1, durationRangeMs: null, requirements: [], evidenceOpportunities: [],
              },
              {
                curriculumId: 'curr-1', unitDefinitionId: 'unit-3', unitStableKey: 'services', kind: 'project_task', title: 'Build a service', description: 'Apply the skill.', orderIndex: 2, durationRangeMs: null, requirements: [], evidenceOpportunities: [],
              },
            ],
            inputHash: 'catalog-hash',
          })
        }
        if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
        if (url.endsWith('/api/v2/curricula/curr-1/versions')) {
          return json([
            {
              id: 'version-1',
              version: 1,
              title: 'Python v1',
              description: '',
              contentHash: 'one',
              effectiveAt: '2026-01-01T00:00:00Z',
            },
            {
              id: 'version-2',
              version: 2,
              title: 'Python v2',
              description: '',
              contentHash: 'two',
              effectiveAt: '2026-02-01T00:00:00Z',
            },
          ])
        }
        if (url.endsWith('/api/v2/curricula/curr-2/versions')) return json([{ id: 'version-rust-1', version: 1, title: 'Rust foundations', description: '', contentHash: 'rust', effectiveAt: '2026-01-01T00:00:00Z' }])
        if (url.endsWith('/api/v2/curricula/units/unit-1/availability')) {
          return json({
            availabilityState: 'unknown',
            readinessState: 'met',
            candidateUsabilityState: 'unknown',
            requirements: [],
            targetSuitability: [
              { targetId: 'target-1', state: 'met', reasonCode: 'UNASSESSED_SUPPORTED' },
            ],
          })
        }
        if (url.endsWith('/api/v2/curricula/units/unit-2/availability')) return json({ availabilityState: 'met', readinessState: 'met', candidateUsabilityState: 'met', requirements: [], targetSuitability: [] })
        if (url.endsWith('/api/v2/curricula/units/unit-3/availability')) return json({ availabilityState: 'not_met', readinessState: 'not_met', candidateUsabilityState: 'not_met', requirements: [{ stableKey: 'prior-work', state: 'not_met', reasonCode: 'REQUIREMENT_NOT_MET' }], targetSuitability: [] })
        if (
          url.endsWith('/api/v2/curricula/curr-1/versions/version-2/activate') &&
          init?.method === 'POST'
        ) {
          activationAttempts += 1
          return activationAttempts === 1
            ? json({ curriculumId: 'curr-1', activeVersionId: 'version-2' })
            : json({ error: { code: 'ACTIVATION_CONFLICT', message: 'The active version changed.' } }, 409)
        }
        return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
      }),
    )
  })

  it('shows immutable versions, readiness facts, and performs atomic activation', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><CurriculumPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Learn', level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('Practice variables')).toBeInTheDocument()
    expect(await screen.findByText('Practice functions')).toBeInTheDocument()
    expect(await screen.findByText('Build a service')).toBeInTheDocument()
    expect(screen.getByText('Rust foundations')).toBeInTheDocument()
    expect(screen.queryByText('rust-internal-key')).not.toBeInTheDocument()
    expect(await screen.findByText('v2 · Python v2')).toBeInTheDocument()

    expect(await screen.findByRole('heading', { name: 'Availability unknown' })).toBeInTheDocument()

    await user.click(screen.getByText('Curriculum version management'))
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    expect(screen.getByRole('dialog', { name: 'Confirm Curriculum activation' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Activate version' }))
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/v2/curricula/curr-1/versions/version-2/activate',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    if (!screen.queryByRole('button', { name: 'Review activation' })) await user.click(screen.getByText('Curriculum version management'))
    await user.click(screen.getByRole('button', { name: 'Review activation' }))
    await user.click(screen.getByRole('button', { name: 'Activate version' }))
    expect(await screen.findByText('The active version changed.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: /Rust foundations/ }))
    expect(await screen.findByText('Setup required')).toBeInTheDocument()
    expect(screen.getByText(/Activate an immutable Curriculum version/)).toBeInTheDocument()
  })
})
