/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { ApiError, apiV2 } from './api'
import type { AuthorityState } from './shared/contracts/authority'

export type { AuthorityState } from './shared/contracts/authority'

type AuthorityContextValue = {
  state: AuthorityState | null
  loading: boolean
  error: string
}

const AuthorityContext = createContext<AuthorityContextValue | null>(null)

export function AuthorityProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthorityState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    apiV2<AuthorityState>('/authority', { signal: controller.signal })
      .then((value) => {
        setState(value)
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === 'AbortError') return
        setError(caught instanceof ApiError ? caught.message : 'Learning authority could not be loaded.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const value = useMemo(() => ({ state, loading, error }), [state, loading, error])
  return <AuthorityContext.Provider value={value}>{children}</AuthorityContext.Provider>
}

export function useAuthority() {
  const value = useContext(AuthorityContext)
  if (!value) throw new Error('useAuthority must be used inside AuthorityProvider.')
  return value
}
