/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { ApiError, apiV2 } from './api'

export type AuthorityState = {
  canonicalLearningAuthority: 'legacy_v1' | 'v2'
  recommendationPresentation: 'legacy_v1' | 'v2' | 'v1_read_only'
  roadmapPresentation: 'legacy_v1' | 'v2' | 'v1_read_only'
  todayPresentation: 'legacy_v1' | 'v2' | 'v1_read_only'
  eventSequence: number
  stateHash: string
  updatedAt: string
  policyVersion: string
}

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
    let active = true
    apiV2<AuthorityState>('/authority')
      .then((value) => {
        if (active) setState(value)
      })
      .catch((caught) => {
        if (active) {
          setError(caught instanceof ApiError ? caught.message : 'Learning authority could not be loaded.')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const value = useMemo(() => ({ state, loading, error }), [state, loading, error])
  return <AuthorityContext.Provider value={value}>{children}</AuthorityContext.Provider>
}

export function useAuthority() {
  const value = useContext(AuthorityContext)
  if (!value) throw new Error('useAuthority must be used inside AuthorityProvider.')
  return value
}
