import { describe, expect, it } from 'vitest'
import type { SavedRunTable } from '@/api/types'
import { savedIds, savedRunDetails, savedRuns, savedRunTables } from '@/test/fixtures'
import {
  backtestFromTables,
  compareFromTables,
  compareOrder,
  filterSavedRuns,
  gridFromTable,
  NO_FILTERS,
  periodText,
  sampleComparison,
  savedHeadline,
  savedRunSummary,
  savedRunTitle,
  tradedText,
  tradesOf,
  walkForwardFromTable,
} from './logic'

const detail = (id: string) => savedRunDetails[id]!
const tableOf = (id: string, name: string) => savedRunTables[`${id}/${name}`]!
const run = (id: string) => savedRuns.find((r) => r.run_id === id)!

describe('what a saved run is', () => {
  it('is named by its strategy, else by its kind', () => {
    expect(savedRunTitle(run(savedIds.grid))).toBe('Cross-Sectional Reversal')
    expect(savedRunTitle({ kind: 'compare', strategy: null, metrics: { n_strategies: 4 } })).toBe('4 strategies')
    expect(savedRunTitle({ kind: 'walk_forward', strategy: null, metrics: {} })).toBe('Walk-forward')
  })

  it('says what it traded and over which dates', () => {
    expect(tradedText(run(savedIds.grid))).toBe('sp100')
    expect(tradedText({ universe: null, n_symbols: 3 })).toBe('3 symbols')
    expect(tradedText({ universe: null, n_symbols: null })).toBe('—')
    expect(periodText({ start: '2025-10-16', end: '2026-10-16T00:00:00+00:00' })).toBe('16 Oct 2025 → 16 Oct 2026')
  })

  it('heads each kind with the figure that is honest for it', () => {
    expect(savedHeadline(run(savedIds.backtest))).toMatchObject({ returnPct: 5.43, basis: 'run', sharpe: 1.234, maxDrawdownPct: 4.2, trades: 3 })
    expect(savedHeadline(run(savedIds.compare)).basis).toBe('best')
    expect(savedHeadline(run(savedIds.grid)).basis).toBe('best-in-sample')
    // a walk-forward: the mean out-of-sample return, never the training one
    expect(savedHeadline(run(savedIds.walkForward))).toMatchObject({ returnPct: 1.0, basis: 'mean-out-of-sample', trades: null })
  })

  it('reads the market and capital for the result views from how it was run', () => {
    expect(savedRunSummary(detail(savedIds.backtest))).toMatchObject({ market: 'US', currency: 'USD', initialCapital: 100000 })
    expect(savedRunSummary(detail(savedIds.hk))).toMatchObject({ market: 'HK', currency: 'HKD', initialCapital: null })
  })
})

describe('the list filters', () => {
  it('by kind, source and a search over id, strategy and universe', () => {
    expect(filterSavedRuns(savedRuns, NO_FILTERS)).toHaveLength(5)
    expect(filterSavedRuns(savedRuns, { ...NO_FILTERS, kind: 'backtest' }).map((r) => r.run_id)).toEqual([savedIds.backtest, savedIds.hk])
    expect(filterSavedRuns(savedRuns, { ...NO_FILTERS, source: 'script' }).map((r) => r.kind)).toEqual(['walk_forward', 'backtest'])
    expect(filterSavedRuns(savedRuns, { ...NO_FILTERS, query: 'SP100' }).map((r) => r.kind)).toEqual(['grid_search', 'walk_forward'])
    expect(filterSavedRuns(savedRuns, { ...NO_FILTERS, query: 'c0ffee' }).map((r) => r.run_id)).toEqual([savedIds.compare])
    expect(filterSavedRuns(savedRuns, { kind: 'compare', source: 'script', query: '' })).toEqual([])
  })
})

