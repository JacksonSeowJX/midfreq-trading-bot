import { describe, expect, it } from 'vitest'
import { capabilities, strategies, universes } from '@/test/fixtures'
import {
  detailFieldErrors,
  checkBacktest,
  checkCompare,
  checkNumber,
  checkOptimize,
  compareSelection,
  defaultForm,
  formKey,
  gridSize,
  isBasket,
  isIntegerParam,
  loadForm,
  resolveChoice,
  resolveCompareOn,
  saveForm,
  serverFieldErrors,
  type FormContext,
  type RunForm,
} from './form'
import { marketCaps } from './logic'

const TODAY = new Date(2026, 9, 16)
const us: FormContext = {
  market: 'US',
  caps: marketCaps(capabilities, 'US', universes),
  strategies,
  objectives: capabilities.objectives,
}
const form = (patch: Partial<RunForm> = {}): RunForm => ({ ...defaultForm(TODAY), ...patch })

describe('checkNumber', () => {
  it('parses a number within its limits, or says what is wrong', () => {
    expect(checkNumber('12', { min: 5, max: 200 })).toEqual({ value: 12 })
    expect(checkNumber('4', { min: 5, max: 200 })).toEqual({ error: 'Between 5 and 200' })
    expect(checkNumber('2.5', { min: 1, integer: true })).toEqual({ error: 'A whole number' })
    expect(checkNumber('', {})).toEqual({ error: 'Required' })
    expect(checkNumber('  ', { optional: true })).toEqual({ value: null })
    expect(checkNumber('abc', {})).toEqual({ error: 'Not a number' })
    expect(checkNumber('999', { min: 1000 })).toEqual({ error: 'At least 1000' })
  })
})

describe('strategy parameters', () => {
  it('treats a parameter as whole-numbered only when its default and limits all are', () => {
    const z = strategies.find((s) => s.name === 'Z-Score Mean Reversion')!
    expect(isIntegerParam(z.params[0]!)).toBe(true) // lookback 20, 5..100 step 1
    expect(isIntegerParam(z.params[1]!)).toBe(false) // entry_z 2 (2.0 in Python), step 0.1
  })

  it('counts the grid the optimizer searches', () => {
    const cs = strategies.find((s) => s.name === 'Cross-Sectional Reversal')!
    // lookback 5..30 by 5 (6), top_n 1..4 (4), rebalance 1..24 by 6 (1, 7, 13, 19: 4)
    expect(gridSize(cs)).toBe(96)
    // adding 0.1 in floating point overshoots the last value, as in generate_param_grid
    const z = strategies.find((s) => s.name === 'Z-Score Mean Reversion')!
    expect(gridSize(z)).toBe(96 * 25 * 15)
  })

  it('knows which strategies trade a whole universe', () => {
    expect(isBasket('Cross-Sectional Reversal')).toBe(true)
    expect(isBasket('Cross-Sectional Momentum')).toBe(true)
    expect(isBasket('Pairs Trading')).toBe(false)
  })
})

describe('checkBacktest', () => {
  it('builds the request with percentages as fractions and the registry defaults', () => {
    const r = checkBacktest(form(), us)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.request).toMatchObject({
      strategy: 'Z-Score Mean Reversion',
      params: { lookback: 20, entry_z: 2, exit_z: 0.5 },
      market: 'US',
      symbol: 'US.AAPL',
      pair_symbol: null,
      universe: null,
      timeframe: '1h',
      start: '2025-10-16',
      end: '2026-10-16',
      initial_capital: 100000,
      slippage_bps: 0,
      risk: { stop_loss_pct: null, max_drawdown_pct: 0.1, sizing: 'fixed_quantity', qty: 100 },
    })
    expect(r.request.commission_rate).toBeCloseTo(0.00005, 10) // 0.005% from the capabilities
  })

  it('sends the stops that are switched on, and the chosen sizing method’s fields', () => {
    const f = form({
      risk: { ...defaultForm().risk, stopLossOn: true, stopLossPct: '3', sizing: 'kelly', kellyFraction: '0.4', maxPositionPct: '20', qty: 'oops' },
    })
    const r = checkBacktest(f, us)
    expect(r.ok).toBe(true) // the fixed-quantity field is not in use, so its bad value does not block
    if (!r.ok) return
    expect(r.request.risk.stop_loss_pct).toBeCloseTo(0.03)
    expect(r.request.risk.trailing_stop_pct).toBeNull()
    expect(r.request.risk).toMatchObject({ sizing: 'kelly', fraction: 0.4, max_pct: 0.2, qty: 100 })
  })

  it('reports every bad field under its request path', () => {
    const r = checkBacktest(
      form({
        params: { 'Z-Score Mean Reversion': { lookback: '3' } },
        capital: '500',
        start: '2026-10-16',
        end: '2026-01-01',
        risk: { ...defaultForm().risk, takeProfitOn: true, takeProfitPct: '45' },
      }),
      us,
    )
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors).toEqual({
      'params.lookback': 'Between 5 and 100',
      initial_capital: 'At least 1000',
      end: 'After the start date',
      'risk.take_profit_pct': 'Between 1 and 30',
    })
  })

  it('adds the pair for pairs trading, and for a basket strategy the universe in place of the symbol', () => {
    const pairs = checkBacktest(form({ strategy: 'Pairs Trading' }), us)
    expect(pairs.ok && pairs.request).toMatchObject({ symbol: 'US.AAPL', pair_symbol: 'US.JPM', universe: null })
    const basket = checkBacktest(form({ strategy: 'Cross-Sectional Reversal' }), us)
    expect(basket.ok && basket.request).toMatchObject({ symbol: null, pair_symbol: null, universe: 'us_15' })
  })

  it('needs a universe with more stocks than the basket holds', () => {
    const small = (withData: number): FormContext => ({
      ...us,
      caps: { ...us.caps, universes: [{ name: 'tiny', label: 'Tiny', symbols: 5, withData, description: '' }] },
    })
    const reversal = (topN: string) => form({ strategy: 'Cross-Sectional Reversal', params: { 'Cross-Sectional Reversal': { top_n: topN } } })
    const r = checkBacktest(reversal('3'), small(3))
    expect(!r.ok && r.errors).toEqual({ universe: 'A basket of 3 needs more than 3 stocks with data to rank; this universe has 3' })
    expect(checkBacktest(reversal('2'), small(3)).ok).toBe(true)
    // a search tries every basket size up to the registry's maximum, 4
    const search = checkOptimize(reversal('1'), small(4))
    expect(!search.ok && search.errors.universe).toBe(
      'The search tries baskets of up to 4, so it needs more than 4 stocks with data to rank; this universe has 4',
    )
    expect(checkOptimize(reversal('1'), small(5)).ok).toBe(true)
    // a top_n that is not a number yet is reported beside itself, not against the universe
    const typing = checkBacktest(reversal('x'), small(3))
    expect(!typing.ok && typing.errors).toEqual({ 'params.top_n': 'Not a number' })
  })
})

