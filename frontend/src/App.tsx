import { useAuth } from './auth'
import { AuthorityProvider, useAuthority } from './authority'
import { AppRoutes } from './app/router/AppRoutes'
import { RouteErrorBoundary } from './app/router/RouteErrorBoundary'
import { ErrorState, LoadingState } from './shared/components'
import { LoginPage } from './pages/LoginPage'

function AuthorizedApplication() {
  const { state, loading, error } = useAuthority()
  if (loading) return <main className="grid min-h-screen place-items-center p-5"><div className="w-full max-w-xl"><LoadingState label="Loading learning authority" /></div></main>
  if (error || !state) return <main className="grid min-h-screen place-items-center p-5"><div className="w-full max-w-xl"><ErrorState message={error || 'Learning authority is unavailable.'} /></div></main>
  return <RouteErrorBoundary><AppRoutes /></RouteErrorBoundary>
}

export function App() {
  const { session, loading } = useAuth()
  if (loading) {
    return (
      <main className="grid min-h-screen place-items-center p-5">
        <div className="w-full max-w-xl"><LoadingState label="Opening your private learning workspace" /></div>
      </main>
    )
  }
  if (!session) return <LoginPage />
  return (
    <AuthorityProvider><AuthorizedApplication /></AuthorityProvider>
  )
}
