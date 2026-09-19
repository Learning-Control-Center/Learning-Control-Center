import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { AuthProvider } from './auth'

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('learning-control authority routing', () => {
  it('makes Today V2 the default after activation and keeps labeled legacy history links', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/auth/session')) {
        return json({
          user_id: 'user-1',
          username: 'learner',
          csrf_token: 'csrf-token',
          absolute_expires_at: '2026-12-01T00:00:00.000Z',
        })
      }
      if (url.endsWith('/api/v2/authority')) {
        return json({
          canonicalLearningAuthority: 'v2',
          recommendationPresentation: 'v2',
          roadmapPresentation: 'v2',
          todayPresentation: 'v2',
          eventSequence: 2,
          stateHash: 'a'.repeat(64),
          updatedAt: '2026-09-19T00:00:00.000Z',
          policyVersion: 'learning-control-authority-policy/v1',
        })
      }
      if (url.endsWith('/api/v2/today/current')) {
        return json({ generation: null, continuingStartedSuggestions: [] })
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider><App /></AuthProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Today V2' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Legacy Today history' })).toHaveAttribute('href', '/legacy-today')
    expect(screen.getByRole('link', { name: 'Legacy Roadmap history' })).toHaveAttribute('href', '/legacy-roadmap')
    expect(screen.getByText('Center / V2')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/recommendations/today', expect.anything()))
  })

  it('uses the labeled read-only Today history when that presentation fallback is selected', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/auth/session')) {
        return json({ user_id: 'user-1', username: 'learner', csrf_token: 'csrf-token', absolute_expires_at: '2026-12-01T00:00:00.000Z' })
      }
      if (url.endsWith('/api/v2/authority')) {
        return json({
          canonicalLearningAuthority: 'v2', recommendationPresentation: 'v1_read_only',
          roadmapPresentation: 'v1_read_only', todayPresentation: 'v1_read_only',
          eventSequence: 3, stateHash: 'b'.repeat(64), updatedAt: '2026-09-19T00:00:00.000Z',
          policyVersion: 'learning-control-authority-policy/v1',
        })
      }
      if (url.endsWith('/api/v1/recommendations/history')) return json([])
      return json({})
    }))
    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider><App /></AuthProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Legacy Today history' })).toBeInTheDocument()
    expect(screen.getByText(/never generates work or records decisions/i)).toBeInTheDocument()
  })
})
