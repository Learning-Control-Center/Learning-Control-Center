import { Navigate, Route, Routes } from 'react-router-dom'
import { lazy, Suspense } from 'react'

import { useAuth } from './auth'
import { AuthorityProvider, useAuthority } from './authority'
import { Layout } from './components/Layout'
import { ErrorState, LoadingState } from './components/PageState'
import { LoginPage } from './pages/LoginPage'

const TodayPage = lazy(() => import('./pages/TodayPage').then((module) => ({ default: module.TodayPage })))
const TodayV2Page = lazy(() => import('./pages/TodayV2Page').then((module) => ({ default: module.TodayV2Page })))
const RoadmapPage = lazy(() => import('./pages/RoadmapPage').then((module) => ({ default: module.RoadmapPage })))
const RoadmapV2Page = lazy(() => import('./pages/RoadmapV2Page').then((module) => ({ default: module.RoadmapV2Page })))
const SessionsPage = lazy(() => import('./pages/SessionsPage').then((module) => ({ default: module.SessionsPage })))
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage').then((module) => ({ default: module.AnalyticsPage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const TransferPage = lazy(() => import('./pages/TransferPage').then((module) => ({ default: module.TransferPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const CurriculumPage = lazy(() => import('./pages/CurriculumPage').then((module) => ({ default: module.CurriculumPage })))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })))
const ProfileCapabilityPage = lazy(() => import('./pages/ProfileCapabilityPage').then((module) => ({ default: module.ProfileCapabilityPage })))
const AnalysisPage = lazy(() => import('./pages/AnalysisPage').then((module) => ({ default: module.AnalysisPage })))
const RecommendationsV2Page = lazy(() => import('./pages/RecommendationsV2Page').then((module) => ({ default: module.RecommendationsV2Page })))
const LegacyTodayHistoryPage = lazy(() => import('./pages/LegacyTodayHistoryPage').then((module) => ({ default: module.LegacyTodayHistoryPage })))
const LegacyRoadmapHistoryPage = lazy(() => import('./pages/LegacyRoadmapHistoryPage').then((module) => ({ default: module.LegacyRoadmapHistoryPage })))

function DefaultTodayPage() {
  const { state } = useAuthority()
  return state?.todayPresentation === 'v2' ? <TodayV2Page /> : state?.canonicalLearningAuthority === 'v2' ? <LegacyTodayHistoryPage /> : <TodayPage />
}

function DefaultRoadmapPage() {
  const { state } = useAuthority()
  return state?.roadmapPresentation === 'v2' ? <RoadmapV2Page /> : state?.canonicalLearningAuthority === 'v2' ? <LegacyRoadmapHistoryPage /> : <RoadmapPage />
}

function DefaultRecommendationPage() {
  const { state } = useAuthority()
  return state?.recommendationPresentation === 'v2' ? <RecommendationsV2Page /> : <LegacyTodayHistoryPage />
}

function AuthorizedApplication() {
  const { state, loading, error } = useAuthority()
  if (loading) return <main className="grid min-h-screen place-items-center p-5"><div className="w-full max-w-xl"><LoadingState label="Loading learning authority" /></div></main>
  if (error || !state) return <main className="grid min-h-screen place-items-center p-5"><div className="w-full max-w-xl"><ErrorState message={error || 'Learning authority is unavailable.'} /></div></main>
  return (
    <Suspense fallback={<main className="p-6"><LoadingState label="Opening section" /></main>}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<DefaultTodayPage />} />
          <Route path="today-v2" element={<TodayV2Page />} />
          <Route path="legacy-today" element={<LegacyTodayHistoryPage />} />
          <Route path="roadmap" element={<DefaultRoadmapPage />} />
          <Route path="roadmap-v2" element={<RoadmapV2Page />} />
          <Route path="legacy-roadmap" element={<LegacyRoadmapHistoryPage />} />
          <Route path="curriculum" element={<CurriculumPage />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="profile" element={<ProfileCapabilityPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="analysis" element={<AnalysisPage />} />
          <Route path="recommendations" element={<DefaultRecommendationPage />} />
          <Route path="recommendations-v2" element={<RecommendationsV2Page />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="transfer" element={<TransferPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

export function App() {
  const { session, loading } = useAuth()
  if (loading) {
    return (
      <main className="grid min-h-screen place-items-center p-5">
        <div className="w-full max-w-xl"><LoadingState label="Opening your private learning workspace" /></div>
      </main>
    )
  }
  if (!session) return <LoginPage />
  return (
    <AuthorityProvider><AuthorizedApplication /></AuthorityProvider>
  )
}
