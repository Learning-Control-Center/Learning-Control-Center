import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DailyReflectionEditor } from './DailyReflectionEditor'

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('daily reflection resource', () => {
  it('loads and saves only the explicitly scoped daily note', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT') return json({ reflection: { text: 'Keep the invariant' } })
      return json({ reflection: { text: 'Initial note' } })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<DailyReflectionEditor localDate="2026-09-19" />)
    const field = await screen.findByRole('textbox', { name: 'Reflection' })
    await waitFor(() => expect(field).toHaveValue('Initial note'))
    await userEvent.clear(field)
    await userEvent.type(field, 'Keep the invariant')
    await userEvent.click(screen.getByRole('button', { name: 'Save reflection' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/reflections/2026-09-19', expect.objectContaining({ method: 'PUT' })))
    expect(await screen.findByText('Reflection saved for 2026-09-19.')).toBeInTheDocument()
  })
})
