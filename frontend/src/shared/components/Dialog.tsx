import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function Dialog({
  open,
  label,
  children,
  onDismiss,
  restoreFocus = true,
  className = '',
}: {
  open: boolean
  label: string
  children: ReactNode
  onDismiss: () => void
  restoreFocus?: boolean
  className?: string
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const invokerRef = useRef<HTMLElement | null>(null)
  const restoreFocusRef = useRef(restoreFocus)
  const dismissRef = useRef(onDismiss)
  restoreFocusRef.current = restoreFocus
  dismissRef.current = onDismiss

  useEffect(() => {
    if (!open) return
    invokerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const background = document.getElementById('application-background')
    background?.setAttribute('inert', '')
    document.body.classList.add('dialog-open')

    const frame = window.requestAnimationFrame(() => {
      const first = dialogRef.current?.querySelector<HTMLElement>(focusableSelector)
      ;(first ?? dialogRef.current)?.focus()
    })

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        dismissRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector))
      if (focusable.length === 0) {
        event.preventDefault()
        dialogRef.current.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKeyDown)
      background?.removeAttribute('inert')
      document.body.classList.remove('dialog-open')
      if (restoreFocusRef.current) {
        const invoker = invokerRef.current
        if (invoker?.isConnected) invoker.focus()
        else document.getElementById('main-content')?.focus({ preventScroll: true })
      }
    }
  }, [open])

  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50" role="presentation">
      <div className="absolute inset-0 bg-ink/45" onMouseDown={onDismiss} aria-hidden="true" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={className}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
