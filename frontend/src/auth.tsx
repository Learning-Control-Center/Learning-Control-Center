/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'

import { api, setCsrfToken } from './api'

type AuthSession = {
  user_id: string
  username: string
  csrf_token: string
  absolute_expires_at: string
}

type AuthContextValue = {
  session: AuthSession | null
  loading: boolean
  bootstrapAvailable: boolean
  refresh: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  bootstrap: (username: string, password: string, bootstrapToken: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [bootstrapAvailable, setBootstrapAvailable] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const current = await api<AuthSession>('/auth/session')
      setSession(current)
      setCsrfToken(current.csrf_token)
    } catch {
      setSession(null)
      setCsrfToken('')
      const status = await api<{ bootstrapAvailable: boolean }>('/auth/bootstrap-status')
      setBootstrapAvailable(status.bootstrapAvailable)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const acceptSession = (value: AuthSession) => {
    setSession(value)
    setCsrfToken(value.csrf_token)
  }

  const login = async (username: string, password: string) => {
    acceptSession(
      await api<AuthSession>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      }),
    )
  }

  const bootstrap = async (username: string, password: string, bootstrapToken: string) => {
    acceptSession(
      await api<AuthSession>('/auth/bootstrap', {
        method: 'POST',
        body: JSON.stringify({ username, password, bootstrap_token: bootstrapToken }),
      }),
    )
  }

  const logout = async () => {
    await api<void>('/auth/logout', { method: 'POST' })
    setSession(null)
    setCsrfToken('')
    setBootstrapAvailable(false)
  }

  const value = { session, loading, bootstrapAvailable, refresh, login, bootstrap, logout }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
