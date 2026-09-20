import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { formatDuration } from '../../api'
import { sessionElapsedDuration, useActiveSession } from '../../shared/session/ActiveSessionProvider'
import { paths } from '../../shared/navigation/paths'

export function ActiveSessionSlot({ compact = false }: { compact?: boolean }) {
  const { active, status, loading, error } = useActiveSession()
  const [tick, setTick] = useState(Date.now())
  useEffect(() => { if (active?.timedState !== 'running') return; const timer = window.setInterval(() => setTick(Date.now()), 1000); return () => window.clearInterval(timer) }, [active?.timedState])
  if (loading && !active) return compact ? null : <div className="px-2 pb-3 text-xs text-white/60" role="status">Checking active Session…</div>
  if (error) return <div className={compact ? 'rounded-lg border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-800' : 'mb-3 rounded-xl border border-rose-300/40 bg-rose-500/10 p-3 text-xs text-white'} role="alert"><p>Session state unavailable</p><Link className="mt-1 inline-flex font-medium underline" to={paths.activity}>Review Activity</Link></div>
  if (!active) return null
  if (status !== 'ready') return null
  const duration = sessionElapsedDuration(active, tick)
  return <div className={compact ? 'rounded-lg border border-ink/15 bg-white/80 px-2 py-1' : 'mb-3 rounded-xl border border-white/15 bg-white/5 p-3'}><p className={`text-xs font-semibold ${compact ? 'text-ink' : 'text-white'}`} role="status" aria-live="polite" aria-atomic="true">Session {active.timedState}</p><p className={`mt-1 text-xs ${compact ? 'text-ink/70' : 'text-white/70'}`} aria-live="off">{formatDuration(duration, true)} · {active.activityType.replaceAll('_', ' ')}</p><Link className={`mt-2 inline-flex text-xs font-medium underline ${compact ? 'text-ink' : 'text-white'}`} to={paths.activity}>Open timer</Link></div>
}
