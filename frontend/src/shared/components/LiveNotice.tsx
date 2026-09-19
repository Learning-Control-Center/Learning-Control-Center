export function LiveNotice({ children, assertive = false }: { children: React.ReactNode; assertive?: boolean }) {
  return (
    <div className="sr-only" role={assertive ? 'alert' : 'status'} aria-live={assertive ? 'assertive' : 'polite'} aria-atomic="true">
      {children}
    </div>
  )
}
