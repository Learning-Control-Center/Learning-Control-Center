import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

import { ErrorState } from '../../shared/components'

type State = { error: Error | null }

class RouteErrorBoundaryState extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Route rendering failed', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return <ErrorState message="This section could not be displayed." retry={() => this.setState({ error: null })} />
    }
    return this.props.children
  }
}

export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation()
  return <RouteErrorBoundaryState key={location.pathname}>{children}</RouteErrorBoundaryState>
}
