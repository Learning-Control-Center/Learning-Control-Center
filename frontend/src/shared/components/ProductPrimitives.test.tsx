import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  Button,
  EmptyState,
  ErrorState,
  LegacySourceBadge,
  LiveNotice,
  LoadingState,
  MutationError,
  PageHeader,
  PageSkeleton,
  ReadOnlyNotice,
  RefreshingNotice,
  SectionError,
  StaleDataNotice,
  StatusBadge,
  Surface,
  UnknownValue,
} from './index'

describe('shared product primitives', () => {
  it('provides semantic headings, surfaces, buttons, badges, and Unknown values', () => {
    const { container } = render(
      <>
        <PageHeader eyebrow="Current work" title="Profile" description="Capability and evidence." />
        <Surface aria-label="Summary"><Button>Continue</Button></Surface>
        <StatusBadge label="Current" tone="success" />
        <UnknownValue reason="No evidence is available" />
        <LegacySourceBadge />
      </>,
    )

    const heading = screen.getByRole('heading', { level: 1, name: 'Profile' })
    expect(heading).toHaveAttribute('data-route-focus')
    expect(heading).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('region', { name: 'Summary' })).toHaveClass('surface')
    expect(screen.getByRole('button', { name: 'Continue' })).toHaveClass('button-primary')
    expect(screen.getByText('Current')).toHaveClass('status-badge-success')
    expect(screen.getByText('Unknown — No evidence is available')).toBeInTheDocument()
    expect(screen.getByText('V1 history')).toHaveClass('status-badge-legacy')
    expect(container.querySelector('.panel')).toBeNull()
  })

  it('exposes loading, refreshing, empty, stale, read-only, and mutation states accessibly', () => {
    render(
      <>
        <LoadingState label="Loading evidence" />
        <PageSkeleton label="Opening roadmap" />
        <RefreshingNotice label="Refreshing capability" />
        <EmptyState title="No projects" detail="Create a project when outcome work is ready." />
        <StaleDataNotice>Showing the last successful response.</StaleDataNotice>
        <ReadOnlyNotice>This record is immutable V1 history.</ReadOnlyNotice>
        <MutationError>Your update was not saved.</MutationError>
        <LiveNotice>Profile page</LiveNotice>
      </>,
    )

    expect(screen.getByText('Loading evidence').closest('[role="status"]')).toBeInTheDocument()
    expect(screen.getByLabelText('Opening roadmap')).toHaveAttribute('role', 'status')
    expect(screen.getByText('Refreshing capability').closest('[role="status"]')).toBeInTheDocument()
    expect(screen.getByText('No projects')).toBeInTheDocument()
    expect(screen.getByText('Showing the last successful response.').closest('[role="status"]')).toBeInTheDocument()
    expect(screen.getByText('This record is immutable V1 history.').closest('[role="note"]')).toBeInTheDocument()
    expect(screen.getByText('Your update was not saved.').closest('[role="alert"]')).toBeInTheDocument()
    expect(screen.getByText('Profile page')).toHaveAttribute('aria-live', 'polite')
  })

  it('keeps page and section failures distinct and retries only through their explicit action', async () => {
    const retryPage = vi.fn()
    const retrySection = vi.fn()
    const user = userEvent.setup()
    render(
      <>
        <ErrorState message="The page could not be loaded." retry={retryPage} />
        <SectionError message="Evidence is temporarily unavailable." retry={retrySection} />
      </>,
    )

    expect(screen.getAllByRole('alert')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retryPage).toHaveBeenCalledOnce()
    expect(retrySection).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Retry section' }))
    expect(retrySection).toHaveBeenCalledOnce()
  })
})
