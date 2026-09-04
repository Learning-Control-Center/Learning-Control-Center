import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AnalyticsPage } from './pages/AnalyticsPage'
import { ReportDocument } from './pages/ReportsPage'
import { RoadmapPage } from './pages/RoadmapPage'
import { TransferPage } from './pages/TransferPage'

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('frontend polish regressions', () => {
  it('gives an unconfigured roadmap a page header and direct import action', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ configured: false })))
    render(
      <MemoryRouter>
        <RoadmapPage />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Roadmap' })).toBeInTheDocument()
    expect(screen.getByText('Competency architecture')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Import roadmap' })).toHaveAttribute(
      'href',
      '/transfer',
    )
  })

  it('distinguishes empty analytics from a chart rendering failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json({
          analyticsVersion: 1,
          range: { name: '30d', startDate: '2026-08-06', endDate: '2026-09-04' },
          totalDurationMs: 0,
          activeDays: 0,
          weeklyTarget: { currentWeekActiveDays: 0, targetActiveDays: 5 },
          durationAdherence: [],
          distributions: { activity: [], assistance: [], track: [] },
          independentCoding: {
            independentDurationMs: 0,
            practicalDurationMs: 0,
            ratio: null,
          },
          regularity: { coefficientOfVariation: null, band: null },
          workloadTrend: { short: { differenceMs: 0, changeRatio: null } },
          gaps: {
            latestCompletedGapDays: null,
            longestCompletedGapDays: null,
            ongoingGapDays: null,
          },
          coverage: {
            verifiedCore: 0,
            applicableCore: 0,
            verifiedImportant: 0,
            applicableImportant: 0,
            weightedVerificationCoverage: null,
          },
          reviewDebt: { count: 0, weightedCount: 0, items: [] },
        }),
      ),
    )
    render(<AnalyticsPage />)

    expect(
      await screen.findByText('No qualifying learning sessions in this period.'),
    ).toBeInTheDocument()
    expect(screen.getByText('No activity data in this period.')).toBeInTheDocument()
    expect(screen.getByText('No assistance data in this period.')).toBeInTheDocument()
    expect(screen.getAllByText('N/A')).toHaveLength(3)
  })

  it('renders canonical report Markdown as safe document elements', () => {
    const { container } = render(
      <ReportDocument
        markdown={'# Daily report\n\nPeriod: 2026-09-01\n\n## Activity\n\n- Active days: 2\n- Total: 60m\n\n<img src=x onerror=alert(1)>'}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Activity' })).toBeInTheDocument()
    expect(screen.getByText('Active days: 2')).toBeInTheDocument()
    expect(screen.queryByText('# Daily report')).not.toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
  })

  it('keeps portable backup complete while other exports stay selective', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/history')) return json({ items: [] })
        if (url.includes('/roadmap/current')) return json({ configured: false })
        return json({})
      }),
    )
    render(
      <MemoryRouter>
        <TransferPage />
      </MemoryRouter>,
    )

    expect(await screen.findByLabelText('Date range')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Included categories' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Portable Logical Backup/ }))
    await waitFor(() => expect(screen.queryByLabelText('Date range')).not.toBeInTheDocument())
    expect(screen.queryByRole('group', { name: 'Included categories' })).not.toBeInTheDocument()
    expect(screen.getByText(/always include all supported learning state/i)).toBeInTheDocument()
  })
})
