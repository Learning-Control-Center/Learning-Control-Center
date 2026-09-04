import type { Status } from '../types'

const labels: Record<Status, string> = {
  not_started: 'Not started',
  learning: 'Learning',
  practicing: 'Practicing',
  ready_for_verification: 'Ready to verify',
  verified: 'Verified',
  needs_review: 'Needs review',
}

const styles: Record<Status, string> = {
  not_started: 'border-ink/15 bg-ink/5 text-ink/70',
  learning: 'border-sky-300 bg-sky-50 text-sky-800',
  practicing: 'border-amber-300 bg-amber-50 text-amber-800',
  ready_for_verification: 'border-violet-300 bg-violet-50 text-violet-800',
  verified: 'border-emerald-300 bg-emerald-50 text-emerald-800',
  needs_review: 'border-rose-300 bg-rose-50 text-rose-800',
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${styles[status]}`}>
      {labels[status]}
    </span>
  )
}
