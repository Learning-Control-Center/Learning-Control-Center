import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApplicationShell } from './ApplicationShell'

const logout = vi.hoisted(() => vi.fn())

vi.mock('../../auth', () => ({
  useAuth: () => ({
    session: { username: 'learner' },
    logout,
  }),
}))

vi.mock('../../authority', () => ({
  useAuthority: () => ({
    state: { canonicalLearningAuthority: 'v2' },
  }),
}))

function ShellHarness({ initialPath = '/roadmap' }: { initialPath?: string }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<ApplicationShell />}>
          <Route path="roadmap" element={<h1 data-route-focus tabIndex={-1}>Roadmap</h1>} />
          <Route path="projects" element={<h1 data-route-focus tabIndex={-1}>Projects</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

function FocusPage({ name }: { name: string }) {
  const navigate = useNavigate()
  return <><h1 data-route-focus tabIndex={-1}>{name}</h1><button onClick={() => navigate(-1)}>Back</button><button onClick={() => navigate('?filter=current', { replace: true })}>Change filter</button></>
}

function HistoryFocusHarness() {
  return (
    <MemoryRouter initialEntries={['/roadmap', '/projects']} initialIndex={1}>
      <Routes>
        <Route element={<ApplicationShell />}>
          <Route path="roadmap" element={<FocusPage name="Roadmap" />} />
          <Route path="projects" element={<FocusPage name="Projects" />} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

describe('application shell navigation and focus', () => {
  beforeEach(() => {
    logout.mockReset()
    document.title = ''
  })

  it('renders seven primary and two utility destinations with the current route marked', async () => {
    render(<ShellHarness />)

    expect(screen.getAllByText('Learning Control Center')).not.toHaveLength(0)
    expect(screen.queryByText(/Center \/ V2/)).not.toBeInTheDocument()
    expect(document.querySelectorAll('img[src="/logo.png"]')).not.toHaveLength(0)
    const productNavigation = screen.getByRole('navigation', { name: 'Product navigation' })
    expect(within(productNavigation).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Today',
      'Roadmap',
      'Profile',
      'Learn',
      'Projects',
      'Activity',
      'Insights',
    ])
    const roadmap = within(productNavigation).getByRole('link', { name: 'Roadmap' })
    expect(roadmap).toHaveAttribute('aria-current', 'page')

    const utilityNavigation = screen.getByRole('navigation', { name: 'Utility navigation' })
    expect(within(utilityNavigation).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Data transfer',
      'Settings',
    ])
    await waitFor(() => expect(document.title).toBe('Roadmap · Learning Control Center'))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Roadmap' })).toHaveFocus())
  })

  it('puts the skip link first and targets the stable focusable main landmark', () => {
    const { container } = render(<ShellHarness />)
    const skip = screen.getByRole('link', { name: 'Skip to main content' })
    expect(container.querySelector('a')).toBe(skip)
    expect(skip).toHaveAttribute('href', '#main-content')
    expect(document.getElementById('main-content')).toHaveAttribute('tabindex', '-1')
  })

  it('returns focus after Escape dismissal and clears background inert state', async () => {
    const user = userEvent.setup()
    render(<ShellHarness />)

    const opener = screen.getByRole('button', { name: 'Open navigation' })
    await user.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Application navigation' })
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Close navigation' })).toHaveFocus())
    expect(document.getElementById('application-background')).toHaveAttribute('inert')
    expect(document.getElementById('application-background')).toContainElement(
      screen.getByRole('link', { name: 'Skip to main content', hidden: true }),
    )

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Application navigation' })).not.toBeInTheDocument()
    expect(document.getElementById('application-background')).not.toHaveAttribute('inert')
    expect(opener).toHaveFocus()
  })

  it('closes on mobile navigation without restoring the menu trigger and focuses the destination heading', async () => {
    const user = userEvent.setup()
    render(<ShellHarness />)

    const opener = screen.getByRole('button', { name: 'Open navigation' })
    await user.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Application navigation' })
    await user.click(within(dialog).getByRole('link', { name: 'Projects' }))

    expect(screen.queryByRole('dialog', { name: 'Application navigation' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Projects' })).toHaveFocus())
    expect(opener).not.toHaveFocus()
    expect(document.title).toBe('Projects · Learning Control Center')
    expect(screen.getByRole('status')).toHaveTextContent('Projects page')
  })

  it('focuses the destination after Back and does not steal focus for same-route query changes', async () => {
    const user = userEvent.setup()
    render(<HistoryFocusHarness />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Projects' })).toHaveFocus())

    await user.click(screen.getByRole('button', { name: 'Change filter' }))
    expect(screen.getByRole('button', { name: 'Change filter' })).toHaveFocus()

    await user.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Roadmap' })).toHaveFocus())
    expect(screen.getByRole('status')).toHaveTextContent('Roadmap page')
  })
})
