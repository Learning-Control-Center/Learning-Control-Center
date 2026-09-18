import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CurriculumPage } from './pages/CurriculumPage'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

describe('Curriculum V2 thin workflow', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/api/v2/curricula')) {
          return json([{ id: 'curr-1', stableKey: 'python', activeVersionId: 'version-1' }])
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
            ],
            inputHash: 'catalog-hash',
          })
        }
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
        if (
          url.endsWith('/api/v2/curricula/curr-1/versions/version-2/activate') &&
          init?.method === 'POST'
        ) {
          return json({ curriculumId: 'curr-1', activeVersionId: 'version-2' })
        }
        return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
      }),
    )
  })

  it('shows immutable versions, readiness facts, and performs atomic activation', async () => {
    const user = userEvent.setup()
    render(<CurriculumPage />)

    expect(await screen.findByRole('heading', { name: 'Curriculum' })).toBeInTheDocument()
    expect(await screen.findByText('Practice variables')).toBeInTheDocument()
    expect(await screen.findByText('v2 · Python v2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Inspect readiness' }))
    expect(
      await screen.findByText(
        /Availability: unknown; readiness: met; candidate usability: unknown; target suitability: met/,
      ),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Activate atomically' }))
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/v2/curricula/curr-1/versions/version-2/activate',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })
})
