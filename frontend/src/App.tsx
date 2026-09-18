import { Navigate, Route, Routes } from 'react-router-dom'
import { lazy, Suspense } from 'react'

import { useAuth } from './auth'
import { Layout } from './components/Layout'
import { LoadingState } from './components/PageState'
import { LoginPage } from './pages/LoginPage'

const TodayPage = lazy(() => import('./pages/TodayPage').then((module) => ({ default: module.TodayPage })))
const RoadmapPage = lazy(() => import('./pages/RoadmapPage').then((module) => ({ default: module.RoadmapPage })))
const RoadmapV2Page = lazy(() => import('./pages/RoadmapV2Page').then((module) => ({ default: module.RoadmapV2Page })))
const SessionsPage = lazy(() => import('./pages/SessionsPage').then((module) => ({ default: module.SessionsPage })))
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage').then((module) => ({ default: module.AnalyticsPage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const TransferPage = lazy(() => import('./pages/TransferPage').then((module) => ({ default: module.TransferPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const CurriculumPage = lazy(() => import('./pages/CurriculumPage').then((module) => ({ default: module.CurriculumPage })))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })))
const AnalysisPage = lazy(() => import('./pages/AnalysisPage').then((module) => ({ default: module.AnalysisPage })))
const RecommendationsV2Page = lazy(() => import('./pages/RecommendationsV2Page').then((module) => ({ default: module.RecommendationsV2Page })))

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
    <Suspense fallback={<main className="p-6"><LoadingState label="Opening section" /></main>}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<TodayPage />} />
          <Route path="roadmap" element={<RoadmapPage />} />
          <Route path="roadmap-v2" element={<RoadmapV2Page />} />
          <Route path="curriculum" element={<CurriculumPage />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="analysis" element={<AnalysisPage />} />
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
