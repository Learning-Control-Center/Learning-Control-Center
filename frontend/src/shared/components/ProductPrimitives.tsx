import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'

function classes(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

export function Surface({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={classes('surface', className)} {...props} />
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 max-w-3xl">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1 className="page-title" data-route-focus tabIndex={-1}>{title}</h1>
        {description ? <p className="mt-3 text-sm leading-6 text-ink/65 sm:text-base">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  )
}

export function SectionHeader({ headingId, title, description, actions }: { headingId?: string; title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h2 id={headingId} className="font-display text-xl font-semibold tracking-tight sm:text-2xl">{title}</h2>
        {description ? <p className="mt-1 max-w-3xl text-sm leading-6 text-ink/65">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}

export function Button({ variant = 'primary', className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'quiet' }) {
  return <button className={classes(variant === 'primary' ? 'button-primary' : variant === 'secondary' ? 'button-secondary' : 'button-quiet', className)} {...props} />
}

export function StatusBadge({ label, tone = 'neutral' }: { label: string; tone?: 'neutral' | 'info' | 'success' | 'warning' | 'critical' | 'today' | 'unknown' | 'legacy' }) {
  return <span className={`status-badge status-badge-${tone}`}>{label}</span>
}

export function UnknownValue({ reason }: { reason?: string | null }) {
  return <span className="inline-flex items-center gap-1 font-medium text-status-unknown">Unknown{reason ? ` — ${reason}` : ''}</span>
}

export function LegacySourceBadge({ source = 'V1 history' }: { source?: string }) {
  return <StatusBadge label={source} tone="legacy" />
}