describe('tables back into the result views', () => {
  it('a backtest: its equity curve, its trades and its metrics; no chart', () => {
    const r = backtestFromTables(detail(savedIds.backtest), tableOf(savedIds.backtest, 'equity'), tableOf(savedIds.backtest, 'trades'))
    expect(r.equity).toEqual([
      { t: '2025-10-16T14:30:00Z', v: 100000 },
      { t: '2026-03-02T14:30:00Z', v: 98800 },
      { t: '2026-10-15T19:30:00Z', v: 105430 },
    ])
    expect(r.trades).toHaveLength(3)
    expect(r.trades[1]).toMatchObject({ action: 'SELL', qty: 100, price: 231.25, pnl: 1072.74 })
    expect(r).toMatchObject({ strategy: 'Z-Score Mean Reversion', symbols: ['US.AAPL'], params: { lookback: 20 }, chart: null })
    expect(r.metrics.return_pct).toBe(5.43)
  })

  it('reads a script’s trades (Portfolio.trade_history: no pnl) and skips rows it cannot use', () => {
    expect(
      tradesOf([
        { timestamp: '2026-01-02T02:30:00+00:00', symbol: 'HK.00700', action: 'BUY', qty: 100, price: 380, commission: 6.1, cash_after: 1 },
        { timestamp: null, symbol: 'HK.00700', action: 'SELL', qty: 100, price: 390 },
      ]),
    ).toEqual([{ timestamp: '2026-01-02T02:30:00+00:00', symbol: 'HK.00700', action: 'BUY', qty: 100, price: 380, commission: 6.1, pnl: null }])
  })

  it('a comparison: a row per strategy with its params parsed, and each curve from the long equity table', () => {
    const r = compareFromTables(tableOf(savedIds.compare, 'strategies'), tableOf(savedIds.compare, 'equity'))
    expect(r.rows.map((x) => x.strategy)).toEqual(['Z-Score Mean Reversion', 'SMA Crossover', 'Bollinger Bands'])
    expect(r.rows[0]!.params).toEqual({ lookback: 20, entry_z: 2, exit_z: 0.5 })
    expect(r.rows[0]!.metrics).not.toHaveProperty('label')
    expect(r.rows[0]!.metrics.return_pct).toBe(5.43)
    expect(Object.keys(r.equity)).toEqual(['SMA Crossover', 'Z-Score Mean Reversion', 'Bollinger Bands'])
    expect(r.equity['Bollinger Bands']).toEqual([
      { t: '2025-10-16T14:30:00Z', v: 100000 },
      { t: '2026-10-15T19:30:00Z', v: 98800 },
    ])
    // colours follow the order asked for, not the rank
    expect(compareOrder(detail(savedIds.compare))).toEqual(['SMA Crossover', 'Z-Score Mean Reversion', 'Bollinger Bands'])
  })

  it('a grid search: parameters and metrics split by column, best first', () => {
    const r = gridFromTable(detail(savedIds.grid), tableOf(savedIds.grid, 'grid'))
    expect(r.method).toBe('grid')
    expect(r.objective).toBe('sharpe_ratio')
    expect(r.rows![0]).toEqual({
      params: { lookback: 10, top_n: 2, rebalance_every: 6 },
      metrics: { return_pct: 7.5, sharpe_ratio: 1.9, max_drawdown: 3.1, win_rate: 50, profit_factor: null, total_trades: 30, alpha: 5.5, _objective: 1.9 },
    })
    expect(r.summary).toMatchObject({ grid_size: 96, combinations: 4, objective_label: 'Sharpe Ratio (higher is better)' })
  })

  it('a walk-forward: params from the plain columns, the objective in sample, every metric out of sample', () => {
    const r = walkForwardFromTable(detail(savedIds.walkForward), tableOf(savedIds.walkForward, 'windows'))
    expect(r.windows).toHaveLength(3)
    const w = r.windows![0]!
    expect(w).toMatchObject({
      index: 1,
      train_start: '2025-10-16T00:00:00+00:00',
      params: { lookback: 10, top_n: 2, rebalance_every: 6 },
      train_objective: 1.82,
    })
    expect(w.test_metrics).toEqual({ return_pct: 2.4, sharpe_ratio: 1.1, total_trades: 12, open_positions: '{"US.AAA": 10}' })
    expect(r.summary).toMatchObject({ avg_oos_return: 1.0, avg_train_return: 4.6, train_returns: [5.1, 6.3, 2.4] })
  })

  it('a walk-forward the API saved: param_<name> where a name was taken', () => {
    const t: SavedRunTable = {
      run_id: 'x',
      name: 'windows',
      columns: ['window', 'train_start', 'param_window', 'is_return_pct', 'oos_return_pct'],
      rows: [{ window: 1, train_start: '2026-01-05T00:00:00+00:00', param_window: 20, is_return_pct: 3, oos_return_pct: 1 }],
      total: 1,
    }
    const r = walkForwardFromTable({ ...detail(savedIds.walkForward), meta: { objective: 'return_pct' } }, t)
    expect(r.windows![0]).toMatchObject({ params: { window: 20 }, train_objective: 3, test_metrics: { return_pct: 1 } })
  })
})

describe('in sample against out of sample', () => {
  it('pairs every metric the windows have both ways, usual ones first, with the mean of each', () => {
    const c = sampleComparison(tableOf(savedIds.walkForward, 'windows'))
    expect(c.pairs.map((p) => p.key)).toEqual(['return_pct', 'sharpe_ratio', 'total_trades'])
    const ret = c.pairs[0]!
    expect(ret.inSample).toEqual([5.1, 6.3, 2.4])
    expect(ret.outOfSample).toEqual([2.4, -0.9, 1.5])
    expect(ret.meanIn).toBeCloseTo(4.6)
    expect(ret.meanOut).toBeCloseTo(1.0)
    expect(c.pairs[1]!.meanIn).toBeCloseTo((1.82 + 2.05 + 1.4) / 3)
    expect(c.windows[2]).toEqual({ index: 3, train: ['2026-05-10T00:00:00+00:00', '2026-08-21T00:00:00+00:00'], test: ['2026-08-21T00:00:00+00:00', '2026-10-16T00:00:00+00:00'] })
  })

  it('leaves out a metric only one side has, and a mean of nothing is null', () => {
    const c = sampleComparison({
      run_id: 'x',
      name: 'windows',
      columns: ['window', 'is_sharpe_ratio', 'oos_sharpe_ratio', 'oos_win_rate'],
      rows: [{ window: 1, is_sharpe_ratio: null, oos_sharpe_ratio: 0.5, oos_win_rate: 60 }],
      total: 1,
    })
    expect(c.pairs).toEqual([{ key: 'sharpe_ratio', inSample: [null], outOfSample: [0.5], meanIn: null, meanOut: 0.5 }])
  })
})
