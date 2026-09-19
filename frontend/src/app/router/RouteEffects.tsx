import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

import { LiveNotice } from '../../shared/components'
import { focusElement } from '../../shared/accessibility/focus'
import { metadataForPath } from './routes'

export function RouteEffects() {
  const location = useLocation()
  const previousPath = useRef<string | null>(null)
  const metadata = metadataForPath(location.pathname)

  useEffect(() => {
    document.title = `${metadata.title} · Learning Control Center`
    if (previousPath.current === location.pathname) return
    previousPath.current = location.pathname

    let cancelled = false
    let fallback = 0
    const focusRoute = () => {
      if (cancelled) return
      const target = document.querySelector<HTMLElement>(metadata.focusTarget)
      if (target) {
        focusElement(target)
        window.clearTimeout(fallback)
        return true
      }
      return false
    }
    const main = document.getElementById('main-content')
    const observer = new MutationObserver(() => {
      if (focusRoute()) observer.disconnect()
    })
    if (main) observer.observe(main, { childList: true, subtree: true })
    const frame = window.requestAnimationFrame(() => {
      if (focusRoute()) observer.disconnect()
    })
    fallback = window.setTimeout(() => {
      observer.disconnect()
      if (!cancelled) main?.focus({ preventScroll: true })
    }, 5000)
    return () => {
      cancelled = true
      observer.disconnect()
      window.cancelAnimationFrame(frame)
      window.clearTimeout(fallback)
    }
  }, [location.pathname, metadata.focusTarget, metadata.title])

  return <LiveNotice>{metadata.title} page</LiveNotice>
}
