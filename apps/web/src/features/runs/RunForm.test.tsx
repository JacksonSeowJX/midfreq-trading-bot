import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { StartJob } from '@/api/queries'
import type { JobKind } from '@/api/types'
import type { Market } from '@/lib/preferences'
import { capabilities, strategies, universes } from '@/test/fixtures'
import { renderWithProviders } from '@/test/render'
import { formKey } from './form'
import { marketCaps } from './logic'
import { RunForm, type RunFormProps } from './RunForm'

function setup(kind: JobKind = 'backtest', market: Market = 'US', props: Partial<RunFormProps> = {}) {
  const onSubmit = vi.fn<(req: StartJob) => void>()
  const utils = renderWithProviders(
    <RunForm
      kind={kind}
      strategies={strategies}
      caps={marketCaps(capabilities, market, universes)}
      objectives={capabilities.objectives}
      onSubmit={onSubmit}
      submitting={false}
      {...props}
    />,
    { market },
  )
  return { ...utils, onSubmit, user: userEvent.setup() }
}

const submitted = (fn: ReturnType<typeof setup>['onSubmit']) => fn.mock.calls.at(-1)?.[0]

describe('RunForm: strategy and parameters', () => {
  it('shows the strategy’s parameters with their labels and limits', async () => {
    const { user } = setup()
    expect(screen.getByLabelText('Strategy')).toHaveValue('Z-Score Mean Reversion')
    const lookback = screen.getByLabelText('Lookback Window')
    expect(lookback).toHaveValue(20)
    expect(lookback).toHaveAttribute('min', '5')
    expect(lookback).toHaveAttribute('max', '100')
    expect(lookback).toHaveAttribute('step', '1')
    expect(screen.getByLabelText('Entry Z-Score')).toHaveAttribute('step', '0.1')
    expect(screen.getByText('5–100, step 1')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Strategy'), 'SMA Crossover')
    expect(screen.queryByLabelText('Lookback Window')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Fast MA Period')).toHaveValue(5)
    expect(screen.getByLabelText('Slow MA Period')).toHaveValue(20)
  })

  it('submits the typed parameters as numbers', async () => {
    const { user, onSubmit } = setup()
    const lookback = screen.getByLabelText('Lookback Window')
    await user.clear(lookback)
    await user.type(lookback, '35')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    const req = submitted(onSubmit)
    expect(req?.kind).toBe('backtest')
    expect(req?.kind === 'backtest' && req.body.params).toEqual({ lookback: 35, entry_z: 2, exit_z: 0.5 })
  })

  it('blocks a value outside the limits, says why beside it and focuses it', async () => {
    const { user, onSubmit } = setup()
    const lookback = screen.getByLabelText('Lookback Window')
    await user.clear(lookback)
    await user.type(lookback, '500')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(lookback).toHaveAttribute('aria-invalid', 'true')
    expect(lookback).toHaveAccessibleDescription('Between 5 and 100')
    await waitFor(() => expect(lookback).toHaveFocus())
    expect(screen.getByText('Fix the highlighted field to run.')).toBeInTheDocument()

    await user.clear(lookback)
    await user.type(lookback, '50')
    expect(lookback).not.toHaveAttribute('aria-invalid')
  })

  it('checks the dates and the capital', async () => {
    const { user, onSubmit } = setup()
    const end = screen.getByLabelText('End')
    await user.clear(end)
    await user.type(end, '2020-01-01')
    const capital = screen.getByLabelText('Starting capital (USD)')
    await user.clear(capital)
    await user.type(capital, '10')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(end).toHaveAccessibleDescription('After the start date')
    expect(capital).toHaveAccessibleDescription('At least 1000')
  })
})

describe('RunForm: data', () => {
  it('searches the market’s symbols with cached data', async () => {
    const { user, onSubmit } = setup()
    const symbol = screen.getByRole('combobox', { name: 'Symbol' })
    expect(symbol).toHaveValue('US.AAPL')
    expect(screen.getByText('4 symbols with cached 1h data')).toBeInTheDocument()
    await user.click(symbol)
    await user.keyboard('msf')
    const list = screen.getByRole('listbox')
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual(['US.MSFT'])
    await user.keyboard('{Enter}')
    expect(symbol).toHaveValue('US.MSFT')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body.symbol).toBe('US.MSFT')
  })

  it('drops typed text that matches no symbol', async () => {
    const { user } = setup()
    const symbol = screen.getByRole('combobox', { name: 'Symbol' })
    await user.click(symbol)
    await user.keyboard('zzz')
    expect(screen.getByText('No match')).toBeInTheDocument()
    await user.tab()
    expect(symbol).toHaveValue('US.AAPL')
  })

  it('asks for the second stock for pairs trading', async () => {
    const { user, onSubmit } = setup()
    expect(screen.queryByRole('combobox', { name: 'Pair symbol' })).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Strategy'), 'Pairs Trading')
    const pair = screen.getByRole('combobox', { name: 'Pair symbol' })
    expect(pair).toHaveValue('US.JPM')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body.pair_symbol).toBe('US.JPM')
  })

  it('runs a basket strategy on a universe, with no symbol, and explains why', async () => {
    const { user, onSubmit } = setup()
    expect(screen.queryByLabelText('Universe')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Strategy'), 'Cross-Sectional Reversal')
    const universe = screen.getByLabelText('Universe')
    expect(within(universe).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'US — 15 large caps (15)',
      'US — S&P 100 (100)',
    ])
    await user.selectOptions(universe, 'sp100')
    expect(screen.getByText(/runs on the whole US — S&P 100 \(100\) universe \(98 symbols with data\), not on one stock/)).toBeInTheDocument()
    expect(screen.getByText(/charts the equity against holding every stock of the universe in equal amounts/)).toBeInTheDocument()
    // the strategy ranks the whole universe: there is no one stock to pick
    expect(screen.queryByRole('combobox', { name: /symbol/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Risk & sizing/ })).toHaveTextContent('equal value across the basket')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body).toMatchObject({ universe: 'sp100', symbol: null, pair_symbol: null })

    // back to a single-stock strategy: the symbol returns as it was, and the universe goes
    await user.selectOptions(screen.getByLabelText('Strategy'), 'SMA Crossover')
    expect(screen.getByRole('combobox', { name: 'Symbol' })).toHaveValue('US.AAPL')
    expect(screen.queryByLabelText('Universe')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body).toMatchObject({ symbol: 'US.AAPL', universe: null })
  })

  it('optimize: a basket strategy takes a universe and no symbol too', async () => {
    const { user, onSubmit } = setup('optimize')
    await user.selectOptions(screen.getByLabelText('Strategy'), 'Cross-Sectional Momentum')
    expect(screen.queryByRole('combobox', { name: /symbol/i })).not.toBeInTheDocument()
    expect(screen.getByText(/runs on the whole US — 15 large caps \(15\) universe/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run walk-forward' }))
    expect(submitted(onSubmit)?.body).toMatchObject({ strategy: 'Cross-Sectional Momentum', universe: 'us_15', symbol: null })
  })

  it('puts an API error about the symbol beside it, or in the summary when a basket hides the field', () => {
    const symbolError = { symbol: 'Cross-Sectional Reversal ranks the whole sp100 universe, so it takes no symbol' }
    const first = setup('backtest', 'US', { serverErrors: symbolError })
    expect(screen.getByRole('combobox', { name: 'Symbol' })).toHaveAccessibleDescription(/takes no symbol/)
    expect(screen.queryByText('The API rejected these settings')).not.toBeInTheDocument()
    first.unmount()

    window.localStorage.setItem(formKey('backtest'), JSON.stringify({ strategy: 'Cross-Sectional Reversal' }))
    setup('backtest', 'US', { serverErrors: symbolError })
    const summary = screen.getByRole('alert')
    expect(summary).toHaveTextContent('The API rejected these settings')
    expect(summary).toHaveTextContent(`symbol: ${symbolError.symbol}`)
  })

  it('takes the commission default from the capabilities, per market, shown in percent', async () => {
    const { user, onSubmit } = setup('backtest', 'HK')
    const commission = screen.getByLabelText('Commission per side (%)')
    expect(commission).toHaveValue(0.16)
    expect(screen.getByText('HK default')).toBeInTheDocument()
    expect(screen.getByLabelText('Starting capital (HKD)')).toBeInTheDocument()
    await user.clear(commission)
    await user.type(commission, '0.1')
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body.commission_rate).toBeCloseTo(0.001)
    await user.click(screen.getByRole('button', { name: 'Reset to 0.160%' }))
    expect(commission).toHaveValue(0.16)
  })

  it('offers only the symbols cached at the chosen timeframe', async () => {
    const { user, onSubmit } = setup()
    await user.click(screen.getByRole('radio', { name: '1 day' }))
    expect(screen.getByText('2 symbols with cached 1d data')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Symbol' }))
    expect(within(screen.getByRole('listbox')).getAllByRole('option').map((o) => o.textContent)).toEqual(['US.AAPL', 'US.MSFT'])
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body.timeframe).toBe('1d')
  })
})

