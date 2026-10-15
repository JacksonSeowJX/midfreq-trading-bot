import { Navigate, type RouteObject } from 'react-router'
import { AppShell } from '@/components/layout/AppShell'
import type { RouteHandle } from '@/components/layout/TopBar'
import LivePage from '@/features/live/LivePage'
import { NotFoundPage, StreamlitOnlyPage } from '@/features/placeholders/StreamlitOnlyPage'
import { OverviewPage, ResearchPage } from '@/lazyPages'

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
        element: (
          <StreamlitOnlyPage
            title="Backtest"
            what="One strategy over one period: price chart with indicators, equity curve against buy-and-hold, and the full trade log."
          />
        ),
        handle: handle('Backtest', 'Runs in the Streamlit app for now'),
      },
      {
        path: 'compare',
        element: (
          <StreamlitOnlyPage
            title="Compare"
            what="Every strategy on the selected stock with default settings, side by side."
          />
        ),
        handle: handle('Compare', 'Runs in the Streamlit app for now'),
      },
      {
        path: 'optimize',
        element: (
          <StreamlitOnlyPage
            title="Optimize"
            what="Grid search, or walk-forward: the validation method used throughout the project."
          />
        ),
        handle: handle('Optimize', 'Runs in the Streamlit app for now'),
      },
      { path: '*', element: <NotFoundPage />, handle: handle('Not found') },
    ],
  },
]
