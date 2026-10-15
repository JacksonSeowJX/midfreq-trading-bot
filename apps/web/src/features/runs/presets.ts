/**
 * Run presets: the settings of the guide's worked examples (docs/DASHBOARD_GUIDE.md), opened
 * by a link such as /backtest?preset=forward-test. A preset only fills in the form; the run
 * starts when Run is pressed. It starts from the defaults rather than the remembered inputs,
 * so the figures the guide quotes come out whatever was run before, and it sets the market.
 */
import type { JobKind } from '@/api/types'
import type { Market } from '@/lib/preferences'
import { defaultForm, type MarketChoice, type RunForm } from './form'
import { KIND_PATH } from './logic'

export interface RunPreset {
  id: string
  kind: JobKind
  /** what the form says was filled in, after "Filled in from the guide:" */
  title: string
  market: Market
  form: (today?: Date) => RunForm
}

// The last full year of the price cache when the guide was written (its last candle is
// 25 September 2026), so the guide's figures can be reproduced exactly.
const PERIOD = { start: '2025-09-25', end: '2026-09-25' }

// The live forward test's settings (config/sp100_forward_test.json).
const LIVE = 'Cross-Sectional Reversal'
const LIVE_PARAMS = { lookback: '30', top_n: '3', rebalance_every: '19' }

/** A US choice: the chart symbol, a universe for basket strategies, the commission in percent. */
function us(patch: Partial<MarketChoice>): Partial<Record<Market, MarketChoice>> {
  return { US: { symbol: 'US.AAPL', pairSymbol: '', universe: '', commissionPct: '0.005', ...patch } }
}

export const PRESETS: RunPreset[] = [
  {
    id: 'forward-test',
    kind: 'backtest',
    title: 'the live forward test’s settings, Cross-Sectional Reversal on the S&P 100 at the US commission of 0.005% per side',
    market: 'US',
    form: (today) => ({
      ...defaultForm(today),
      ...PERIOD,
      strategy: LIVE,
      params: { [LIVE]: { ...LIVE_PARAMS } },
      markets: us({ universe: 'sp100' }),
    }),
  },
  {
    id: 'forward-test-hk-fees',
    kind: 'backtest',
    title: 'the live forward test’s settings at the Hong Kong commission of 0.16% per side',
    market: 'US',
    form: (today) => ({
      ...defaultForm(today),
      ...PERIOD,
      strategy: LIVE,
      params: { [LIVE]: { ...LIVE_PARAMS } },
      markets: us({ universe: 'sp100', commissionPct: '0.160' }),
    }),
  },
  {
    id: 'oscillators',
    kind: 'compare',
    title: 'RSI, MACD and Bollinger Bands on US.AAPL with their default parameters',
    market: 'US',
    form: (today) => ({
      ...defaultForm(today),
      ...PERIOD,
      compare: ['RSI', 'MACD', 'Bollinger Bands'],
      markets: us({}),
    }),
  },
  {
    id: 'walk-forward',
    kind: 'optimize',
    title: 'a walk-forward of Cross-Sectional Reversal on the 15 US large caps, 3 windows, 60% training',
    market: 'US',
    form: (today) => ({
      ...defaultForm(today),
      ...PERIOD,
      strategy: LIVE,
      markets: us({ universe: 'us_15' }),
      method: 'walk_forward',
      objective: 'sharpe_ratio',
      nSplits: '3',
      trainPct: '60',
    }),
  },
]

/** The preset `id` for this kind of run, if there is one. */
export function findPreset(kind: JobKind, id: string | null | undefined): RunPreset | undefined {
  return id ? PRESETS.find((p) => p.id === id && p.kind === kind) : undefined
}

/** The page a preset opens, e.g. "/backtest?preset=forward-test". */
export function presetPath(preset: Pick<RunPreset, 'id' | 'kind'>): string {
  return `${KIND_PATH[preset.kind]}?preset=${encodeURIComponent(preset.id)}`
}
