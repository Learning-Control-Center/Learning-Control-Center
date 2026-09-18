import {
  Activity,
  BarChart3,
  BrainCircuit,
  BookOpenCheck,
  LibraryBig,
  ListChecks,
  FolderKanban,
  CalendarClock,
  FileOutput,
  GitBranch,
  LogOut,
  Map,
  Menu,
  Settings,
  Sparkles,
  X,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../auth'

const navigation = [
  { to: '/', label: 'Today', icon: Sparkles },
  { to: '/roadmap', label: 'Roadmap', icon: Map },
  { to: '/roadmap-v2', label: 'Roadmap V2 preview', icon: GitBranch },
  { to: '/curriculum', label: 'Curriculum', icon: LibraryBig },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/sessions', label: 'Log & sessions', icon: CalendarClock },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/analysis', label: 'Analysis V3', icon: BrainCircuit },
  { to: '/recommendations-v2', label: 'Recommendation V2', icon: ListChecks },
  { to: '/reports', label: 'Reports', icon: BookOpenCheck },
  { to: '/transfer', label: 'Import / Export', icon: FileOutput },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function Navigation({ close }: { close?: () => void }) {
  const { session, logout } = useAuth()
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-white/10 px-5 py-6">
        <div className="mb-3 flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-fern/20 text-fern">
            <Activity className="size-5" aria-hidden="true" />
          </span>
          <div>
            <p className="font-display text-sm font-semibold tracking-tight text-white">Learning Control</p>
            <p className="font-mono text-[0.68rem] uppercase tracking-[0.17em] text-white/60">Center / V1</p>
          </div>
        </div>
        <p className="text-sm leading-5 text-white/60">Competency, evidence, and deliberate practice.</p>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-5" aria-label="Primary navigation">
        {navigation.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            onClick={close}
            className={({ isActive }) =>
              `flex min-h-11 items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                isActive ? 'bg-white text-ink' : 'text-white/75 hover:bg-white/10 hover:text-white'
              }`
            }
          >
            <Icon className="size-[1.1rem]" aria-hidden="true" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-white/10 p-4">
        <p className="mb-3 truncate px-2 text-xs text-white/60">Signed in as {session?.username}</p>
        <button
          className="flex min-h-11 w-full items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium text-white/75 transition hover:bg-white/10 hover:text-white"
          onClick={() => void logout()}
        >
          <LogOut className="size-4" aria-hidden="true" />
          Sign out
        </button>
      </div>
    </div>
  )
}

export function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  useEffect(() => {
    if (!mobileOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [mobileOpen])
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[15.5rem_minmax(0,1fr)]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[15.5rem] bg-ink lg:block">
        <Navigation />
      </aside>
      <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-ink/10 bg-parchment/90 px-4 backdrop-blur lg:hidden">
        <p className="font-display font-semibold">Learning Control Center</p>
        <button
          className="grid size-11 place-items-center rounded-xl border border-ink/10"
          onClick={() => setMobileOpen(true)}
          aria-label="Open navigation"
        >
          <Menu className="size-5" />
        </button>
      </header>
      <AnimatePresence>
        {mobileOpen ? (
          <>
            <motion.button
              className="fixed inset-0 z-40 bg-ink/40 lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileOpen(false)}
              aria-label="Close navigation"
            />
            <motion.aside
              className="fixed inset-y-0 left-0 z-50 w-[min(19rem,86vw)] bg-ink lg:hidden"
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ duration: 0.2 }}
              role="dialog"
              aria-modal="true"
              aria-label="Application navigation"
            >
              <button
                className="absolute right-3 top-3 grid size-10 place-items-center rounded-xl text-white/70 hover:bg-white/10"
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation"
              >
                <X className="size-5" />
              </button>
              <Navigation close={() => setMobileOpen(false)} />
            </motion.aside>
          </>
        ) : null}
      </AnimatePresence>
      <main className="min-w-0 lg:col-start-2">
        <motion.div
          className="mx-auto w-full max-w-[144rem] px-4 py-6 sm:px-6 sm:py-8 xl:px-10"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.22 }}
        >
          <Outlet />
        </motion.div>
      </main>
    </div>
  )
}
