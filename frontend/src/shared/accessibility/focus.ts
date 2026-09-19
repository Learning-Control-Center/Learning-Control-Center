export function focusElement(target: HTMLElement | null, options: { scroll?: boolean } = {}) {
  if (!target) return false
  if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1')
  target.focus({ preventScroll: true })
  if (options.scroll !== false) target.scrollIntoView?.({ block: 'nearest' })
  return true
}