describe('checkCompare and checkOptimize', () => {
  it('needs two to eight strategies, each with its own parameters', () => {
    const one = checkCompare(form({ compare: ['RSI', 'SMA Crossover'] }), us)
    expect(!one.ok && one.errors.strategies).toBe('Pick 2 to 8 strategies (1 picked)') // RSI is not in the registry here
    const two = checkCompare(form({ compare: ['SMA Crossover', 'Bollinger Bands'] }), us)
    expect(two.ok && two.request.strategies).toEqual([
      { strategy: 'SMA Crossover', params: { fast_period: 5, slow_period: 20 } },
      { strategy: 'Bollinger Bands', params: { bb_period: 20, num_std: 2 } },
    ])
  })

  it('defaults to the single-stock strategies', () => {
    const r = checkCompare(form(), us)
    expect(r.ok && r.request.strategies.map((s) => s.strategy)).toEqual(['SMA Crossover', 'Bollinger Bands', 'Z-Score Mean Reversion'])
  })

  it('compares on one stock or on one universe, never both', () => {
    // a selection remembered from before keeps only the strategies that run on a stock
    const stock = checkCompare(form({ compare: ['SMA Crossover', 'Cross-Sectional Reversal', 'Bollinger Bands'] }), us)
    expect(stock.ok && stock.request).toMatchObject({ symbol: 'US.AAPL', universe: null })
    expect(stock.ok && stock.request.strategies.map((s) => s.strategy)).toEqual(['SMA Crossover', 'Bollinger Bands'])
    // on a universe: every cross-sectional strategy by default, and no symbol
    const universe = checkCompare(form({ compareOn: 'universe', compare: ['SMA Crossover', 'Bollinger Bands'] }), us)
    expect(universe.ok && universe.request).toMatchObject({ symbol: null, pair_symbol: null, universe: 'us_15' })
    expect(universe.ok && universe.request.strategies.map((s) => s.strategy)).toEqual([
      'Cross-Sectional Reversal',
      'Cross-Sectional Momentum',
    ])
    const one = checkCompare(form({ compareOn: 'universe', compareBaskets: ['Cross-Sectional Momentum', 'SMA Crossover'] }), us)
    expect(!one.ok && one.errors.strategies).toBe('Pick 2 to 8 strategies (1 picked)')
    // a registry without cross-sectional strategies has nothing to compare on a universe
    const plain = { ...us, strategies: strategies.filter((s) => !isBasket(s.name)) }
    expect(resolveCompareOn(form({ compareOn: 'universe' }), plain.strategies)).toBe('stock')
    expect(compareSelection(form({ compareOn: 'universe' }), plain.strategies)).toEqual(['SMA Crossover', 'Bollinger Bands', 'Z-Score Mean Reversion'])
  })

  it('sends only the chosen method’s settings', () => {
    const wf = checkOptimize(form({ nSplits: '5', trainPct: '70' }), us)
    expect(wf.ok && wf.request).toMatchObject({ method: 'walk_forward', n_splits: 5, objective: 'sharpe_ratio' })
    expect(wf.ok && wf.request.train_pct).toBeCloseTo(0.7)
    const grid = checkOptimize(form({ method: 'grid', maxCombinations: '3', nSplits: 'x' }), us)
    expect(!grid.ok && grid.errors).toEqual({ max_combinations: 'Between 5 and 2000' })
  })
})

