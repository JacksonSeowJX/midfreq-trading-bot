import { describe, expect, it } from 'vitest'
import type { JobCapabilities } from '@/api/types'
import { backtestResult, capabilities, compareJob, compareResult, gridJob, gridResult, universes, walkForwardResult } from '@/test/fixtures'
import {
  bestPerColumn,
  compareLines,
  entityOrder,
  forTimeframe,
  gridHeatmap,
  headline,
  marketCaps,
  objectiveValue,
  runSummary,
  tradeMarkers,
  trainReturns,
  walkForwardSummary,
} from './logic'

describe('marketCaps', () => {
  it('reads the API’s market entry: symbols with timeframes, universes with sizes', () => {
    const us = marketCaps(capabilities, 'US', universes)
    expect(us.currency).toBe('USD')
    expect(us.commissionRate).toBe(0.00005)
    expect(us.symbols).toEqual(['US.AAPL', 'US.JPM', 'US.MSFT', 'US.NVDA'])
    expect(us.universes[1]).toEqual({
      name: 'sp100',
      label: 'US — S&P 100 (100)',
      symbols: 100,
      withData: 98,
      description: 'The S&P 100 constituents',
    })
    expect(forTimeframe(us, '1d').symbols).toEqual(['US.AAPL', 'US.MSFT'])
    expect(forTimeframe(us, '1h')).toBe(us)
  })

  it('accepts plain names too, labelling universes from /api/universes', () => {
    const plain: JobCapabilities = { ...capabilities, markets: { HK: { symbols: ['HK.00700'], universes: ['hsi'] } } }
    const hk = marketCaps(plain, 'HK', universes)
    expect(hk).toMatchObject({ currency: 'HKD', commissionRate: null, symbols: ['HK.00700'] })
    expect(hk.universes[0]).toMatchObject({ name: 'hsi', label: 'HK — Hang Seng Index (88)', symbols: 88, withData: null })
    expect(marketCaps(undefined, 'US').symbols).toEqual([])
  })
})

describe('headline', () => {
  it('reads the backtester’s keys, and an infinite profit factor sent as null', () => {
    expect(headline(backtestResult.metrics, backtestResult.equity)).toMatchObject({
      returnPct: 5.43,
      maxDrawdownPct: 4.2,
      winRatePct: 55,
      trades: 3,
      finalValue: 105430,
      profitFactor: null,
      profitFactorInfinite: true,
    })
    // a missing profit factor is unknown, not infinite; the last equity point stands in for final_equity
    expect(headline({}, [{ v: 99 }])).toMatchObject({ profitFactorInfinite: false, finalValue: 99 })
  })
})

describe('compare', () => {
  it('marks the best value per column, lowest for drawdown', () => {
    const best = bestPerColumn(compareLines(compareResult))
    expect(best.get('returnPct')).toBe(5.43)
    expect(best.get('maxDrawdownPct')).toBe(4.2)
    expect(best.get('winRatePct')).toBe(61)
    expect(best.has('trades')).toBe(false)
  })
})

describe('entityOrder', () => {
  it('puts compare rows in the order asked for, with the API’s #n labels for repeats', () => {
    expect(entityOrder(['RSI #2', 'MACD', 'RSI #1'], ['RSI', 'MACD', 'RSI'])).toEqual(['RSI #1', 'MACD', 'RSI #2'])
    expect(entityOrder(['B', 'A', 'X'], ['A', 'B'])).toEqual(['A', 'B', 'X'])
  })
})

describe('grid search', () => {
  it('maps the ranked objective over the two parameters that vary', () => {
    const heat = gridHeatmap(gridResult.rows ?? [], 'sharpe_ratio')
    expect(heat).toMatchObject({ x: 'lookback', y: 'top_n', xValues: ['5', '10'], yValues: ['1', '2'], min: -0.3, max: 1.9 })
    expect(heat?.cells).toContainEqual([1, 1, 1.9]) // lookback 10, top_n 2
  })

  it('ranks on _objective, else the metric (a drawdown negated)', () => {
    expect(objectiveValue({ metrics: { _objective: -3, max_drawdown: 3 } }, 'max_drawdown')).toBe(-3)
    expect(objectiveValue({ metrics: { max_drawdown: 3 } }, 'max_drawdown')).toBe(-3)
    expect(objectiveValue({ metrics: { sharpe_ratio: 1.2 } }, 'sharpe_ratio')).toBe(1.2)
  })
})

describe('walk-forward', () => {
  it('takes the optimizer’s summary, and each window’s train return from it', () => {
    const w = walkForwardResult.windows ?? []
    expect(walkForwardSummary(w, walkForwardResult.summary)).toEqual({
      meanTestReturn: 1,
      meanTrainReturn: 4.6,
      positiveShare: 66.67,
      efficiency: 0.42,
      windows: 3,
    })
    expect(trainReturns(w, walkForwardResult.summary)).toEqual([5.1, 6.3, 2.4])
  })

  it('computes the mean and the positive share itself when the summary lacks them', () => {
    const s = walkForwardSummary(walkForwardResult.windows ?? [])
    expect(s.meanTestReturn).toBeCloseTo((2.4 - 0.9 + 1.5) / 3)
    expect(s.positiveShare).toBeCloseTo(66.667, 2)
    expect(s.meanTrainReturn).toBeNull()
  })
})

describe('tradeMarkers', () => {
  it('keeps the charted symbol’s buys and sells, in time order', () => {
    const trades = [
      ...backtestResult.trades,
      { timestamp: '2025-10-20T15:30:00Z', symbol: 'US.MSFT', action: 'BUY', qty: 1, price: 1, commission: 0 },
      { timestamp: '2025-10-01T15:30:00Z', symbol: 'US.AAPL', action: 'sell (stop_loss)', qty: 1, price: 1, commission: 0 },
    ]
    expect(tradeMarkers(trades, 'US.AAPL').map((m) => m.side)).toEqual(['sell', 'buy', 'sell', 'buy'])
  })
})

describe('runSummary', () => {
  it('describes a run from its request', () => {
    expect(runSummary(compareJob)).toMatchObject({ title: '3 strategies', currency: 'USD', initialCapital: 100000 })
    expect(runSummary(gridJob)).toMatchObject({
      title: 'Cross-Sectional Reversal',
      detail: 'sp100 · 1h · 2025-10-16 → 2026-10-16 · grid search',
    })
    expect(runSummary({ kind: 'backtest', request: { market: 'HK' } })).toMatchObject({ title: 'Backtest', currency: 'HKD', detail: '' })
  })
})
