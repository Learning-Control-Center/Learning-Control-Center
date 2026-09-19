import { describe, expect, it } from 'vitest'

import { errorMessage, isAbortError, type MutationState, type RemoteResource } from './remoteResource'

describe('remote resource conventions', () => {
  it('represents each resource state without discarding prior data during refresh or recoverable failure', () => {
    const states = [
      { status: 'idle' },
      { status: 'loading' },
      { status: 'success', data: ['one'] },
      { status: 'refreshing', data: ['one'] },
      { status: 'error', data: ['one'], error: 'Refresh failed.' },
    ] satisfies Array<RemoteResource<string[]>>

    expect(states.map(({ status }) => status)).toEqual([
      'idle',
      'loading',
      'success',
      'refreshing',
      'error',
    ])
    expect(states[3]).toMatchObject({ data: ['one'] })
    expect(states[4]).toMatchObject({ data: ['one'], error: 'Refresh failed.' })
  })

  it('keeps mutation state separate from remote reads', () => {
    const states = [
      { status: 'idle' },
      { status: 'pending' },
      { status: 'error', error: 'Could not save.' },
      { status: 'success' },
    ] satisfies MutationState[]
    expect(states.map(({ status }) => status)).toEqual(['idle', 'pending', 'error', 'success'])
  })

  it('suppresses abort errors and safely normalizes other failures', () => {
    const aborted = new DOMException('The operation was aborted.', 'AbortError')
    expect(isAbortError(aborted)).toBe(true)
    expect(errorMessage(aborted, 'Fallback')).toBe('')
    expect(errorMessage(new Error('Server unavailable.'), 'Fallback')).toBe('Server unavailable.')
    expect(errorMessage({ unsafe: 'detail' }, 'Safe fallback.')).toBe('Safe fallback.')
  })
})
