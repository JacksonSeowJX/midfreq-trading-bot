import { lazy } from 'react'

// The research pages bring ECharts with them, so they load on first visit.
export const OverviewPage = lazy(() => import('@/features/overview/OverviewPage'))
export const ResearchPage = lazy(() => import('@/features/research/ResearchPage'))
