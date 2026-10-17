import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SavedRun } from '@/api/types'
import { savedIds, savedRuns } from '@/test/fixtures'
import { server } from '@/test/handlers'
import { renderApp } from '@/test/render'

const rowsOf = (table: HTMLElement) => within(table).getAllByRole('row').slice(1) // without the header

async function savedRunsTable() {
  return screen.findByRole('table', { name: 'Saved runs' })
}

describe('Run history: the list', () => {
  it('sits in the left nav below Optimize and lists every saved run', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/live')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    const links = within(nav).getAllByRole('link').map((a) => a.getAttribute('aria-label'))
    expect(links.indexOf('Run history')).toBe(links.indexOf('Optimize') + 1)
    await user.click(within(nav).getByRole('link', { name: 'Run history' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Run history' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/runs')
    const table = await savedRunsTable()
    expect(rowsOf(table)).toHaveLength(5)
    expect(screen.getByText(/5 of 5 shown/)).toBeInTheDocument()
    // newest first
    expect(rowsOf(table)[0]).toHaveTextContent(savedIds.backtest)
    expect(rowsOf(table)[4]).toHaveTextContent(savedIds.hk)
  })

  it('heads each kind with what its return is: best, in sample, or out of sample', async () => {
    renderApp('/runs')
    const table = await savedRunsTable()
    const row = (id: string) => rowsOf(table).find((r) => r.textContent?.includes(id))!
    expect(row(savedIds.backtest)).toHaveTextContent('+5.43%')
    expect(row(savedIds.compare)).toHaveTextContent('best+5.43%')
    expect(row(savedIds.grid)).toHaveTextContent('IS+7.50%')
    expect(row(savedIds.walkForward)).toHaveTextContent('OOS+1.00%')
    expect(row(savedIds.walkForward)).toHaveTextContent('Walk-forward')
    expect(row(savedIds.walkForward)).toHaveTextContent('Script')
    expect(row(savedIds.backtest)).toHaveTextContent('Dashboard')
    expect(row(savedIds.hk)).toHaveTextContent('HK.00700')
    expect(row(savedIds.grid)).toHaveTextContent('16 Oct 2025 → 16 Oct 2026')
  })

  it('filters by kind, source and a search, and keeps them in the URL', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/runs')
    const table = await savedRunsTable()
    await user.click(screen.getByRole('button', { name: 'Backtest · 2' }))
    expect(rowsOf(table)).toHaveLength(2)
    expect(router.state.location.search).toBe('?kind=backtest')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Source' }), 'script')
    expect(rowsOf(table)).toHaveLength(1)
    expect(rowsOf(table)[0]).toHaveTextContent('RSI')
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(rowsOf(table)).toHaveLength(5)
    await user.type(screen.getByRole('textbox', { name: 'Search runs' }), 'sp100')
    expect(rowsOf(table)).toHaveLength(2)
    expect(router.state.location.search).toBe('?q=sp100')
    await user.click(screen.getByRole('button', { name: 'Compare · 1' }))
    expect(screen.getByText('No runs match these filters')).toBeInTheDocument()
  })

  it('sorts by any column', async () => {
    const user = userEvent.setup()
    renderApp('/runs')
    const table = await savedRunsTable()
    await user.click(within(table).getByRole('button', { name: /Return/ }))
    expect(rowsOf(table)[0]).toHaveTextContent(savedIds.hk) // -2.10% first
    await user.click(within(table).getByRole('button', { name: /Return/ }))
    expect(rowsOf(table)[0]).toHaveTextContent(savedIds.grid) // +7.50% first
  })

  it('opens a run by clicking its row', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/runs')
    const table = await savedRunsTable()
    await user.click(within(rowsOf(table).find((r) => r.textContent?.includes(savedIds.hk))!).getByText('RSI'))
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${savedIds.hk}`))
    expect(await screen.findByRole('heading', { level: 1, name: 'RSI' })).toBeInTheDocument()
  })

  it('shows that it is reading the store while a slow bucket answers, then the runs', async () => {
    server.use(
      http.get('*/api/runs', async () => {
        await delay(150)
        return HttpResponse.json(savedRuns)
      }),
    )
    renderApp('/runs')
    expect(await screen.findByText('Reading the saved runs from the run store…')).toBeInTheDocument()
    expect(rowsOf(await savedRunsTable())).toHaveLength(5)
  })

  it('says when nothing is saved yet, and where runs come from', async () => {
    server.use(http.get('*/api/runs', () => HttpResponse.json([] satisfies SavedRun[])))
    renderApp('/runs')
    const empty = (await screen.findByText('No saved runs yet')).closest('[role="status"]') as HTMLElement
    expect(within(empty).getByRole('link', { name: 'Backtest' })).toHaveAttribute('href', '/backtest')
  })

  it('a store that cannot be read is shown with the API’s reason', async () => {
    server.use(
      http.get('*/api/runs', () =>
        HttpResponse.json({ detail: 'the run store cannot be read: Forbidden: 403 storage.objects.list' }, { status: 503 }),
      ),
    )
    renderApp('/runs')
    expect(await screen.findByText('Unavailable right now')).toBeInTheDocument()
    expect(screen.getByText(/403 storage\.objects\.list/)).toBeInTheDocument()
  })
})

describe('Run history: a saved run', () => {
  it('a backtest: its KPIs, equity curve and trades, and how it was run', async () => {
    renderApp(`/runs/${savedIds.backtest}`)
    expect(await screen.findByRole('heading', { level: 1, name: 'Z-Score Mean Reversion' })).toBeInTheDocument()
    expect(await screen.findByRole('group', { name: 'Return' })).toHaveTextContent('+5.43%')
    expect(screen.getByRole('group', { name: 'Final value' })).toHaveTextContent('USD 105,430')
    expect(screen.getByRole('img', { name: 'Equity curve of the backtest' })).toBeInTheDocument()
    expect(rowsOf(screen.getByRole('table', { name: 'Backtest trades' }))).toHaveLength(3)
    expect(screen.getByText(/The price and buy-and-hold charts are not saved with a run/)).toBeInTheDocument()
    expect(screen.getByTestId('run-id')).toHaveTextContent(savedIds.backtest)
    expect(screen.getByText(/from the dashboard/)).toBeInTheDocument()
    const settings = screen.getByRole('heading', { name: 'How it was run' }).closest('[data-slot="card"]') as HTMLElement
    expect(within(settings).getByText('0.0050% per side')).toBeInTheDocument()
    expect(within(settings).getByText('lookback=20, entry_z=2, exit_z=0.500')).toBeInTheDocument()
    expect(within(settings).getByText('1f2639af67')).toBeInTheDocument()
  })

  it('a comparison: the metrics table and the equity curves', async () => {
    renderApp(`/runs/${savedIds.compare}`)
    const table = await screen.findByRole('table', { name: 'Strategy comparison' })
    expect(rowsOf(table)).toHaveLength(3)
    const legend = screen.getByRole('list', { name: 'Legend' })
    expect(within(legend).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'SMA Crossover',
      'Z-Score Mean Reversion',
      'Bollinger Bands',
    ])
  })

  it('a grid search: every combination and the heatmap', async () => {
    renderApp(`/runs/${savedIds.grid}`)
    const table = await screen.findByRole('table', { name: 'Grid search results' })
    expect(rowsOf(table)).toHaveLength(4)
    expect(screen.getByRole('img', { name: 'Sharpe Ratio by lookback and top_n' })).toBeInTheDocument()
    expect(screen.getByText(/In-sample\./)).toBeInTheDocument()
  })

  it('a walk-forward: in sample against out of sample per window, with the mean of each', async () => {
    renderApp(`/runs/${savedIds.walkForward}`)
    const table = await screen.findByRole('table', { name: 'In sample against out of sample' })
    expect(within(table).getAllByRole('columnheader', { name: 'In sample' })).toHaveLength(3) // return, Sharpe, trades
    expect(screen.getByTestId('mean-in-return_pct')).toHaveTextContent('+4.60%')
    expect(screen.getByTestId('mean-out-return_pct')).toHaveTextContent('+1.00%')
    expect(screen.getByTestId('mean-out-sharpe_ratio')).toHaveTextContent('0.47')
    expect(within(table).getByText('Mean of 3 windows')).toBeInTheDocument()
    // and the walk-forward view itself
    expect(screen.getByRole('group', { name: 'Mean test return' })).toHaveTextContent('+1.00%')
    expect(rowsOf(screen.getByRole('table', { name: 'Walk-forward windows' }))).toHaveLength(3)
  })

  it('previews a table on demand', async () => {
    const user = userEvent.setup()
    renderApp(`/runs/${savedIds.hk}`)
    const tables = await screen.findByRole('list', { name: 'Tables' })
    expect(within(tables).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByRole('table', { name: 'Table positions' })).not.toBeInTheDocument()
    await user.click(within(tables).getByRole('button', { name: /^positions/ }))
    const preview = await screen.findByRole('table', { name: 'Table positions' })
    expect(rowsOf(preview)[0]).toHaveTextContent('HK.00700')
    expect(screen.getByText('1 rows × 2 columns')).toBeInTheDocument()
  })

  it('says there is no result view when a run lacks the tables for one', async () => {
    server.use(
      http.get(`*/api/runs/${savedIds.grid}`, async () => {
        const { savedRunDetails } = await import('@/test/fixtures')
        return HttpResponse.json({ ...savedRunDetails[savedIds.grid], tables: [] })
      }),
    )
    renderApp(`/runs/${savedIds.grid}`)
    expect(await screen.findByText('No result view for this run')).toBeInTheDocument()
    expect(screen.getByText('This run saved no tables')).toBeInTheDocument()
  })

  it('an unknown run is not found', async () => {
    renderApp('/runs/20200101T000000_backtest_000000')
    expect(await screen.findByText('No such saved run')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to Run history' })).toHaveAttribute('href', '/runs')
  })
})

describe('Run history: downloads', () => {
  const saved: { name: string; size: number }[] = []
  const requested: string[] = []
  beforeEach(() => {
    saved.length = 0
    requested.length = 0
    URL.createObjectURL = vi.fn(() => 'blob:mock')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push({ name: this.download, size: (vi.mocked(URL.createObjectURL).mock.calls.at(-1)?.[0] as Blob).size })
    })
    server.events.on('request:start', ({ request }) => {
      if (request.url.includes('/tables/')) requested.push(request.url)
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
    server.events.removeAllListeners()
  })

  it('downloads a table as CSV or Parquet, the whole file, through the API', async () => {
    const user = userEvent.setup()
    renderApp(`/runs/${savedIds.backtest}`)
    await user.click(await screen.findByRole('button', { name: 'Download equity as CSV' }))
    await waitFor(() => expect(saved).toHaveLength(1))
    expect(saved[0]!.name).toBe(`${savedIds.backtest}_equity.csv`)
    expect(saved[0]!.size).toBeGreaterThan(20)
    await user.click(screen.getByRole('button', { name: 'Download trades as Parquet' }))
    await waitFor(() => expect(saved).toHaveLength(2))
    expect(saved[1]).toEqual({ name: `${savedIds.backtest}_trades.parquet`, size: 4 })
    expect(requested.filter((u) => u.includes('format=csv'))).toEqual([
      expect.stringContaining(`/api/runs/${savedIds.backtest}/tables/equity?format=csv`),
    ])
    expect(requested.filter((u) => u.includes('format=parquet'))).toEqual([expect.stringContaining('/tables/trades?format=parquet')])
  })

  it('says when a download fails', async () => {
    const user = userEvent.setup()
    server.use(
      http.get('*/api/runs/:id/tables/:name', ({ request }) =>
        new URL(request.url).searchParams.get('format') === 'csv'
          ? HttpResponse.json({ detail: 'the run store cannot be read' }, { status: 503 })
          : HttpResponse.json({ detail: 'no table' }, { status: 404 }),
      ),
    )
    renderApp(`/runs/${savedIds.hk}`)
    await user.click(await screen.findByRole('button', { name: 'Download positions as CSV' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('CSV download failed: unavailable right now')
  })
})

describe('Run history: deleting', () => {
  it('asks again, deletes, and goes back to the list without it', async () => {
    const user = userEvent.setup()
    let deleted: string | null = null
    server.use(
      http.get('*/api/runs', () => HttpResponse.json(savedRuns.filter((r) => r.run_id !== deleted))),
      http.delete('*/api/runs/:id', ({ params }) => {
        deleted = String(params.id)
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const { router } = renderApp(`/runs/${savedIds.hk}`)
    await user.click(await screen.findByRole('button', { name: 'Delete run' }))
    const confirm = screen.getByRole('group', { name: 'Confirm delete' })
    expect(deleted).toBeNull()
    await user.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/runs'))
    expect(deleted).toBe(savedIds.hk)
    expect(await screen.findByText(savedIds.hk, { selector: 'span' })).toBeInTheDocument() // "Deleted run …"
    const table = await savedRunsTable()
    await waitFor(() => expect(rowsOf(table)).toHaveLength(4))
    expect(table).not.toHaveTextContent(savedIds.hk)
  })

  it('cancel keeps the run', async () => {
    const user = userEvent.setup()
    renderApp(`/runs/${savedIds.hk}`)
    await user.click(await screen.findByRole('button', { name: 'Delete run' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Delete run' })).toBeInTheDocument()
  })

  it('where deleting is off (403) it says so, and the button is gone from then on', async () => {
    const user = userEvent.setup()
    server.use(
      http.delete('*/api/runs/:id', () =>
        HttpResponse.json({ detail: 'deleting runs is not allowed on this server' }, { status: 403 }),
      ),
    )
    const { router } = renderApp(`/runs/${savedIds.hk}`)
    await user.click(await screen.findByRole('button', { name: 'Delete run' }))
    await user.click(within(screen.getByRole('group', { name: 'Confirm delete' })).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('Deleting runs is turned off on this server.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete run' })).not.toBeInTheDocument()
    await router.navigate(`/runs/${savedIds.backtest}`)
    expect(await screen.findByRole('heading', { level: 1, name: 'Z-Score Mean Reversion' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete run' })).not.toBeInTheDocument()
  })
})

describe('a finished job links to its saved run', () => {
  it('Saved as run <id>, which opens it in Run history', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/backtest?job=bt-1')
    const run = await screen.findByRole('region', { name: 'Run' })
    const link = await within(run).findByRole('link', { name: savedIds.backtest })
    expect(within(run).getByText(/Saved as run/)).toBeInTheDocument()
    await user.click(link)
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${savedIds.backtest}`))
    expect(await screen.findByRole('heading', { level: 1, name: 'Z-Score Mean Reversion' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Run history' })).toHaveAttribute('aria-current', 'page')
  })

  it('a finished job that was not saved says so', async () => {
    renderApp('/compare?job=cmp-1')
    expect(await screen.findByText('This run was not saved to Run history.')).toBeInTheDocument()
  })
})