describe('RunForm: risk and sizing', () => {
  it('is folded away, with a summary of the settings in force', async () => {
    const { user } = setup()
    const toggle = screen.getByRole('button', { name: /Risk & sizing/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent('100 shares · no stops · halt at 10% drawdown')
    expect(screen.queryByLabelText('Max drawdown halt (%)')).not.toBeInTheDocument()
    await user.click(toggle)
    expect(screen.getByLabelText('Max drawdown halt (%)')).toHaveValue(10)
  })

  it('sends optional stops as fractions when switched on', async () => {
    const { user, onSubmit } = setup()
    await user.click(screen.getByRole('button', { name: /Risk & sizing/ }))
    const stop = screen.getByLabelText('Stop-loss (%)')
    expect(stop).toBeDisabled()
    await user.click(screen.getByRole('switch', { name: 'Stop-loss' }))
    expect(stop).toBeEnabled()
    await user.clear(stop)
    await user.type(stop, '4.5')
    await user.click(screen.getByRole('switch', { name: 'Take-profit' }))
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    const risk = submitted(onSubmit)?.body.risk
    expect(risk?.stop_loss_pct).toBeCloseTo(0.045)
    expect(risk?.take_profit_pct).toBeCloseTo(0.05)
    expect(risk?.trailing_stop_pct).toBeNull()
  })

  it('shows each sizing method’s own fields', async () => {
    const { user, onSubmit } = setup()
    await user.click(screen.getByRole('button', { name: /Risk & sizing/ }))
    expect(screen.getByLabelText('Shares per trade')).toHaveValue(100)
    await user.selectOptions(screen.getByLabelText('Sizing method'), 'kelly')
    expect(screen.queryByLabelText('Shares per trade')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Kelly fraction')).toHaveValue(0.5)
    expect(screen.getByLabelText('Max position (%)')).toHaveValue(25)
    await user.selectOptions(screen.getByLabelText('Sizing method'), 'percent_risk')
    expect(screen.getByLabelText('Risk per trade (%)')).toHaveValue(2)
    await user.selectOptions(screen.getByLabelText('Sizing method'), 'equal_dollar')
    expect(screen.getByText('Split equity evenly by value across the positions.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(submitted(onSubmit)?.body.risk.sizing).toBe('equal_dollar')
  })

  it('opens itself when one of its fields is invalid', async () => {
    const { user, onSubmit } = setup()
    const toggle = screen.getByRole('button', { name: /Risk & sizing/ })
    await user.click(toggle)
    const halt = screen.getByLabelText('Max drawdown halt (%)')
    await user.clear(halt)
    await user.type(halt, '80')
    await user.click(toggle) // fold it away again
    expect(screen.queryByLabelText('Max drawdown halt (%)')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Max drawdown halt (%)')).toHaveAccessibleDescription('Between 5 and 50')
  })
})

describe('RunForm: per view', () => {
  it('remembers the last inputs of each view', async () => {
    const first = setup()
    const capital = screen.getByLabelText('Starting capital (USD)')
    await first.user.clear(capital)
    await first.user.type(capital, '250000')
    await first.user.selectOptions(screen.getByLabelText('Strategy'), 'SMA Crossover')
    first.unmount()
    expect(JSON.parse(window.localStorage.getItem(formKey('backtest'))!).capital).toBe('250000')

    setup()
    expect(screen.getByLabelText('Starting capital (USD)')).toHaveValue(250000)
    expect(screen.getByLabelText('Strategy')).toHaveValue('SMA Crossover')
  })

  it('still works when storage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    setup()
    expect(screen.getByLabelText('Strategy')).toHaveValue('Z-Score Mean Reversion')
    expect(spy).toHaveBeenCalled()
  })

  it('compare: picks two to eight strategies on one stock, each with its parameters', async () => {
    const { user, onSubmit } = setup('compare')
    expect(screen.getByRole('radio', { name: 'One stock' })).toHaveAttribute('data-state', 'on')
    const list = screen.getByRole('list', { name: 'Strategies to compare' })
    const offered = () => within(list).getAllByRole('checkbox').map((c) => c.closest('li')?.querySelector('label')?.textContent)
    // the cross-sectional strategies are not offered beside the single-stock ones
    expect(offered()).toEqual(['SMA Crossover', 'Bollinger Bands', 'Z-Score Mean Reversion', 'Pairs Tradingneeds a pair'])
    expect(within(list).getByLabelText('SMA Crossover')).toBeChecked()
    await user.click(within(list).getByLabelText('SMA Crossover'))
    await user.click(within(list).getByLabelText('Bollinger Bands'))
    await user.click(screen.getByRole('button', { name: 'Compare strategies' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Pick 2 to 8 strategies (1 picked)')).toBeInTheDocument()

    await user.click(within(list).getByLabelText(/Pairs Trading/))
    expect(screen.getByRole('combobox', { name: 'Pair symbol' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Pairs Trading parameters' }))
    const lookback = within(list).getByLabelText('Lookback Window')
    await user.clear(lookback)
    await user.type(lookback, '40')
    await user.click(screen.getByRole('button', { name: 'Compare strategies' }))
    const req = submitted(onSubmit)
    expect(req?.kind === 'compare' && req.body.strategies).toEqual([
      { strategy: 'Z-Score Mean Reversion', params: { lookback: 20, entry_z: 2, exit_z: 0.5 } },
      { strategy: 'Pairs Trading', params: { lookback: 40, entry_z: 2, exit_z: 0.5 } },
    ])
    expect(req?.body).toMatchObject({ symbol: 'US.AAPL', pair_symbol: 'US.JPM', universe: null })
  })

  it('compare: on a universe, the cross-sectional strategies with each other and no symbol', async () => {
    const { user, onSubmit } = setup('compare')
    expect(screen.getByRole('combobox', { name: 'Symbol' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Universe')).not.toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'A universe' }))
    const list = screen.getByRole('list', { name: 'Strategies to compare' })
    const boxes = within(list).getAllByRole('checkbox')
    expect(boxes.map((c) => c.closest('li')?.querySelector('label')?.textContent)).toEqual([
      'Cross-Sectional Reversal',
      'Cross-Sectional Momentum',
    ])
    expect(boxes.every((c) => (c as HTMLInputElement).checked)).toBe(true) // both, by default
    expect(screen.getByText('2 picked, 2–8')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /symbol/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Universe')).toHaveValue('us_15')
    expect(screen.getByText(/they run on the whole US — 15 large caps \(15\) universe/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Cross-Sectional Reversal parameters' }))
    const topN = screen.getByLabelText('Basket Size (# laggards held)')
    await user.clear(topN)
    await user.type(topN, '3')
    await user.click(screen.getByRole('button', { name: 'Compare strategies' }))
    const req = submitted(onSubmit)
    expect(req?.kind === 'compare' && req.body.strategies).toEqual([
      { strategy: 'Cross-Sectional Reversal', params: { lookback: 10, top_n: 3, rebalance_every: 6 } },
      { strategy: 'Cross-Sectional Momentum', params: { lookback: 10, top_n: 2, rebalance_every: 6 } },
    ])
    expect(req?.body).toMatchObject({ symbol: null, pair_symbol: null, universe: 'us_15' })

    // each side keeps its own ticks
    await user.click(within(list).getByLabelText('Cross-Sectional Momentum'))
    await user.click(screen.getByRole('radio', { name: 'One stock' }))
    expect(screen.getByText('3 picked, 2–8')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'A universe' }))
    expect(screen.getByText('1 picked, 2–8')).toBeInTheDocument()
  })

  it('optimize: method, objective and the method’s own settings', async () => {
    const { user, onSubmit } = setup('optimize')
    expect(screen.queryByLabelText('Lookback Window')).not.toBeInTheDocument() // the grid covers the ranges
    // lookback 96 values x entry_z 25 x exit_z 15, as the optimizer counts them
    expect(screen.getByText(/The full grid has/)).toHaveTextContent('The full grid has 36,000 combinations')
    expect(screen.getByLabelText('Rolling windows')).toHaveValue(3)
    await user.selectOptions(screen.getByLabelText('Objective'), 'return_pct')
    await user.click(screen.getByRole('radio', { name: 'Grid search' }))
    expect(screen.queryByLabelText('Rolling windows')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Max combinations')).toHaveValue(200)
    await user.click(screen.getByRole('button', { name: 'Run grid search' }))
    const req = submitted(onSubmit)
    expect(req?.kind === 'optimize' && req.body).toMatchObject({ method: 'grid', objective: 'return_pct', max_combinations: 200 })
  })
})
