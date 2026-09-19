import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppRoutes } from './AppRoutes'

const authority = vi.hoisted(() => ({
  state: {
    canonicalLearningAuthority: 'v2',
    todayPresentation: 'v2',
    roadmapPresentation: 'v2',
    recommendationPresentation: 'v2',
  },
}))

vi.mock('../../authority', () => ({
  useAuthority: () => ({
    state: authority.state,
  }),
}))

vi.mock('../layout/ApplicationShell', () => ({
  ApplicationShell: () => {
    const location = useLocation()
    return <><output data-testid="location">{location.pathname}{location.search}{location.hash}</output><Outlet /></>
  },
}))

vi.mock('../../features/today', () => ({
  TodayPage: () => <h1>Legacy Today live</h1>,
  TodayV2Page: () => <h1>Today V2</h1>,
}))
vi.mock('../../features/today/legacy', () => ({ TodayPage: () => <h1>Legacy Today live</h1> }))
vi.mock('../../features/today/v2', () => ({ TodayV2Page: () => <h1>Today V2</h1> }))
vi.mock('../../features/roadmap', () => ({
  RoadmapPage: () => <h1>Legacy Roadmap live</h1>,
  RoadmapV2Page: () => <h1>Roadmap V2</h1>,
}))
vi.mock('../../features/roadmap/legacy', () => ({ RoadmapPage: () => <h1>Legacy Roadmap live</h1> }))
vi.mock('../../features/roadmap/v2', () => ({ RoadmapV2Page: () => <h1>Roadmap V2</h1> }))
vi.mock('../../features/profile', () => ({ ProfileCapabilityPage: () => <h1>Profile</h1> }))
vi.mock('../../features/learn', () => ({ CurriculumPage: () => <h1>Learn</h1> }))
vi.mock('../../features/projects', () => ({ ProjectsPage: () => <h1>Projects</h1> }))
vi.mock('../../features/activity', () => ({ SessionsPage: () => <h1>Activity</h1> }))
vi.mock('../../features/settings', () => ({ SettingsPage: () => <h1>Settings</h1> }))
vi.mock('../../features/data-transfer', () => ({ TransferPage: () => <h1>Data transfer</h1> }))
vi.mock('../../features/insights', () => ({
  InsightsIndexPage: () => <h1>Insights</h1>,
  AnalysisPage: () => <h1>Current analysis</h1>,
  RecommendationsV2Page: () => <h1>Recommendation history</h1>,
  LegacyIndexPage: () => <h1>Legacy V1 history</h1>,
}))
vi.mock('../../features/insights/landing', () => ({
  InsightsIndexPage: () => <h1>Insights</h1>,
  LegacyIndexPage: () => <h1>Legacy V1 history</h1>,
}))
vi.mock('../../features/insights/analysis', () => ({ AnalysisPage: () => <h1>Current analysis</h1> }))
vi.mock('../../features/insights/recommendations', () => ({ RecommendationsV2Page: () => <h1>Recommendation history</h1> }))
vi.mock('../../features/legacy-history', () => ({
  LegacyTodayHistoryPage: () => <h1>Legacy Today history</h1>,
  LegacyRoadmapHistoryPage: () => <h1>Legacy Roadmap history</h1>,
  AnalyticsPage: () => <h1>V1 compatibility analytics</h1>,
  ReportsPage: () => <h1>Generated reports</h1>,
}))
vi.mock('../../features/legacy-history/today', () => ({ LegacyTodayHistoryPage: () => <h1>Legacy Today history</h1> }))
vi.mock('../../features/legacy-history/roadmap', () => ({ LegacyRoadmapHistoryPage: () => <h1>Legacy Roadmap history</h1> }))
vi.mock('../../features/legacy-history/analytics', () => ({ AnalyticsPage: () => <h1>V1 compatibility analytics</h1> }))
vi.mock('../../features/legacy-history/reports', () => ({ ReportsPage: () => <h1>Generated reports</h1> }))

const compatibilityCases = [
  ['/sessions', '/activity', 'Activity'],
  ['/curriculum', '/learn', 'Learn'],
  ['/analysis', '/insights/analysis', 'Current analysis'],
  ['/analytics', '/insights/legacy/analytics', 'V1 compatibility analytics'],
  ['/recommendations', '/insights/recommendations', 'Recommendation history'],
  ['/recommendations-v2', '/insights/recommendations', 'Recommendation history'],
  ['/reports', '/insights/legacy/reports', 'Generated reports'],
  ['/transfer', '/data-transfer', 'Data transfer'],
  ['/today-v2', '/', 'Today V2'],
  ['/roadmap-v2', '/roadmap', 'Roadmap V2'],
  ['/legacy-today', '/insights/legacy/today', 'Legacy Today history'],
  ['/legacy-roadmap', '/insights/legacy/roadmap', 'Legacy Roadmap history'],
] as const

afterEach(() => {
  vi.unstubAllGlobals()
  authority.state = {
    canonicalLearningAuthority: 'v2',
    todayPresentation: 'v2',
    roadmapPresentation: 'v2',
    recommendationPresentation: 'v2',
  }
})

describe('compatibility aliases', () => {
  it.each(compatibilityCases)(
    'redirects %s to %s while preserving safe query and hash state',
    async (source, destination, heading) => {
      const fetchMock = vi.fn()
      vi.stubGlobal('fetch', fetchMock)
      render(
        <MemoryRouter initialEntries={[`${source}?target=backend%20engineer#evidence`]}>
          <AppRoutes />
        </MemoryRouter>,
      )

      expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(
        `${destination}?target=backend%20engineer#evidence`,
      ))
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )

  it('drops oversized query and hash input rather than forwarding it', async () => {
    render(
      <MemoryRouter initialEntries={[`/sessions?${'q'.repeat(2049)}#${'h'.repeat(513)}`]}>
        <AppRoutes />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Activity' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/activity')
    expect(screen.getByTestId('location').textContent).toBe('/activity')
  })

  it.each([
    ['/', 'legacy_v1', 'legacy_v1', 'legacy_v1', 'legacy_v1', 'Legacy Today live'],
    ['/', 'v2', 'v1_read_only', 'v2', 'v2', 'Legacy Today history'],
    ['/roadmap', 'legacy_v1', 'legacy_v1', 'legacy_v1', 'legacy_v1', 'Legacy Roadmap live'],
    ['/roadmap', 'v2', 'v2', 'v1_read_only', 'v2', 'Legacy Roadmap history'],
    ['/insights/recommendations', 'legacy_v1', 'legacy_v1', 'legacy_v1', 'legacy_v1', 'Legacy Today history'],
    ['/insights/recommendations', 'v2', 'v2', 'v2', 'v1_read_only', 'Legacy Today history'],
  ] as const)(
    'renders the authority-selected destination at %s for %s authority',
    async (path, canonicalLearningAuthority, todayPresentation, roadmapPresentation, recommendationPresentation, expectedHeading) => {
      authority.state = {
        canonicalLearningAuthority,
        todayPresentation,
        roadmapPresentation,
        recommendationPresentation,
      }
      render(
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>,
      )
      expect(await screen.findByRole('heading', { name: expectedHeading })).toBeInTheDocument()
    },
  )
})
