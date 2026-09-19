import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Dialog } from './Dialog'

function DialogHarness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <div id="application-background"><button>Background action</button></div>
      <button onClick={() => setOpen(true)}>Open details</button>
      <Dialog open={open} label="Details" onDismiss={() => setOpen(false)}>
        <button>First action</button>
        <a href="#last">Last action</a>
      </Dialog>
    </>
  )
}

function RemovedInvokerHarness() {
  const [open, setOpen] = useState(false)
  const [showInvoker, setShowInvoker] = useState(true)
  return (
    <>
      <main id="main-content" tabIndex={-1}>Fallback content</main>
      {showInvoker ? <button onClick={() => setOpen(true)}>Open transient dialog</button> : null}
      <Dialog open={open} label="Transient" onDismiss={() => setOpen(false)}>
        <button onClick={() => setShowInvoker(false)}>Remove opener</button>
        <button onClick={() => setOpen(false)}>Close transient dialog</button>
      </Dialog>
    </>
  )
}

function RerenderingDialogHarness() {
  const [count, setCount] = useState(0)
  return (
    <>
      <div id="application-background" />
      <Dialog open label="Rerendering" onDismiss={() => undefined}>
        <button>First action</button>
        <button onClick={() => setCount((value) => value + 1)}>Update content {count}</button>
      </Dialog>
    </>
  )
}

describe('focus-managed dialog', () => {
  it('focuses the first control, makes the background inert, traps Tab, and restores the invoker on Escape', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)

    const invoker = screen.getByRole('button', { name: 'Open details' })
    await user.click(invoker)
    const dialog = screen.getByRole('dialog', { name: 'Details' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'First action' })).toHaveFocus())
    expect(document.getElementById('application-background')).toHaveAttribute('inert')

    await user.tab({ shift: true })
    expect(screen.getByRole('link', { name: 'Last action' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'First action' })).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(dialog).not.toBeInTheDocument()
    expect(document.getElementById('application-background')).not.toHaveAttribute('inert')
    expect(invoker).toHaveFocus()
  })

  it('dismisses from the backdrop and invokes the supplied callback once', async () => {
    const onDismiss = vi.fn()
    render(
      <>
        <div id="application-background" />
        <Dialog open label="Navigation" onDismiss={onDismiss}><button>Inside</button></Dialog>
      </>,
    )
    const backdrop = screen.getByRole('presentation').firstElementChild
    expect(backdrop).toHaveAttribute('aria-hidden', 'true')
    fireEvent.mouseDown(backdrop!)
    expect(onDismiss).toHaveBeenCalledOnce()
  })

  it('falls back to the main landmark when the invoking control no longer exists', async () => {
    const user = userEvent.setup()
    render(<RemovedInvokerHarness />)
    await user.click(screen.getByRole('button', { name: 'Open transient dialog' }))
    await user.click(screen.getByRole('button', { name: 'Remove opener' }))
    await user.click(screen.getByRole('button', { name: 'Close transient dialog' }))
    await waitFor(() => expect(document.getElementById('main-content')).toHaveFocus())
  })

  it('does not restart focus and inert management when an open dialog rerenders', async () => {
    const user = userEvent.setup()
    render(<RerenderingDialogHarness />)
    const update = await screen.findByRole('button', { name: 'Update content 0' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'First action' })).toHaveFocus())
    await user.click(update)
    expect(screen.getByRole('button', { name: 'Update content 1' })).toHaveFocus()
    expect(document.getElementById('application-background')).toHaveAttribute('inert')
  })
})
