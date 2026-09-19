import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TodayV2Page } from './pages/TodayV2Page'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const suggestion = {
  id: 'suggestion-1',
  portfolioRole: 'primary',
  advisoryDurationMs: 1_800_000,
  durationRangeMs: [900_000, 1_800_000, 2_700_000],
  presentation: {
    title: 'Practice JavaScript functions',
    description: 'Complete the authored exercise.',
    candidateType: 'curriculum_unit',
    reasonSummary: 'Addresses the current capability gap.',
    reasons: [{ code: 'CAPABILITY_GAP', text: 'A required capability gap is open.' }],
  },
  timezone: 'UTC',
  expiresAt: '2026-09-20T00:00:00Z',
  presentationExpired: false,
  status: 'suggested',
  terminal: false,
  interactions: [],
  activityRelations: [],
}

const current = {
  generation: {
    id: 'generation-1',
    localDate: '2026-09-19',
    timezone: 'UTC',
    generationSequence: 1,
    generationKey: 'generation-key',
    generatedAt: '2026-09-19T08:00:00Z',
    regeneration: false,
    suggestions: [suggestion],
  },
  continuingStartedSuggestions: [],
}

describe('Today V2 minimum surface', () => {
  beforeEach(() => {
    cleanup()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/api/v2/today/current')) return json(current)
        if (url.endsWith('/api/v2/today/suggestions/suggestion-1/viewed')) {
          expect(init?.method).toBe('POST')
          return json({ ...suggestion, status: 'viewed' }, 201)
        }
        throw new Error(`Unexpected URL: ${url}`)
      }),
    )
  })

  it('reads without recording a view and exposes explicit no-debt interactions', async () => {
    const user = userEvent.setup()
    render(<TodayV2Page />, { wrapper: MemoryRouter })

    expect(await screen.findByRole('heading', { name: 'Today V2' })).toBeInTheDocument()
    expect(await screen.findByText('Practice JavaScript functions')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Skip without debt' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch).toHaveBeenNthCalledWith(
      1,
      '/api/v2/today/current',
      expect.not.objectContaining({ method: 'POST' }),
    )

    await user.click(screen.getByRole('button', { name: 'Mark viewed' }))
    expect(fetch).toHaveBeenCalledWith(
      '/api/v2/today/suggestions/suggestion-1/viewed',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('keeps started work from an earlier generation visible and actionable', async () => {
    const continuing = {
      ...suggestion,
      id: 'continuing-1',
      status: 'started',
      interactions: [
        { id: 'started-interaction', type: 'started', sessionId: 'session-1', correction: null },
      ],
    }
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/api/v2/today/current')) {
        return json({ generation: null, continuingStartedSuggestions: [continuing] })
      }
      throw new Error(`Unexpected URL: ${String(input)}`)
    })
    render(<TodayV2Page />, { wrapper: MemoryRouter })
    expect(await screen.findByText('Continuing actual work')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Complete from session' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Partially complete' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record replacement' })).toBeInTheDocument()
  })

  it('disables prospective actions after expiry but preserves actual-work reconciliation', async () => {
    const expiredPresentation = { ...suggestion, presentationExpired: true }
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/api/v2/today/current')) {
        return json({
          ...current,
          generation: { ...current.generation, suggestions: [expiredPresentation] },
        })
      }
      throw new Error(`Unexpected URL: ${String(input)}`)
    })
    render(<TodayV2Page />, { wrapper: MemoryRouter })
    expect(await screen.findByRole('button', { name: 'Start actual work' })).toBeDisabled()
    expect(screen.getByText(/replacement remains available/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record replacement' })).toBeInTheDocument()
  })
})
