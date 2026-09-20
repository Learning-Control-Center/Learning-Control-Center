import type { ReactNode } from 'react'

import { Surface } from './ProductPrimitives'

export type ProductReason = { code: string; title?: string; text: string; facts?: unknown }

export function ReasonList({ reasons }: { reasons: ProductReason[] }) {
  return reasons.length ? <ul className="space-y-2">{reasons.map((reason) => <li className="text-sm leading-6 text-ink/70" key={`${reason.code}:${reason.title ?? ''}`}><span className="font-medium text-ink">{reason.title ? `${reason.title}: ` : ''}</span>{reason.text}</li>)}</ul> : <p className="text-sm text-ink/65">No additional explanation was recorded.</p>
}

export function AuditDisclosure({ label = 'Audit details', children, open = false }: { label?: string; children: ReactNode; open?: boolean }) {
  return <details className="rounded-xl border border-ink/10 bg-white/50 p-3" open={open}><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-moss">{label}</summary><div className="mt-3 break-words text-xs leading-5 text-ink/65">{children}</div></details>
}

export function ProvenanceNotice({ title, children }: { title: string; children: ReactNode }) {
  return <Surface className="border-l-4 border-copper p-4"><p className="font-semibold">{title}</p><div className="mt-1 text-sm leading-6 text-ink/65">{children}</div></Surface>
}
