import { render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { AuthProvider } from './auth'

describe('application authentication boundary', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/session')) {
          return new Response(JSON.stringify({ error: { code: 'AUTH_REQUIRED', message: 'Authentication is required.' } }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(JSON.stringify({ bootstrapAvailable: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    )
  })

  it('shows protected first setup when no user exists', async () => {
    render(
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>,
    )
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Create the first account' })).toBeInTheDocument())
    expect(screen.getByLabelText('Bootstrap secret')).toBeInTheDocument()
  })
})
