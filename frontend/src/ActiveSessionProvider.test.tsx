import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ActiveSessionSlot } from './app/layout/ActiveSessionSlot'
import { SessionsPage } from './pages/SessionsPage'
import { ActiveSessionProvider, useActiveSession } from './shared/session/ActiveSessionProvider'
import type { Session } from './types'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

function RefreshControl() {
  const { refresh } = useActiveSession()
  return <button onClick={() => void refresh()}>Refresh active resource</button>
}

describe('shared Active Session resource', () => {
  it('keeps shell consumers synchronized and leaves per-second time silent', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const running = { id: 'session-1', competencyIdentityId: null, trackId: null, sessionMode: 'timed', timedState: 'running', activityType: 'practice', assistanceMode: 'none', startedAt: new Date(Date.now() - 5_000).toISOString(), endedAt: null, accumulatedDurationMs: 0, activeSince: new Date(Date.now() - 5_000).toISOString(), durationMs: 5_000, difficulty: null, outcome: null, notes: null }
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(async () => { calls += 1; return json({ active: true, session: calls === 1 ? running : { ...running, timedState: 'paused', activeSince: null, accumulatedDurationMs: 8_000, durationMs: 8_000 } }) }))
    render(<MemoryRouter><ActiveSessionProvider><ActiveSessionSlot /><RefreshControl /></ActiveSessionProvider></MemoryRouter>)

    expect(await screen.findByText('Session running')).toBeInTheDocument()
    expect(screen.getByText(/practice/)).toHaveAttribute('aria-live', 'off')
    const stateAnnouncement = screen.getByRole('status', { name: '' })
    expect(stateAnnouncement).toHaveTextContent('Session running')
    const silentDuration = screen.getByText(/practice/)
    expect(silentDuration).toHaveAttribute('aria-live', 'off')
    act(() => vi.advanceTimersByTime(2_000))
    expect(silentDuration).toHaveAttribute('aria-live', 'off')
    expect(stateAnnouncement).toHaveTextContent('Session running')
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole('button', { name: 'Refresh active resource' }))
    await waitFor(() => expect(screen.getByText('Session paused')).toBeInTheDocument())
    expect(calls).toBe(2)
  })

  it('marks retained Session state unavailable when authoritative refresh fails', async () => {
    const running = { id: 'session-1', competencyIdentityId: null, trackId: null, sessionMode: 'timed', timedState: 'running', activityType: 'practice', assistanceMode: 'none', startedAt: new Date().toISOString(), endedAt: null, accumulatedDurationMs: 0, activeSince: new Date().toISOString(), durationMs: 0, difficulty: null, outcome: null, notes: null }
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      calls += 1
      return calls === 1 ? json({ active: true, session: running }) : new Response(JSON.stringify({ error: { code: 'UNAVAILABLE', message: 'Session authority could not be reached.' } }), { status: 503, headers: { 'Content-Type': 'application/json' } })
    }))
    render(<MemoryRouter><ActiveSessionProvider><ActiveSessionSlot /><RefreshControl /></ActiveSessionProvider></MemoryRouter>)

    expect(await screen.findByText('Session running')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh active resource' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Session state unavailable')
    expect(screen.queryByText('Session running')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review Activity' })).toHaveAttribute('href', '/activity')
  })

  it('announces each material timer transition once while per-second ticks stay silent', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const baseSession: Session = { id: 'session-1', competencyIdentityId: null, trackId: null, sessionMode: 'timed', timedState: 'running', activityType: 'practice', assistanceMode: 'none', startedAt: new Date(Date.now() - 5_000).toISOString(), endedAt: null, accumulatedDurationMs: 0, activeSince: new Date(Date.now() - 5_000).toISOString(), durationMs: 5_000, difficulty: null, outcome: null, notes: null }
    let active: Session | null = baseSession
    let failNextPause = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/sessions/active')) return json({ active: Boolean(active), session: active })
      if (url.endsWith('/api/v2/activities')) return json([{ id: 'activity-1', title: 'Practice', description: null, categoryStableKey: 'practice', occurredAt: null, createdAt: new Date().toISOString() }])
      if (url.includes('/api/v1/sessions?')) return json({ items: active ? [active] : [] })
      if (url.endsWith('/api/v2/roadmap-projection/current')) return json({ configured: true, nodes: [], edges: [] })
      if (url.endsWith('/api/v2/curricula/catalog/active')) return json({ units: [] })
      if (url.endsWith('/api/v2/projects/catalog/current')) return json({ candidates: [] })
      if (url.endsWith('/api/v1/settings/discipline')) return json({ timezone: 'UTC' })
      if (url.includes('/api/v1/reflections/')) return json({ reflection: null })
      if (url.endsWith('/api/v2/sessions/session-1/pause') && init?.method === 'POST') {
        if (failNextPause) return json({ error: { code: 'UNAVAILABLE', message: 'Timer authority rejected the transition.' } }, 503)
        active = { ...baseSession, timedState: 'paused', activeSince: null, accumulatedDurationMs: 8_000, durationMs: 8_000 }
        return json(active)
      }
      if (url.endsWith('/api/v2/sessions/session-1/resume') && init?.method === 'POST') {
        active = { ...baseSession, timedState: 'running', activeSince: new Date().toISOString(), accumulatedDurationMs: 8_000, durationMs: 8_000 }
        return json(active)
      }
      if (url.endsWith('/api/v2/sessions/session-1/complete') && init?.method === 'POST') { active = null; return json({ ...baseSession, timedState: 'completed', outcome: 'completed' }) }
      if (url.endsWith('/api/v2/sessions/timed') && init?.method === 'POST') { active = { ...baseSession, activeSince: new Date().toISOString() }; return json(active, 201) }
      if (url.endsWith('/api/v2/sessions/session-1/cancel') && init?.method === 'POST') { active = null; return json({ ...baseSession, timedState: 'cancelled' }) }
      throw new Error(`Unexpected URL: ${url}`)
    }))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(<MemoryRouter><ActiveSessionProvider><ActiveSessionSlot /><SessionsPage /></ActiveSessionProvider></MemoryRouter>)

    const nonEmptyPoliteAnnouncements = () => screen.getAllByRole('status').map((item) => item.textContent?.trim()).filter(Boolean)
    expect(await screen.findByText('Session running')).toBeInTheDocument()
    expect(nonEmptyPoliteAnnouncements()).toEqual(['Session running'])
    act(() => vi.advanceTimersByTime(2_000))
    expect(nonEmptyPoliteAnnouncements()).toEqual(['Session running'])

    await user.click(await screen.findByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(nonEmptyPoliteAnnouncements()).toEqual(['Session paused']))
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(nonEmptyPoliteAnnouncements()).toEqual(['Session running']))
    await user.click(screen.getByRole('button', { name: 'Complete' }))
    await waitFor(() => expect(nonEmptyPoliteAnnouncements()).toEqual(['Timer completed.']))

    await user.click(screen.getByRole('button', { name: 'Start timer' }))
    await waitFor(() => expect(nonEmptyPoliteAnnouncements()).toEqual(['Session running']))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(nonEmptyPoliteAnnouncements()).toEqual(['Timer cancelled.']))

    await user.click(screen.getByRole('button', { name: 'Start timer' }))
    await waitFor(() => expect(nonEmptyPoliteAnnouncements()).toEqual(['Session running']))
    failNextPause = true
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(screen.getAllByRole('alert').filter((item) => item.textContent?.includes('Timer authority rejected')).length).toBe(1))
    expect(nonEmptyPoliteAnnouncements()).toEqual(['Session running'])
  })
})
