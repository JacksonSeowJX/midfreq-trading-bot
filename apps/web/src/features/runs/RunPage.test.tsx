import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { JOB_POLL } from '@/api/queries'
import type { Job } from '@/api/types'
import { backtestJob, capabilities, failedJob, gridJob, jobList } from '@/test/fixtures'
import { server } from '@/test/handlers'
import { renderApp } from '@/test/render'

const queued: Job = {
  ...backtestJob,
  id: 'job-new',
  status: 'queued',
  progress: 0,
  message: '',
  started: null,
  finished: null,
  backtest: null,
}

/** GET /api/jobs/job-new answers whatever `state.job` is at the time, so a test can move it on. */
function serveJob(start: Job) {
  const state = { job: start, polls: 0 }
  server.use(
    http.get('*/api/jobs/job-new', () => {
      state.polls++
      return HttpResponse.json(state.job)
    }),
  )
  return state
}

beforeEach(() => {
  JOB_POLL.ms = 20
})
afterEach(() => {
  JOB_POLL.ms = 1000
})

describe('running a job', () => {
  it('submits, polls while queued and running, then shows the result', async () => {
    const user = userEvent.setup()
    let posted: Record<string, unknown> | undefined
    server.use(
      http.post('*/api/jobs/backtest', async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(queued, { status: 202 })
      }),
    )
    const state = serveJob(queued)
    const { router } = renderApp('/backtest')
    await user.click(await screen.findByRole('button', { name: 'Run backtest' }))

    await waitFor(() => expect(router.state.location.search).toBe('?job=job-new'))
    expect(posted).toMatchObject({ strategy: 'Z-Score Mean Reversion', symbol: 'US.AAPL', market: 'US' })
    const run = await screen.findByRole('region', { name: 'Run' })
    expect(within(run).getByText('Queued')).toBeInTheDocument()
    expect(within(run).getByTestId('job-message')).toHaveTextContent('Waiting for a free worker')

    state.job = { ...queued, status: 'running', progress: 0.4, message: 'Backtesting US.AAPL', started: '2026-10-16T01:00:01Z' }
    await waitFor(() => expect(within(run).getByRole('progressbar', { name: 'Run progress' })).toHaveAttribute('aria-valuenow', '40'))
    expect(within(run).getByText('40%')).toBeInTheDocument()
    expect(within(run).getByTestId('job-message')).toHaveTextContent('Backtesting US.AAPL')
    expect(within(run).getByRole('button', { name: 'Stop watching' })).toBeInTheDocument()

    state.job = { ...backtestJob, id: 'job-new' }
    expect(await screen.findByRole('group', { name: 'Return' })).toHaveTextContent('+5.43%')
    expect(within(run).getByText('Done')).toBeInTheDocument()
    expect(within(run).queryByRole('progressbar')).not.toBeInTheDocument()

    // a finished run is not polled again
    const polls = state.polls
    await new Promise((r) => setTimeout(r, 100))
    expect(state.polls).toBe(polls)
  })

  it('shows why a run failed', async () => {
    const user = userEvent.setup()
    serveJob({ ...failedJob, id: 'job-new' })
    renderApp('/backtest')
    await user.click(await screen.findByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByText('The run failed')).toBeInTheDocument()
    expect(screen.getByText('No cached 1d candles for US.JPM between 2025-10-16 and 2026-10-16')).toBeInTheDocument()
  })

  it('can stop watching a run, which carries on in the API', async () => {
    const user = userEvent.setup()
    serveJob({ ...queued, status: 'running', progress: 0.1 })
    const { router } = renderApp('/backtest')
    await user.click(await screen.findByRole('button', { name: 'Run backtest' }))
    await user.click(await screen.findByRole('button', { name: 'Stop watching' }))
    expect(router.state.location.search).toBe('')
    expect(screen.getByText('No run open')).toBeInTheDocument()
  })
})

