import { AlertTriangle, Clock3, Info, LoaderCircle, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

export function LoadingState({ label = 'Loading' }: { label?: string }) {
  return <div className="surface state-surface" role="status"><LoaderCircle className="size-5 animate-spin motion-reduce:animate-none" aria-hidden="true" />{label}</div>
}

export function PageSkeleton({ label = 'Loading page' }: { label?: string }) {
  return <div className="surface space-y-4 p-6" role="status" aria-label={label}><span className="sr-only">{label}</span><div className="skeleton h-8 w-1/2" /><div className="skeleton h-4 w-full" /><div className="skeleton h-4 w-4/5" /></div>
}

export function RefreshingNotice({ label = 'Refreshing' }: { label?: string }) {
  return <div className="inline-flex min-h-11 items-center gap-2 text-sm text-ink/60" role="status"><RefreshCw className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />{label}</div>
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="surface state-surface text-center" role="alert"><AlertTriangle className="size-6 text-status-critical" aria-hidden="true" /><p className="max-w-lg text-ink/70">{message}</p>{retry ? <button className="button-secondary" onClick={retry}>Try again</button> : null}</div>
}

export function SectionError({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="rounded-xl border border-status-critical/30 bg-status-critical/5 p-4" role="alert"><p className="font-semibold">This section could not be loaded.</p><p className="mt-1 text-sm text-ink/70">{message}</p>{retry ? <button className="button-secondary mt-3" onClick={retry}>Retry section</button> : null}</div>
}

export function EmptyState({ title, detail, action, className = '' }: { title: string; detail: string; action?: ReactNode; className?: string }) {
  return <div className={`surface state-surface text-center ${className}`}><Info className="size-5 text-status-info" aria-hidden="true" /><p className="font-display text-xl font-semibold">{title}</p><p className="max-w-xl text-sm leading-6 text-ink/65">{detail}</p>{action}</div>
}

export function StaleDataNotice({ children }: { children: ReactNode }) {
  return <div className="notice notice-warning" role="status"><Clock3 className="size-4 shrink-0" aria-hidden="true" />{children}</div>
}

export function ReadOnlyNotice({ children }: { children: ReactNode }) {
  return <div className="notice notice-legacy" role="note"><Info className="size-4 shrink-0" aria-hidden="true" />{children}</div>
}

export function MutationError({ children }: { children: ReactNode }) {
  return <div className="notice notice-critical" role="alert"><AlertTriangle className="size-4 shrink-0" aria-hidden="true" />{children}</div>
}
