import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AnalyticsPage } from './pages/AnalyticsPage'
import { ReportsPage } from './pages/ReportsPage'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const analyticsPayload = (name: string) => ({
  analyticsVersion: 1,
  generatedAt: Date.parse('2026-09-19T10:00:00Z'),
  timezone: 'UTC',
  historicalContext: null,
  range: { name, startDate: '2026-09-01', endDate: '2026-09-19' },
  totalDurationMs: 3_600_000,
  activeDays: 1,
  weeklyTarget: { currentWeekActiveDays: 1, targetActiveDays: 4 },
  durationAdherence: [{ localDate: '2026-09-19', durationMs: 3_600_000, adherence: 0.5 }],
  distributions: { activity: [{ key: name, durationMs: 3_600_000, ratio: 1 }], assistance: [], track: [] },
  independentCoding: { independentDurationMs: 3_600_000, practicalDurationMs: 3_600_000, ratio: 1 },
  regularity: { coefficientOfVariation: null, band: null },
  workloadTrend: { short: { differenceMs: 0, changeRatio: null } },
  gaps: { latestCompletedGapDays: null, longestCompletedGapDays: null, ongoingGapDays: null },
  coverage: { verifiedCore: 0, applicableCore: 0, verifiedImportant: 0, applicableImportant: 0, weightedVerificationCoverage: null },
  reviewDebt: { count: 0, weightedCount: 0, items: [] },
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('labeled V1 compatibility Insights', () => {
  it('uses one exact-value model for recalculated Analytics charts and table', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/api/v1/analytics?range=')) return json({ analyticsVersion: 1, range: { name: '30d', startDate: '2026-08-21', endDate: '2026-09-19' }, totalDurationMs: 3_600_000, activeDays: 1, weeklyTarget: { currentWeekActiveDays: 1, targetActiveDays: 4 }, durationAdherence: [{ localDate: '2026-09-19', durationMs: 3_600_000, adherence: 0.5 }], distributions: { activity: [{ key: 'coding', durationMs: 3_600_000, ratio: 1 }], assistance: [{ key: 'none', durationMs: 3_600_000, ratio: 1 }], track: [] }, independentCoding: { independentDurationMs: 3_600_000, practicalDurationMs: 3_600_000, ratio: 1 }, regularity: { coefficientOfVariation: null, band: null }, workloadTrend: { short: { differenceMs: 0, changeRatio: null } }, gaps: { latestCompletedGapDays: null, longestCompletedGapDays: null, ongoingGapDays: null }, coverage: { verifiedCore: 0, applicableCore: 0, verifiedImportant: 0, applicableImportant: 0, weightedVerificationCoverage: null }, reviewDebt: { count: 0, weightedCount: 0, items: [] } })
      throw new Error(`Unexpected URL ${String(input)}`)
    }))
    render(<AnalyticsPage />)
    expect(await screen.findByRole('heading', { name: 'V1 Compatibility Analytics' })).toBeInTheDocument()
    expect(screen.getByText(/Recalculated on request/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '30d' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('table', { name: /Exact V1 compatibility Analytics values/ })).toHaveTextContent('2026-09-19')
    expect(screen.getByRole('table', { name: /Exact V1 compatibility Analytics values/ })).toHaveTextContent('1h 0m')
  })

  it('suppresses a stale range response and keeps exact as-of provenance aligned', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    let resolveSevenDays: ((response: Response) => void) | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('range=7d')) return new Promise<Response>((resolve) => { resolveSevenDays = resolve })
      if (url.includes('range=90d')) return json(analyticsPayload('90d'))
      return json(analyticsPayload('30d'))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<AnalyticsPage />)
    expect(await screen.findByText(/Recalculated 9\/19\/2026, 10:00:00 AM in UTC/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '7d' }))
    await userEvent.click(screen.getByRole('button', { name: '90d' }))
    expect(await screen.findByRole('button', { name: '90d' })).toHaveAttribute('aria-pressed', 'true')
    resolveSevenDays?.(json(analyticsPayload('7d')))
    await Promise.resolve()
    expect(screen.getAllByText('90d')).not.toHaveLength(0)
    expect(screen.getByRole('button', { name: '90d' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('retains the last exact values when a range refresh fails', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).includes('range=7d')
      ? json({ error: { code: 'ANALYTICS_UNAVAILABLE', message: 'The selected range is unavailable.' } }, 503)
      : json(analyticsPayload('30d'))))
    render(<AnalyticsPage />)
    const table = await screen.findByRole('table', { name: /Exact V1 compatibility Analytics values/ })
    expect(table).toHaveTextContent('1h 0m')
    await userEvent.click(screen.getByRole('button', { name: '7d' }))
    expect(await screen.findByText('The selected range is unavailable.')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: /Exact V1 compatibility Analytics values/ })).toHaveTextContent('1h 0m')
  })

  it('keeps generated Reports immutable and sends current reflection editing to Today', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/api/v1/reports?limit=100')) return json({ items: [{ id: 'report-1', type: 'daily', periodStart: '2026-09-13', periodEnd: '2026-09-13', generatedAt: '2026-09-14T00:00:00Z', analyticsVersion: 1, payload: { totalDurationMs: 3_600_000, activeDays: 1, signals: [] }, markdown: '## Summary\nPreserved result.' }, { id: 'report-2', type: 'weekly', periodStart: '2026-09-07', periodEnd: '2026-09-13', generatedAt: '2026-09-14T00:00:00Z', analyticsVersion: 1, payload: { totalDurationMs: 3_600_000, activeDays: 2, signals: [] }, markdown: '## Weekly summary\nPreserved result.' }] })
      throw new Error(`Unexpected URL ${String(input)}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><ReportsPage /></MemoryRouter>)
    expect(await screen.findByRole('heading', { name: 'Generated V1 Reports' })).toBeInTheDocument()
    expect(screen.getByText(/Immutable snapshots/)).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByText(/Generated 9\/14\/2026/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open reflection for 2026-09-13/ })).toHaveAttribute('href', '/?reflectionDate=2026-09-13#daily-reflection')
    await userEvent.click(screen.getByRole('button', { name: /weekly report/i }))
    expect(screen.queryByRole('link', { name: /Open reflection/ })).not.toBeInTheDocument()
    const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>
    expect(calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })
})
