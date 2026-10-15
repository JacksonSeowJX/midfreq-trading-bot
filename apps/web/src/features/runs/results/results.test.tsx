import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { OptimizeResult } from '@/api/types'
import { backtestJob, backtestResult, capabilities, compareJob, compareResult, gridResult, walkForwardResult } from '@/test/fixtures'
import { renderWithProviders } from '@/test/render'
import { runSummary } from '../logic'
import { BacktestResultView } from './BacktestResultView'
import { CompareResultView } from './CompareResultView'
import { OptimizeResultView } from './OptimizeResultView'

const kpi = (label: string) => within(screen.getByRole('group', { name: label })).getByTestId('kpi-value')
const rowsOf = (table: HTMLElement) => within(table).getAllByRole('row').slice(1)

describe('BacktestResultView', () => {
  it('shows the headline figures', () => {
    renderWithProviders(<BacktestResultView result={backtestResult} summary={runSummary(backtestJob)} />)
    expect(kpi('Return')).toHaveTextContent('+5.43%')
    expect(kpi('Return')).toHaveClass('text-gain')
    expect(kpi('Sharpe ratio')).toHaveTextContent('1.23')
    expect(kpi('Max drawdown')).toHaveTextContent('4.20%')
    expect(kpi('Win rate')).toHaveTextContent('55.0%')
    expect(kpi('Trades')).toHaveTextContent('3')
    expect(kpi('Final value')).toHaveTextContent('USD 105,430')
    expect(screen.getByRole('group', { name: 'Final value' })).toHaveTextContent('USD +5,430 on USD 100,000')
    expect(kpi('Profit factor')).toHaveTextContent('∞') // null from the API: no losing trade
    expect(screen.getByRole('group', { name: 'Buy and hold' })).toHaveTextContent('+8.10%alpha -2.67%')
  })

  it('charts the price with indicators and the trades, and the equity curve', () => {
    renderWithProviders(<BacktestResultView result={backtestResult} summary={runSummary(backtestJob)} />)
    const price = screen.getByRole('img', { name: 'US.AAPL candles with Z-Score Mean Reversion indicators and trades' })
    expect(price).toHaveAttribute('data-markers', '3')
    expect(screen.getByText('3 candles · 3 trades marked')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Equity curve of the backtest' })).toBeInTheDocument()
    expect(screen.getByText('against the initial capital of USD 100,000')).toBeInTheDocument()
  })

  it('lists the trades, newest first', () => {
    renderWithProviders(<BacktestResultView result={backtestResult} summary={runSummary(backtestJob)} />)
    const rows = rowsOf(screen.getByRole('table', { name: 'Backtest trades' }))
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('BUY')
    expect(rows[1]).toHaveTextContent('SELL')
    expect(within(rows[1]!).getByText('+1,072.74')).toHaveClass('text-gain')
  })

  it('says when there is no chart and no trade', () => {
    renderWithProviders(
      <BacktestResultView result={{ ...backtestResult, chart: null, trades: [] }} summary={runSummary(backtestJob)} />,
    )
    expect(screen.getByText('No price chart for this run')).toBeInTheDocument()
    expect(screen.getByText('The strategy placed no trades in this period')).toBeInTheDocument()
  })

  it('notes that a basket run charts one symbol for reference', () => {
    renderWithProviders(
      <BacktestResultView result={{ ...backtestResult, symbols: ['US.AAPL', 'US.MSFT', 'US.NVDA'] }} summary={runSummary(backtestJob)} />,
    )
    expect(screen.getByText(/Traded 3 symbols; the chart shows US\.AAPL for reference only/)).toBeInTheDocument()
  })
})

describe('CompareResultView', () => {
  it('tables the metrics, best return first, with the best of each column marked', () => {
    renderWithProviders(<CompareResultView result={compareResult} summary={runSummary(compareJob)} />)
    const table = screen.getByRole('table', { name: 'Strategy comparison' })
    const rows = rowsOf(table)
    expect(rows.map((r) => within(r).getAllByRole('cell')[0]!.textContent)).toEqual([
      'Z-Score Mean Reversion',
      'SMA Crossover',
      'Bollinger Bands',
    ])
    const best = [...table.querySelectorAll('[data-best]')].map((el) => el.textContent)
    // return, alpha, Sharpe, drawdown (lowest), final value: Z-Score; win rate: Bollinger; trades: no best
    expect(best).toEqual(['+5.43% (best)', '-2.67% (best)', '1.23 (best)', '4.20% (best)', 'USD 105,430 (best)', '61.0% (best)'])
  })

  it('overlays one equity curve per strategy, each in its own colour', () => {
    renderWithProviders(<CompareResultView result={compareResult} summary={runSummary(compareJob)} />)
    const chart = screen.getByRole('img', { name: 'Equity curves of 3 strategies' })
    const legend = within(chart).getAllByRole('listitem')
    expect(legend.map((l) => l.textContent)).toEqual(['SMA Crossover', 'Z-Score Mean Reversion', 'Bollinger Bands'])
    expect(new Set(legend.map((l) => l.dataset.color)).size).toBe(3)
  })

  it('keeps each strategy’s colour whatever order the rows come in', () => {
    const requested = compareResult.rows.map((r) => r.strategy)
    const colours = () =>
      within(screen.getByRole('img', { name: /Equity curves/ }))
        .getAllByRole('listitem')
        .map((l) => [l.textContent, l.dataset.color])
    const first = renderWithProviders(<CompareResultView result={compareResult} summary={runSummary(compareJob)} requested={requested} />)
    const before = colours()
    first.unmount()
    const reversed = { ...compareResult, rows: [...compareResult.rows].reverse() }
    renderWithProviders(<CompareResultView result={reversed} summary={runSummary(compareJob)} requested={requested} />)
    expect(colours()).toEqual(before)
  })

  it('notes that cross-sectional rows are not like-for-like, only when there is one', () => {
    renderWithProviders(<CompareResultView result={compareResult} summary={runSummary(compareJob)} />)
    expect(screen.queryByText(/not like-for-like/)).not.toBeInTheDocument()
  })

  it('sorts by any column', async () => {
    const user = userEvent.setup()
    renderWithProviders(<CompareResultView result={compareResult} summary={runSummary(compareJob)} />)
    const table = screen.getByRole('table', { name: 'Strategy comparison' })
    await user.click(within(table).getByRole('button', { name: /Trades/ }))
    expect(rowsOf(table).map((r) => within(r).getAllByRole('cell')[0]!.textContent)).toEqual([
      'Z-Score Mean Reversion',
      'Bollinger Bands',
      'SMA Crossover',
    ])
  })
})

describe('OptimizeResultView: grid search', () => {
  it('lists every combination best first, with the best one’s figures', () => {
    renderWithProviders(<OptimizeResultView result={gridResult} objectives={capabilities.objectives} />)
    expect(kpi('Best parameters')).toHaveTextContent('lookback=10, top_n=2, rebalance_every=6')
    expect(screen.getByText('4 tested of 96')).toBeInTheDocument()
    expect(kpi('Sharpe Ratio')).toHaveTextContent('1.90')
    const rows = rowsOf(screen.getByRole('table', { name: 'Grid search results' }))
    expect(rows).toHaveLength(4)
    expect(within(rows[0]!).getAllByRole('cell').slice(0, 4).map((c) => c.textContent)).toEqual(['1', '10', '2', '6'])
    expect(rows[0]).toHaveTextContent('∞') // an infinite profit factor
    expect(screen.getByText(/The best row was picked on the same data it is scored on/)).toBeInTheDocument()
  })

  it('draws a heatmap of the objective when exactly two parameters vary', () => {
    renderWithProviders(<OptimizeResultView result={gridResult} objectives={capabilities.objectives} />)
    expect(screen.getByRole('img', { name: 'Sharpe Ratio by lookback and top_n' })).toBeInTheDocument()
  })

  it('labels a drawdown objective as negated, as it was ranked', () => {
    const dd: OptimizeResult = {
      ...gridResult,
      objective: 'max_drawdown',
      rows: (gridResult.rows ?? []).map((r) => ({ ...r, metrics: { ...r.metrics, _objective: -(r.metrics.max_drawdown as number) } })),
    }
    renderWithProviders(<OptimizeResultView result={dd} objectives={capabilities.objectives} />)
    expect(screen.getByRole('img', { name: 'Max Drawdown (negated) by lookback and top_n' })).toBeInTheDocument()
    expect(kpi('Max Drawdown')).toHaveTextContent('3.10%') // the best row's drawdown, as it reads
  })

  it('explains the missing heatmap when more than two parameters vary', () => {
    const three: OptimizeResult = {
      ...gridResult,
      rows: (gridResult.rows ?? []).map((r, i) => ({ ...r, params: { ...r.params, rebalance_every: i } })),
    }
    renderWithProviders(<OptimizeResultView result={three} objectives={capabilities.objectives} />)
    expect(screen.getByText(/this grid varies 3 \(lookback, top_n, rebalance_every\)/)).toBeInTheDocument()
  })
})

describe('OptimizeResultView: walk-forward', () => {
  it('shows the out-of-sample figures and why they are the honest ones', () => {
    renderWithProviders(<OptimizeResultView result={walkForwardResult} objectives={capabilities.objectives} />)
    expect(kpi('Mean test return')).toHaveTextContent('+1.00%')
    expect(kpi('Mean train return')).toHaveTextContent('+4.60%')
    expect(kpi('Windows positive')).toHaveTextContent('67%')
    expect(kpi('WF efficiency')).toHaveTextContent('42%')
    expect(screen.getByText('Walk-forward is the honest out-of-sample measure.')).toBeInTheDocument()
  })

  it('tables each window and charts the test returns with their mean', () => {
    renderWithProviders(<OptimizeResultView result={walkForwardResult} objectives={capabilities.objectives} />)
    const rows = rowsOf(screen.getByRole('table', { name: 'Walk-forward windows' }))
    expect(within(rows[0]!).getAllByRole('cell').map((c) => c.textContent)).toEqual([
      'W1',
      '16 Oct 2025 → 27 Jan 2026',
      '27 Jan 2026 → 05 Apr 2026',
      '1.82',
      '+5.10%',
      '+2.40%',
      '1.10',
      'lookback=10, top_n=2, rebalance_every=6',
    ])
    expect(within(rows[1]!).getByText('-0.90%')).toHaveClass('text-loss')
    expect(screen.getByRole('img', { name: 'Out-of-sample return per walk-forward window, with the mean' })).toBeInTheDocument()
  })
})
