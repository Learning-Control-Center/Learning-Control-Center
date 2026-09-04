import { AlertTriangle, LoaderCircle } from 'lucide-react'

export function LoadingState({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="surface flex min-h-48 items-center justify-center gap-3 p-8 text-ink/60" role="status">
      <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
      {label}
    </div>
  )
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="surface flex min-h-48 flex-col items-center justify-center gap-4 p-8 text-center" role="alert">
      <AlertTriangle className="size-6 text-copper" aria-hidden="true" />
      <p className="max-w-lg text-ink/70">{message}</p>
      {retry ? (
        <button className="button-secondary" onClick={retry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

export function EmptyState({ title, detail, action, className = '' }: { title: string; detail: string; action?: React.ReactNode; className?: string }) {
  return (
    <div className={`surface flex min-h-56 flex-col items-center justify-center gap-3 p-8 text-center ${className}`}>
      <p className="font-display text-xl font-semibold">{title}</p>
      <p className="max-w-xl text-sm leading-6 text-ink/65">{detail}</p>
      {action}
    </div>
  )
}
