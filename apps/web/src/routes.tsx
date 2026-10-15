import { Navigate, type RouteObject } from 'react-router'
import { AppShell } from '@/components/layout/AppShell'
import type { RouteHandle } from '@/components/layout/TopBar'
import LivePage from '@/features/live/LivePage'
import { NotFoundPage } from '@/features/placeholders/NotFoundPage'
import { BacktestPage, ComparePage, OptimizePage, OverviewPage, ResearchPage } from '@/lazyPages'

const handle = (title: string, subtitle?: string): RouteHandle => ({ title, subtitle })

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/live" replace /> },
      {
        path: 'live',
        element: <LivePage />,
        handle: handle('Live', 'Broker accounts and the bot’s own record, refreshed every 60 s'),
      },
      {
        path: 'overview',
        element: <OverviewPage />,
        handle: handle('Overview', 'Every result against the validation standard, and the correction for the search'),
      },
      {
        path: 'research',
        element: <ResearchPage />,
        handle: handle('Research files', 'Raw results of every study'),
      },
      {
        path: 'backtest',
        element: <BacktestPage />,
        handle: handle('Backtest', 'One strategy over one period, run in the API'),
      },
      {
        path: 'compare',
        element: <ComparePage />,
        handle: handle('Compare', 'Strategies side by side on the same data'),
      },
      {
        path: 'optimize',
        element: <OptimizePage />,
        handle: handle('Optimize', 'Grid search and walk-forward'),
      },
      { path: '*', element: <NotFoundPage />, handle: handle('Not found') },
    ],
  },
]
