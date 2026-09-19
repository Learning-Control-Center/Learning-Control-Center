import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SessionsPage } from './pages/SessionsPage'
import { TodayPage } from './pages/TodayPage'

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('critical learning workflows', () => {
  it('explains and accepts the deterministic primary recommendation', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/recommendations/today')) {
        return json({
          recommendationVersion: 1,
          generatedAt: '2026-09-02T08:00:00.000Z',
          localDate: '2026-09-02',
          setupRequired: false,
          primary: {
            competencyIdentityId: 'competency-1',
            stableKey: 'python.functions',
            title: 'Python functions',
            activity: 'learning',
            suggestedDurationMs: 2_700_000,
            reasonCodes: ['CORE_COMPETENCY', 'BLOCKS_DOWNSTREAM_SKILLS'],
            activityReasonCode: 'ACTIVITY_LEARNING_CONCEPTUAL_THRESHOLD_UNMET',
            explanation: {},
          },
          secondary: null,
          snapshotId: 'snapshot-1',
          todayCompletedDurationMs: 0,
          todayTargetDurationMs: 3_600_000,
        })
      }
      if (url.includes('/analytics')) return json({ activeDays: 2, reviewDebt: { count: 1, weightedCount: 2 }, signals: [] })
      if (url.includes('/sessions/active')) return json({ active: false, session: null })
      if (url.includes('/reflections/')) return json({ reflection: null })
      if (url.endsWith('/sessions/timed') && init?.method === 'POST') return json({ id: 'session-1' }, 201)
      if (url.includes('/recommendations/snapshot-1/decision')) return json({ acceptedPrimary: true })
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<TodayPage />} />
          <Route path="/activity" element={<p>Session opened</p>} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Python functions' })).toBeInTheDocument()
    expect(screen.getByText('core competency')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Start focused session' }))
    expect(await screen.findByText('Session opened')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/recommendations/snapshot-1/decision',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('reconstructs a refresh-safe active timer and sends pause to the server', async () => {
    const activeSince = new Date(Date.now() - 65_000).toISOString()
    const activeSession = {
      id: 'session-1',
      competencyIdentityId: null,
      trackId: null,
      sessionMode: 'timed',
      timedState: 'running',
      activityType: 'practice',
      assistanceMode: 'none',
      startedAt: activeSince,
      endedAt: null,
      accumulatedDurationMs: 5_000,
      activeSince,
      durationMs: 70_000,
      difficulty: null,
      outcome: null,
      notes: null,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/v2/activities')) return json([])
      if (url.includes('/api/v2/roadmap-projection/current')) return json({ configured: false, nodes: [], edges: [] })
      if (url.includes('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.includes('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.includes('/sessions/active')) return json({ active: true, session: activeSession })
      if (url.includes('/sessions?')) return json({ items: [] })
      if (url.includes('/api/v2/sessions/session-1/pause') && init?.method === 'POST') return json(activeSession)
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <SessionsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/1:1\d/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v2/sessions/session-1/pause',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })
})