describe('remembered inputs', () => {
  it('round-trips through localStorage, per view', () => {
    saveForm('backtest', form({ capital: '250000', strategy: 'SMA Crossover' }))
    expect(loadForm('backtest', TODAY)).toMatchObject({ capital: '250000', strategy: 'SMA Crossover' })
    expect(loadForm('compare', TODAY).capital).toBe('100000')
    saveForm('compare', form({ compareOn: 'universe', compare: ['RSI', 'MACD'], compareBaskets: ['Cross-Sectional Reversal'] }))
    expect(loadForm('compare', TODAY)).toMatchObject({
      compareOn: 'universe',
      compare: ['RSI', 'MACD'],
      compareBaskets: ['Cross-Sectional Reversal'],
    })
  })

  it('falls back to the defaults for anything unreadable', () => {
    window.localStorage.setItem(formKey('backtest'), '{not json')
    expect(loadForm('backtest', TODAY)).toEqual(defaultForm(TODAY))
    window.localStorage.setItem(
      formKey('backtest'),
      JSON.stringify({ capital: 5, timeframe: '4h', risk: { sizing: 'martingale' }, compareOn: 'both', compareBaskets: 'all' }),
    )
    const f = loadForm('backtest', TODAY)
    expect(f.capital).toBe('100000')
    expect(f.timeframe).toBe('1h')
    expect(f.risk.sizing).toBe('fixed_quantity')
    expect(f.compareOn).toBe('stock')
    expect(f.compareBaskets).toBeNull()
  })

  it('drops a stored symbol that no longer has data', () => {
    const c = resolveChoice({ symbol: 'US.GONE', pairSymbol: 'US.AAPL', universe: 'nope', commissionPct: null }, 'US', us.caps)
    expect(c).toEqual({ symbol: 'US.AAPL', pairSymbol: 'US.JPM', universe: 'us_15', commissionPct: '0.005' })
  })
})

describe('serverFieldErrors', () => {
  it('keys a 422 the way the form does', () => {
    expect(
      serverFieldErrors(
        [
          { loc: ['body', 'risk', 'qty'], msg: 'Input should be greater than 0' },
          { loc: ['body', 'strategies', 1, 'params', 'num_std'], msg: 'Value error, too wide' },
          { loc: ['body', 'strategies'], msg: 'List should have at most 8 items' },
          { loc: ['body'], msg: 'Field required' },
        ],
        ['SMA Crossover', 'Bollinger Bands'],
      ),
    ).toEqual({
      'risk.qty': 'Input should be greater than 0',
      'strategies.Bollinger Bands.num_std': 'too wide',
      strategies: 'List should have at most 8 items',
      '': 'Field required',
    })
  })
})

describe('detailFieldErrors', () => {
  const sent = { strategies: ['RSI'], compare: false, symbol: 'US.AAPL', pairSymbol: null }
  it('places the API’s one-sentence 422s beside the field they name', () => {
    expect(detailFieldErrors('symbol: US.X is not a US symbol', sent)).toEqual({ symbol: 'US.X is not a US symbol' })
    expect(detailFieldErrors('RSI: rsi_period must be between 2 and 50, not 80', sent)).toEqual({
      'params.rsi_period': 'must be between 2 and 50, not 80',
    })
    expect(
      detailFieldErrors('RSI: rsi_period must be a whole number, not 2.5', { ...sent, compare: true }),
    ).toEqual({ 'strategies.RSI.rsi_period': 'must be a whole number, not 2.5' })
    expect(detailFieldErrors('risk.qty must be at least 1', sent)).toEqual({ 'risk.qty': 'must be at least 1' })
    expect(detailFieldErrors('start (2026-10-01) is after end (2026-01-01)', sent)).toEqual({
      end: 'start (2026-10-01) is after end (2026-01-01)',
    })
    expect(detailFieldErrors('no cached 1d candles for US.AAPL', sent)).toEqual({ symbol: 'no cached 1d candles for US.AAPL' })
    expect(detailFieldErrors("unknown param 'x' for RSI", sent)).toEqual({ '': "unknown param 'x' for RSI" })
  })

  it('places the basket checks beside the universe and the strategies', () => {
    const basket = { strategies: ['Cross-Sectional Reversal'], compare: false, symbol: null, pairSymbol: null }
    expect(
      detailFieldErrors('universe: Cross-Sectional Reversal holds 3 of the stocks it ranks, so it needs more than 3 to rank; x has 2', basket),
    ).toEqual({ universe: 'Cross-Sectional Reversal holds 3 of the stocks it ranks, so it needs more than 3 to rank; x has 2' })
    expect(detailFieldErrors('strategies: cross-sectional strategies (A) rank a universe', { ...basket, compare: true })).toEqual({
      strategies: 'cross-sectional strategies (A) rank a universe',
    })
  })
})
