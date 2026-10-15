import { lazy } from 'react'

// The research pages and Optimize bring ECharts with them, so they load on first visit; the
// run pages are lazy too, so the Live view does not wait for the run form.
export const OverviewPage = lazy(() => import('@/features/overview/OverviewPage'))
export const ResearchPage = lazy(() => import('@/features/research/ResearchPage'))
export const BacktestPage = lazy(() => import('@/features/runs/BacktestPage'))
export const ComparePage = lazy(() => import('@/features/runs/ComparePage'))
export const OptimizePage = lazy(() => import('@/features/runs/OptimizePage'))
// the guide brings the markdown renderer
export const GuidePage = lazy(() => import('@/features/guide/GuidePage'))
