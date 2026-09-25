import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuthority } from '../../authority'
import { ActiveSessionProvider } from '../../shared/session/ActiveSessionProvider'
import { ApplicationShell } from '../layout/ApplicationShell'
import { PageSkeleton } from '../../shared/components'
import { resolveAuthorityDestination, type AuthoritySurface } from './authorityResolver'
import { CompatibilityRedirect } from './CompatibilityRedirect'
import { compatibilityAliases, paths } from './routes'

const TodayLegacy = lazy(() => import('../../features/today/legacy').then((module) => ({ default: module.TodayPage })))
const TodayV2 = lazy(() => import('../../features/today/v2').then((module) => ({ default: module.TodayV2Page })))
const RoadmapLegacy = lazy(() => import('../../features/roadmap/legacy').then((module) => ({ default: module.RoadmapPage })))
const RoadmapV2 = lazy(() => import('../../features/roadmap/v2').then((module) => ({ default: module.RoadmapV2Page })))
const Profile = lazy(() => import('../../features/profile').then((module) => ({ default: module.ProfileCapabilityPage })))
const Learn = lazy(() => import('../../features/learn').then((module) => ({ default: module.CurriculumPage })))
const Projects = lazy(() => import('../../features/projects').then((module) => ({ default: module.ProjectsPage })))
const Activity = lazy(() => import('../../features/activity').then((module) => ({ default: module.SessionsPage })))
const AssessmentExecution = lazy(() => import('../../features/assessment').then((module) => ({ default: module.AssessmentExecutionPage })))
const Settings = lazy(() => import('../../features/settings').then((module) => ({ default: module.SettingsPage })))
const DataTransfer = lazy(() => import('../../features/data-transfer').then((module) => ({ default: module.TransferPage })))
const InsightsIndex = lazy(() => import('../../features/insights/landing').then((module) => ({ default: module.InsightsIndexPage })))
const Analysis = lazy(() => import('../../features/insights/analysis').then((module) => ({ default: module.AnalysisPage })))
const RecommendationsV2 = lazy(() => import('../../features/insights/recommendations').then((module) => ({ default: module.RecommendationsV2Page })))
const LegacyIndex = lazy(() => import('../../features/insights/landing').then((module) => ({ default: module.LegacyIndexPage })))
const LegacyToday = lazy(() => import('../../features/legacy-history/today').then((module) => ({ default: module.LegacyTodayHistoryPage })))
const LegacyRoadmap = lazy(() => import('../../features/legacy-history/roadmap').then((module) => ({ default: module.LegacyRoadmapHistoryPage })))
const LegacyAnalytics = lazy(() => import('../../features/legacy-history/analytics').then((module) => ({ default: module.AnalyticsPage })))
const LegacyReports = lazy(() => import('../../features/legacy-history/reports').then((module) => ({ default: module.ReportsPage })))

function LazySurface({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageSkeleton label="Opening section" />}>{children}</Suspense>
}

function AuthoritySurface({ surface }: { surface: AuthoritySurface }) {
  const { state } = useAuthority()
  if (!state) return null
  const destination = resolveAuthorityDestination(state, surface)
  if (surface === 'today') return <LazySurface>{destination === 'v2' ? <TodayV2 /> : destination === 'legacy_live' ? <TodayLegacy /> : <LegacyToday />}</LazySurface>
  if (surface === 'roadmap') return <LazySurface>{destination === 'v2' ? <RoadmapV2 /> : destination === 'legacy_live' ? <RoadmapLegacy /> : <LegacyRoadmap />}</LazySurface>
  // V1 does not have a separate recommendation-history page. Its immutable
  // recommendation records are presented by the labeled Today history surface;
  // the live Today workflow remains owned exclusively by the canonical `/` route.
  return <LazySurface>{destination === 'v2' ? <RecommendationsV2 /> : <LegacyToday />}</LazySurface>
}

export function AppRoutes() {
  return <Routes><Route element={<ActiveSessionProvider><ApplicationShell /></ActiveSessionProvider>}><Route index element={<AuthoritySurface surface="today" />} /><Route path="roadmap" element={<AuthoritySurface surface="roadmap" />} /><Route path="profile" element={<LazySurface><Profile /></LazySurface>} /><Route path="profile/competencies/:competencyIdentityId" element={<LazySurface><Profile /></LazySurface>} /><Route path="learn" element={<LazySurface><Learn /></LazySurface>} /><Route path="learn/curricula/:curriculumId" element={<LazySurface><Learn /></LazySurface>} /><Route path="projects" element={<LazySurface><Projects /></LazySurface>} /><Route path="projects/:projectId" element={<LazySurface><Projects /></LazySurface>} /><Route path="activity" element={<LazySurface><Activity /></LazySurface>} /><Route path="assessments/:executionId" element={<LazySurface><AssessmentExecution /></LazySurface>} /><Route path="insights" element={<LazySurface><InsightsIndex /></LazySurface>} /><Route path="insights/analysis" element={<LazySurface><Analysis /></LazySurface>} /><Route path="insights/recommendations" element={<AuthoritySurface surface="recommendation" />} /><Route path="insights/legacy" element={<LazySurface><LegacyIndex /></LazySurface>} /><Route path="insights/legacy/today" element={<LazySurface><LegacyToday /></LazySurface>} /><Route path="insights/legacy/roadmap" element={<LazySurface><LegacyRoadmap /></LazySurface>} /><Route path="insights/legacy/analytics" element={<LazySurface><LegacyAnalytics /></LazySurface>} /><Route path="insights/legacy/reports" element={<LazySurface><LegacyReports /></LazySurface>} /><Route path="data-transfer" element={<LazySurface><DataTransfer /></LazySurface>} /><Route path="settings" element={<LazySurface><Settings /></LazySurface>} /><Route path="*" element={<Navigate to={paths.today} replace />} /></Route>{compatibilityAliases.map((alias) => <Route key={alias.from} path={alias.from.slice(1)} element={<CompatibilityRedirect to={alias.to} />} />)}</Routes>
}
