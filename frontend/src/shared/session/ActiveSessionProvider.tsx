/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

import { ApiError, api } from '../../api'
import type { Session } from '../../types'

export type ActiveSessionStatus = 'loading' | 'ready' | 'error'
type ActiveSessionResource = { active: Session | null; status: ActiveSessionStatus; loading: boolean; error: string; refresh: () => Promise<Session | null> }
const inactiveResource: ActiveSessionResource = { active: null, status: 'ready', loading: false, error: '', refresh: async () => null }
const ActiveSessionContext = createContext<ActiveSessionResource>(inactiveResource)

export function ActiveSessionProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<Session | null>(null)
  const [status, setStatus] = useState<ActiveSessionStatus>('loading')
  const [error, setError] = useState('')
  const requestSequence = useRef(0)
  const refresh = useCallback(async () => {
    const sequence = ++requestSequence.current
    setStatus('loading')
    try {
      const response = await api<{ active: boolean; session: Session | null }>('/sessions/active')
      if (sequence !== requestSequence.current) return response.session
      setActive(response.session); setError(''); setStatus('ready')
      return response.session
    } catch (caught) {
      if (sequence === requestSequence.current) {
        setError(caught instanceof ApiError ? caught.message : 'Active Session state could not be loaded.')
        setStatus('error')
      }
      return null
    }
  }, [])
  useEffect(() => { void refresh() }, [refresh])
  return <ActiveSessionContext.Provider value={{ active, status, loading: status === 'loading', error, refresh }}>{children}</ActiveSessionContext.Provider>
}

export function useActiveSession() { return useContext(ActiveSessionContext) }

export function sessionElapsedDuration(session: Session, at: number) {
  return session.timedState === 'running' && session.activeSince
    ? session.accumulatedDurationMs + Math.max(0, at - Date.parse(session.activeSince))
    : session.durationMs ?? session.accumulatedDurationMs
}
