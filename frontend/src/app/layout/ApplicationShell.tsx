import {
  Activity,
  BarChart3,
  FileOutput,
  FolderKanban,
  LibraryBig,
  LogOut,
  Map,
  Menu,
  Settings,
  Sparkles,
  UserRoundCheck,
  X,
} from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../../auth'
import { Dialog } from '../../shared/components'
import { productMotion } from '../../shared/styles/motion'
import { RouteEffects } from '../router/RouteEffects'
import { paths } from '../router/routes'
import { ActiveSessionSlot } from './ActiveSessionSlot'
import { Breadcrumbs } from './Breadcrumbs'
import { InsightsSubnavigation } from './InsightsSubnavigation'
import { productNavigation, utilityNavigation } from './navigation'

const icons = {
  [paths.today]: Sparkles,
  [paths.roadmap]: Map,
  [paths.profile]: UserRoundCheck,
  [paths.learn]: LibraryBig,
  [paths.projects]: FolderKanban,
  [paths.activity]: Activity,
  [paths.insights]: BarChart3,
  [paths.dataTransfer]: FileOutput,
  [paths.settings]: Settings,
}

function NavigationLink({ to, label, close }: { to: string; label: string; close?: (navigating: boolean) => void }) {
  const Icon = icons[to as keyof typeof icons]
  return <NavLink to={to} end={to === '/'} onClick={() => close?.(true)} className={({ isActive }) => `navigation-link ${isActive ? 'navigation-link-active' : ''}`}><Icon className="size-[1.1rem]" aria-hidden="true" />{label}</NavLink>
}

function Navigation({ close }: { close?: (navigating: boolean) => void }) {
  const { session, logout } = useAuth()
  return <div className="flex h-full flex-col"><div className="border-b border-white/10 px-5 py-6"><div className="mb-3 flex items-center gap-3"><img src="/logo.png" alt="" className="size-11 rounded-xl bg-white object-contain" aria-hidden="true" /><div><p className="font-display text-sm font-semibold tracking-tight text-white">Learning Control Center</p><p className="font-mono text-[0.68rem] uppercase tracking-[0.17em] text-white/60">Private learning workspace</p></div></div><p className="text-sm leading-5 text-white/60">Competency, evidence, and deliberate practice.</p></div><div className="flex-1 overflow-y-auto px-3 py-4"><nav className="space-y-1" aria-label="Product navigation">{productNavigation.map((item) => <NavigationLink key={item.path} to={item.path} label={item.label} close={close} />)}</nav><div className="my-4 border-t border-white/10" /><nav className="space-y-1" aria-label="Utility navigation">{utilityNavigation.map((item) => <NavigationLink key={item.path} to={item.path} label={item.label} close={close} />)}</nav></div><div className="border-t border-white/10 p-4"><ActiveSessionSlot /><p className="mb-3 truncate px-2 text-xs text-white/60">Signed in as {session?.username}</p><button className="navigation-link w-full" onClick={() => void logout()}><LogOut className="size-4" aria-hidden="true" />Sign out</button></div></div>
}

export function ApplicationShell() {
  const reduceMotion = useReducedMotion()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [restoreMobileFocus, setRestoreMobileFocus] = useState(true)
  const closeNavigation = useCallback((navigating = false) => {
    setRestoreMobileFocus(!navigating)
    setMobileOpen(false)
  }, [])
  const dismissNavigation = useCallback(() => closeNavigation(false), [closeNavigation])
  const openNavigation = () => {
    setRestoreMobileFocus(true)
    setMobileOpen(true)
  }

  return <div className="min-h-screen lg:grid lg:grid-cols-[15.5rem_minmax(0,1fr)]"><div id="application-background" className="contents"><a className="skip-link" href="#main-content">Skip to main content</a><aside className="fixed inset-y-0 left-0 z-30 hidden w-[15.5rem] bg-ink lg:block"><Navigation /></aside><header className="sticky top-0 z-20 flex min-h-16 items-center gap-2 border-b border-ink/10 bg-parchment/95 px-4 backdrop-blur lg:hidden"><img src="/logo.png" alt="" className="size-9 rounded-lg bg-white object-contain" aria-hidden="true" /><p className="mr-auto truncate font-display font-semibold">Learning Control Center</p><ActiveSessionSlot compact /><button className="grid size-11 shrink-0 place-items-center rounded-xl border border-ink/15 bg-white/70" onClick={openNavigation} aria-label="Open navigation"><Menu className="size-5" aria-hidden="true" /></button></header><main id="main-content" tabIndex={-1} className="min-w-0 scroll-mt-20 lg:col-start-2"><RouteEffects /><motion.div className="page-container" data-reduced-motion={reduceMotion ? 'true' : 'false'} initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={reduceMotion ? productMotion.reduced : productMotion.enter}><Breadcrumbs /><InsightsSubnavigation /><Outlet /></motion.div></main></div><Dialog open={mobileOpen} label="Application navigation" onDismiss={dismissNavigation} restoreFocus={restoreMobileFocus} className="absolute inset-y-0 left-0 w-[min(19rem,86vw)] bg-ink pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] shadow-2xl"><button className="absolute right-3 top-[calc(0.75rem+env(safe-area-inset-top))] grid size-11 place-items-center rounded-xl text-white/75 hover:bg-white/10" onClick={dismissNavigation} aria-label="Close navigation"><X className="size-5" aria-hidden="true" /></button><Navigation close={closeNavigation} /></Dialog></div>
}