describe('when a run cannot start', () => {
  it('403: Optimize runs only on a local install, with the commands', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('*/api/jobs/optimize', () =>
        HttpResponse.json({ detail: 'optimize runs are not allowed on this server (allowed: backtest, compare)' }, { status: 403 }),
      ),
    )
    renderApp('/optimize')
    await user.click(await screen.findByRole('button', { name: 'Run walk-forward' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Optimize runs only on a local install')
    expect(within(alert).getByText(/uv run uvicorn midfreq\.api\.main:app --port 8000/)).toHaveTextContent('cd apps/web && npm run dev')
  })

  it('422 (FastAPI’s list): marks the field the API rejected, until it is edited', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('*/api/jobs/backtest', () =>
        HttpResponse.json(
          { detail: [{ loc: ['body', 'initial_capital'], msg: 'Input should be at most 10000000', type: 'less_than_equal' }] },
          { status: 422 },
        ),
      ),
    )
    renderApp('/backtest')
    await user.click(await screen.findByRole('button', { name: 'Run backtest' }))
    expect(await screen.findByText('Check the settings')).toBeInTheDocument()
    const capital = screen.getByLabelText('Starting capital (USD)')
    expect(capital).toHaveAttribute('aria-invalid', 'true')
    expect(capital).toHaveAccessibleDescription('Input should be at most 10000000')

    await user.type(capital, '0')
    expect(capital).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByText('Check the settings')).not.toBeInTheDocument()
  })

  it('422 (the API’s own sentence): beside the field it names, else in the summary', async () => {
    const user = userEvent.setup()
    let detail = 'symbol: US.AAPL is not a US symbol'
    server.use(http.post('*/api/jobs/backtest', () => HttpResponse.json({ detail }, { status: 422 })))
    renderApp('/backtest')
    await user.click(await screen.findByRole('button', { name: 'Run backtest' }))
    const symbol = await screen.findByRole('combobox', { name: 'Symbol' })
    await waitFor(() => expect(symbol).toHaveAttribute('aria-invalid', 'true'))
    expect(symbol).toHaveAccessibleDescription('US.AAPL is not a US symbol')

    detail = "unknown param 'x' for Z-Score Mean Reversion"
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    const summary = await screen.findByText('The API rejected these settings')
    expect(summary.closest('[role=alert]')).toHaveTextContent("unknown param 'x' for Z-Score Mean Reversion")
  })

  it('429: says a run is already in progress, and links it whatever its kind', async () => {
    const user = userEvent.setup()
    const busy: Job = { ...gridJob, status: 'running', progress: 0.3, optimize: null }
    server.use(
      http.post('*/api/jobs/compare', () => HttpResponse.json({ detail: '1 run in progress; at most 1 at a time' }, { status: 429 })),
      http.get('*/api/jobs', () => HttpResponse.json([busy, ...jobList.slice(1)])),
    )
    renderApp('/compare')
    await user.click(await screen.findByRole('button', { name: 'Compare strategies' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A run is already in progress')
    expect(alert).toHaveTextContent('This deployment runs one at a time')
    expect(within(alert).getByRole('link', { name: 'Open the running optimize run: Cross-Sectional Reversal' })).toHaveAttribute(
      'href',
      '/optimize?job=opt-grid',
    )
  })

  it('keeps the nav item for a kind this deployment does not allow, and explains it', async () => {
    server.use(http.get('*/api/jobs/capabilities', () => HttpResponse.json({ ...capabilities, kinds: ['backtest', 'compare'] })))
    renderApp('/optimize')
    expect(await screen.findByText('Optimize is available on a local install')).toBeInTheDocument()
    expect(screen.getByText(/grid search or walk-forward runs hundreds of backtests/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Run/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Optimize' })).toHaveAttribute('aria-current', 'page')
  })
})

describe('opening earlier runs', () => {
  it('opens a run from its link', async () => {
    renderApp('/backtest?job=bt-1')
    expect(await screen.findByRole('group', { name: 'Return' })).toHaveTextContent('+5.43%')
    const recent = await screen.findByRole('list', { name: 'Recent runs' })
    const current = within(recent).getByRole('button', { current: true })
    expect(current).toHaveTextContent('Z-Score Mean Reversion')
    // only this view's runs are listed
    expect(within(recent).getAllByRole('button')).toHaveLength(2)
  })

  it('reopens a result from the recent runs', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/optimize')
    const recent = await screen.findByRole('list', { name: 'Recent runs' })
    const items = within(recent).getAllByRole('button')
    expect(items.map((b) => b.textContent)).toEqual([
      expect.stringContaining('Cross-Sectional Reversalsp100 · 1h · 2025-10-16 → 2026-10-16 · grid search'),
      expect.stringContaining('walk-forward'),
    ])
    await user.click(items[1]!)
    expect(router.state.location.search).toBe('?job=opt-wf')
    expect(await screen.findByRole('table', { name: 'Walk-forward windows' })).toBeInTheDocument()
  })

  it('sends a run of another kind to its own page', async () => {
    renderApp('/backtest?job=cmp-1')
    const link = await screen.findByRole('link', { name: 'Open it on the Compare page' })
    expect(link).toHaveAttribute('href', '/compare?job=cmp-1')
  })

  it('says when the API no longer has the run', async () => {
    renderApp('/backtest?job=gone')
    expect(await screen.findByText('This run is no longer on the API')).toBeInTheDocument()
  })
})
