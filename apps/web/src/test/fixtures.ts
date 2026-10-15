/**
 * Mock API responses, typed from the generated schema: if the contract changes, these stop
 * compiling, which is the point.
 */
import type {
  AccountInfo,
  AccountView,
  BacktestRequest,
  BacktestResult,
  CompareResult,
  ForwardTest,
  Health,
  Job,
  JobCapabilities,
  OptimizeResult,
  Overview,
  ResearchFile,
  ResearchFileContent,
  SavedRun,
  SavedRunDetail,
  SavedRunTable,
  SessionDetail,
  SessionSummary,
  StrategyInfo,
  Universe,
} from '@/api/types'

export const health: Health = {
  status: 'ok',
  version: '0.1.0',
  sources: { results: true, live_sessions: true, data_cache: false },
}

export const accounts: AccountInfo[] = [
  { id: 'moomoo_us', label: 'Moomoo paper US', connector: 'moomoo', available: true, reason: '', kind: 'live' },
  {
    id: 'moomoo_hk',
    label: 'Moomoo paper HK',
    connector: 'moomoo',
    available: false,
    reason: 'OpenD is not running on 127.0.0.1:11111',
    kind: 'live',
  },
  { id: 'binance_spot', label: 'Binance spot', connector: 'binance', available: true, reason: '', kind: 'live' },
]

export const accountView: AccountView = {
  id: 'moomoo_us',
  label: 'Moomoo paper US',
  connector: 'moomoo',
  as_of: '2026-10-16T13:30:00Z',
  snapshot: { currency: 'USD', total_assets: 1001234.56, cash: 901234.56, market_value: 100000 },
  positions: [
    {
      symbol: 'US.AAPL',
      quantity: 120,
      average_price: 187.5,
      market_price: 190.25,
      market_value: 22830,
      unrealized_pnl: 330,
      instrument_id: 'US.AAPL',
    },
    {
      symbol: 'US.MSFT',
      quantity: 50,
      average_price: null, // exchange spot holdings carry no cost basis
      market_price: 410,
      market_value: 20500,
      unrealized_pnl: null,
      instrument_id: 'US.MSFT',
    },
  ],
  orders: [
    {
      order_id: 'o-1',
      symbol: 'US.AAPL',
      side: 'BUY',
      quantity: 120,
      filled_quantity: 120,
      price: 187.6,
      average_fill_price: 187.5,
      status: 'FILLED_ALL',
      created: '2026-10-14T13:31:00Z',
      updated: '2026-10-14T13:31:02Z',
    },
  ],
}

export const forwardTest: ForwardTest = {
  market: 'US',
  capital: 100000,
  pnl: 1234.4,
  pnl_pct: 1.234,
  value: 101234.4,
  sessions: 12,
  rebalances: 4,
  orders: 18,
  realised: -56.7,
  started: '20260915',
  first_trade: '20260917',
  as_of: '2026-10-15T20:00:00Z',
  equity: [
    { timestamp: '2026-09-15T13:30:00Z', pnl: 0, value: 100000 },
    { timestamp: '2026-09-15T20:00:00Z', pnl: -120, value: 99880 },
    { timestamp: '2026-10-15T20:00:00Z', pnl: 1234.4, value: 101234.4 },
  ],
  trades: [
    {
      date: '2026-09-17',
      decided_on_candle: '2026-09-17 10:30',
      side: 'BUY',
      stock: 'AAPL',
      shares: 1200,
      price: 187.5,
      value: 225000,
      realised_pnl: null,
      realised_pct: null,
    },
    {
      date: '2026-09-22',
      decided_on_candle: '2026-09-22 11:30',
      side: 'SELL',
      stock: 'AAPL',
      shares: 1200,
      price: 188.53,
      value: 226236,
      realised_pnl: 1236.4,
      realised_pct: 0.549,
    },
    {
      date: '2026-10-01',
      decided_on_candle: '2026-10-01 14:30',
      side: 'SELL',
      stock: 'MSFT',
      shares: 30,
      price: 405.1,
      value: 12153,
      realised_pnl: -56.7,
      realised_pct: -0.46,
    },
  ],
  holdings: [
    {
      stock: 'NVDA',
      shares: 80,
      bought_at: 120.5,
      last_price: 125.75,
      value: 10060,
      unrealised_pnl: 420,
      unrealised_pct: 4.357,
    },
  ],
}

export const sessions: SessionSummary[] = [
  {
    id: 'session_20261015_213000_CrossSectionalReversal_US',
    market: 'US',
    strategy: 'CrossSectionalReversal',
    timeframe: '1h',
    symbols: 98,
    started_at: '2026-10-15T13:30:00Z',
    ended_at: '2026-10-15T20:00:00Z',
    candles: 686,
    trades: 6,
    crashed: false,
    start_total_assets: 1000950,
    end_total_assets: 1001234.4,
  },
  {
    id: 'session_20261014_213000_CrossSectionalReversal_US',
    market: 'US',
    strategy: 'CrossSectionalReversal',
    timeframe: '1h',
    symbols: 98,
    started_at: '2026-10-14T13:30:00Z',
    ended_at: null,
    candles: 300,
    trades: 0,
    crashed: true,
    start_total_assets: 1000000,
    end_total_assets: null,
  },
]

export const sessionDetail: SessionDetail = {
  summary: sessions[0]!,
  equity: [
    { timestamp: '2026-10-15T13:30:00Z', pnl: 0, value: 1000950 },
    { timestamp: '2026-10-15T20:00:00Z', pnl: 284.4, value: 1001234.4 },
  ],
  trades: [
    { timestamp: '2026-10-15T14:30:00Z', symbol: 'US.NVDA', side: 'BUY', qty: 80, price: 120.5 },
  ],
  event_counts: { session_start: 1, candle_close: 686, order_filled: 6, session_end: 1 },
}

export const overview: Overview = {
  summary: { total: 3, passed: 1, studies: 2, families: 2, pass_rate: 33.3333 },
  rows: [
    {
      family: 'Cross-Sectional Reversal',
      scope: 'universe',
      strategy: 'CS-Reversal',
      subject: 'US S&P 100',
      config_a_oos: 6.15,
      config_a_consistency: 66.7,
      config_b_oos: 3.2,
      config_b_consistency: 60,
      worst_config: 3.2,
      robust: true,
      study: 'cross_sectional_corrected',
      file: 'cross_sectional_corrected_20260909_050515.csv',
    },
    {
      family: 'Cross-Sectional Reversal',
      scope: 'universe',
      strategy: 'CS-Reversal',
      subject: 'HK Hang Seng',
      config_a_oos: -1.17,
      config_a_consistency: 44.4,
      config_b_oos: 0.4,
      config_b_consistency: 53.3,
      worst_config: -1.17,
      robust: false,
      study: 'cross_sectional_corrected',
      file: 'cross_sectional_corrected_20260909_050515.csv',
    },
    {
      family: 'Single-Stock Strategies',
      scope: 'symbol',
      strategy: 'Z-Score Mean Reversion',
      subject: 'HK.00700',
      config_a_oos: -0.5,
      config_a_consistency: 33.3,
      config_b_oos: -0.2,
      config_b_consistency: 40,
      worst_config: -0.5,
      robust: false,
      study: 'best_tool_robust_v2_full',
      file: 'best_tool_robust_v2_full_20260909_040502.csv',
    },
  ],
  search_correction: {
    file: 'reality_check_20260929_050842.json',
    end: '2026-09-05',
    n_boot: 20000,
    configs: [
      {
        name: 'A (9 windows)',
        p_value: 0.09415,
        naive_p_value: 0.05755,
        best: 'CS-Reversal x sp100',
        best_mean: 6.147,
        n_windows: 9,
        n_candidates: 8,
        luck_p95: 7.2,
        means: { 'CS-Reversal x hk_live': -1.19, 'CS-Reversal x us_15': 1.22, 'CS-Reversal x sp100': 6.147 },
      },
      {
        name: 'B (15 windows)',
        p_value: 0.11,
        naive_p_value: 0.07,
        best: 'CS-Reversal x sp100',
        best_mean: 3.2,
        n_windows: 15,
        n_candidates: 8,
        luck_p95: 4.1,
        means: { 'CS-Reversal x hk_live': -0.8, 'CS-Reversal x sp100': 3.2 },
      },
    ],
    pbo: { pbo: 0.2738, n_trials: 252, n_configs: 96, n_subsets: 10 },
  },
}

export const researchFiles: ResearchFile[] = [
  {
    name: 'cross_sectional_corrected_20260909_050515.csv',
    study: 'cross_sectional_corrected',
    label: 'Cross-Sectional, corrected — both directions, S&P 100 + Hang Seng (current, headline)',
    kind: 'csv',
    size: 2048,
    modified: '2026-09-09T05:05:15Z',
  },
  {
    name: 'best_tool_robust_full_20260731_185704.csv',
    study: 'best_tool_robust_full',
    label: 'Full Comparison — Robust, 1yr Data (superseded)',
    kind: 'csv',
    size: 4096,
    modified: '2026-07-31T18:57:04Z',
  },
  {
    name: 'pbo_sp100_reversal_full96.json',
    study: 'pbo_sp100_reversal_full96',
    label: 'Pbo Sp100 Reversal Full96',
    kind: 'json',
    size: 512,
    modified: '2026-09-28T09:41:20Z',
  },
]

export const researchCsv: ResearchFileContent = {
  name: 'cross_sectional_corrected_20260909_050515.csv',
  kind: 'csv',
  columns: ['symbol', 'config_a_oos', 'robust'],
  rows: [
    { symbol: 'sp100', config_a_oos: 6.147, robust: true },
    { symbol: 'hsi', config_a_oos: -1.165, robust: false },
  ],
}

export const researchJson: ResearchFileContent = {
  name: 'pbo_sp100_reversal_full96.json',
  kind: 'json',
  data: { pbo: 0.2738, n_trials: 252, params: { lookback: [10, 20], top_n: 2 } },
}

// ── runs ────────────────────────────────────────────────────────────────────

export const capabilities: JobCapabilities = {
  kinds: ['backtest', 'compare', 'optimize'],
  max_concurrent: 1,
  objectives: {
    sharpe_ratio: 'Sharpe Ratio (higher is better)',
    return_pct: 'Total Return % (higher is better)',
    max_drawdown: 'Max Drawdown (lower is better — inverted for ranking)',
  },
  // as the API sends them: symbols with their cached timeframes, universes with their sizes
  markets: {
    US: {
      currency: 'USD',
      commission_rate: 0.00005,
      symbols: [
        { symbol: 'US.AAPL', timeframes: ['1h', '1d'] },
        { symbol: 'US.JPM', timeframes: ['1h'] },
        { symbol: 'US.MSFT', timeframes: ['1h', '1d'] },
        { symbol: 'US.NVDA', timeframes: ['1h'] },
      ],
      universes: [
        { name: 'us_15', label: 'US — 15 large caps (15)', description: 'Fifteen US large caps', symbols: 15, symbols_with_data: 15 },
        { name: 'sp100', label: 'US — S&P 100 (100)', description: 'The S&P 100 constituents', symbols: 100, symbols_with_data: 98 },
      ],
    },
    HK: {
      currency: 'HKD',
      commission_rate: 0.0016,
      symbols: [
        { symbol: 'HK.00005', timeframes: ['1h'] },
        { symbol: 'HK.00700', timeframes: ['1h', '1d'] },
        { symbol: 'HK.00941', timeframes: ['1h'] },
      ],
      universes: [
        { name: 'hk_live', label: 'HK — live roster (19)', description: 'The live HK roster', symbols: 19, symbols_with_data: 19 },
        { name: 'hsi', label: 'HK — Hang Seng Index (88)', description: 'Hang Seng Index constituents', symbols: 88, symbols_with_data: 88 },
      ],
    },
  },
}

/** A subset of the registry: whole-number and fractional parameters, a pair and a basket strategy. */
export const strategies: StrategyInfo[] = [
  {
    name: 'SMA Crossover',
    params: [
      { name: 'fast_period', label: 'Fast MA Period', default: 5, min: 2, max: 50, step: 1 },
      { name: 'slow_period', label: 'Slow MA Period', default: 20, min: 5, max: 200, step: 1 },
    ],
  },
  {
    name: 'Bollinger Bands',
    params: [
      { name: 'bb_period', label: 'BB Period', default: 20, min: 5, max: 50, step: 1 },
      { name: 'num_std', label: 'Std Deviations', default: 2, min: 1, max: 3.5, step: 0.1 },
    ],
  },
  {
    name: 'Z-Score Mean Reversion',
    params: [
      { name: 'lookback', label: 'Lookback Window', default: 20, min: 5, max: 100, step: 1 },
      { name: 'entry_z', label: 'Entry Z-Score', default: 2, min: 1, max: 3.5, step: 0.1 },
      { name: 'exit_z', label: 'Exit Z-Score', default: 0.5, min: 0, max: 1.5, step: 0.1 },
    ],
  },
  {
    name: 'Pairs Trading',
    params: [
      { name: 'lookback', label: 'Lookback Window', default: 30, min: 10, max: 100, step: 1 },
      { name: 'entry_z', label: 'Entry Z-Score', default: 2, min: 1, max: 3.5, step: 0.1 },
      { name: 'exit_z', label: 'Exit Z-Score', default: 0.5, min: 0, max: 1.5, step: 0.1 },
    ],
  },
  {
    name: 'Cross-Sectional Reversal',
    params: [
      { name: 'lookback', label: 'Lookback (candles)', default: 10, min: 5, max: 30, step: 5 },
      { name: 'top_n', label: 'Basket Size (# laggards held)', default: 2, min: 1, max: 4, step: 1 },
      { name: 'rebalance_every', label: 'Rebalance Every N Candles', default: 6, min: 1, max: 24, step: 6 },
    ],
  },
]

export const universes: Universe[] = [
  { name: 'hk_live', label: 'HK — live roster (19)', market: 'HK', symbols: 19, description: 'The live HK roster' },
  { name: 'us_15', label: 'US — 15 large caps (15)', market: 'US', symbols: 15, description: 'Fifteen US large caps' },
  { name: 'sp100', label: 'US — S&P 100 (100)', market: 'US', symbols: 100, description: 'The S&P 100 constituents' },
  { name: 'hsi', label: 'HK — Hang Seng Index (88)', market: 'HK', symbols: 88, description: 'Hang Seng Index constituents' },
]

export const backtestRequest: BacktestRequest = {
  market: 'US',
  symbol: 'US.AAPL',
  pair_symbol: null,
  universe: null,
  timeframe: '1h',
  start: '2025-10-16',
  end: '2026-10-16',
  initial_capital: 100000,
  commission_rate: 0.00005,
  slippage_bps: 0,
  risk: { max_drawdown_pct: 0.1, sizing: 'fixed_quantity', qty: 100, risk_pct: 0.02, fraction: 0.5, max_pct: 0.25 },
  strategy: 'Z-Score Mean Reversion',
  params: { lookback: 20, entry_z: 2, exit_z: 0.5 },
}

export const backtestResult: BacktestResult = {
  strategy: 'Z-Score Mean Reversion',
  symbols: ['US.AAPL'],
  params: { lookback: 20, entry_z: 2, exit_z: 0.5 },
  // the backtester's own keys, as the API passes them on
  metrics: {
    initial_cash: 100000,
    final_equity: 105430,
    return_pct: 5.43,
    total_trades: 3,
    cash_balance: 81430,
    open_positions: { 'US.AAPL': 100 },
    sharpe_ratio: 1.234,
    max_drawdown: 4.2,
    win_rate: 55,
    profit_factor: null, // infinite: no losing trade
    benchmark_return_pct: 8.1,
    alpha: -2.67,
    slippage_bps: 0,
    total_events: 1743,
  },
  equity: [
    { t: '2025-10-16T14:30:00Z', v: 100000 },
    { t: '2026-03-02T14:30:00Z', v: 98800 },
    { t: '2026-10-15T19:30:00Z', v: 105430 },
  ],
  trades: [
    { timestamp: '2025-11-03T15:30:00Z', symbol: 'US.AAPL', action: 'BUY', qty: 100, price: 220.5, commission: 1.1, pnl: null },
    { timestamp: '2025-11-20T15:30:00Z', symbol: 'US.AAPL', action: 'SELL', qty: 100, price: 231.25, commission: 1.16, pnl: 1072.74 },
    { timestamp: '2026-02-10T16:30:00Z', symbol: 'US.AAPL', action: 'BUY', qty: 100, price: 240, commission: 1.2, pnl: null },
  ],
  chart: {
    symbol: 'US.AAPL',
    candles: [
      { t: '2025-11-03T15:30:00Z', o: 220, h: 221, l: 219.5, c: 220.5, v: 1000 },
      { t: '2025-11-20T15:30:00Z', o: 230, h: 232, l: 229, c: 231.25, v: 1200 },
      { t: '2026-02-10T16:30:00Z', o: 239, h: 241, l: 238, c: 240, v: 900 },
    ],
    indicators: [
      { name: 'Mean (20)', pane: 'price', points: [{ t: '2025-11-20T15:30:00Z', v: 226 }] },
      { name: 'Z-score', pane: 'lower', points: [{ t: '2025-11-20T15:30:00Z', v: 1.7 }] },
    ],
  },
}

export const compareResult: CompareResult = {
  rows: [
    {
      strategy: 'SMA Crossover',
      params: { fast_period: 5, slow_period: 20 },
      metrics: { return_pct: 3.1, alpha: -5, sharpe_ratio: 0.8, max_drawdown: 6.5, win_rate: 48, total_trades: 40, final_equity: 103100 },
    },
    {
      strategy: 'Z-Score Mean Reversion',
      params: { lookback: 20, entry_z: 2, exit_z: 0.5 },
      metrics: { return_pct: 5.43, alpha: -2.67, sharpe_ratio: 1.23, max_drawdown: 4.2, win_rate: 55, total_trades: 12, final_equity: 105430 },
    },
    {
      strategy: 'Bollinger Bands',
      params: { bb_period: 20, num_std: 2 },
      metrics: { return_pct: -1.2, alpha: -9.3, sharpe_ratio: -0.2, max_drawdown: 9.9, win_rate: 61, total_trades: 22, final_equity: 98800 },
    },
  ],
  equity: {
    'SMA Crossover': [
      { t: '2025-10-16T14:30:00Z', v: 100000 },
      { t: '2026-10-15T19:30:00Z', v: 103100 },
    ],
    'Z-Score Mean Reversion': [
      { t: '2025-10-16T14:30:00Z', v: 100000 },
      { t: '2026-10-15T19:30:00Z', v: 105430 },
    ],
    'Bollinger Bands': [
      { t: '2025-10-16T14:30:00Z', v: 100000 },
      { t: '2026-10-15T19:30:00Z', v: 98800 },
    ],
  },
}

const grid = (lookback: number, top_n: number, sharpe: number, ret: number) => ({
  params: { lookback, top_n, rebalance_every: 6 },
  metrics: {
    return_pct: ret,
    sharpe_ratio: sharpe,
    max_drawdown: 5 - sharpe,
    win_rate: 50,
    profit_factor: sharpe > 1.5 ? null : 1.2, // null: infinite
    total_trades: 30,
    alpha: ret - 2,
    _objective: sharpe, // the value ranked on
  },
})

export const gridResult: OptimizeResult = {
  method: 'grid',
  objective: 'sharpe_ratio',
  // best first, as the API sends it
  rows: [grid(10, 2, 1.9, 7.5), grid(5, 2, 1.4, 5.2), grid(10, 1, 0.6, 2.1), grid(5, 1, -0.3, -1.4)],
  windows: [],
  summary: { grid_size: 96, combinations: 4, objective_label: 'Sharpe Ratio (higher is better)' },
}

export const walkForwardResult: OptimizeResult = {
  method: 'walk_forward',
  objective: 'sharpe_ratio',
  rows: [],
  windows: [
    {
      index: 1,
      train_start: '2025-10-16T00:00:00',
      train_end: '2026-01-27T00:00:00',
      test_start: '2026-01-27T00:00:00',
      test_end: '2026-04-05T00:00:00',
      params: { lookback: 10, top_n: 2, rebalance_every: 6 },
      train_objective: 1.82,
      test_metrics: { return_pct: 2.4, sharpe_ratio: 1.1 },
    },
    {
      index: 2,
      train_start: '2026-01-27T00:00:00',
      train_end: '2026-05-10T00:00:00',
      test_start: '2026-05-10T00:00:00',
      test_end: '2026-07-18T00:00:00',
      params: { lookback: 5, top_n: 2, rebalance_every: 6 },
      train_objective: 2.05,
      test_metrics: { return_pct: -0.9, sharpe_ratio: -0.4 },
    },
    {
      index: 3,
      train_start: '2026-05-10T00:00:00',
      train_end: '2026-08-21T00:00:00',
      test_start: '2026-08-21T00:00:00',
      test_end: '2026-10-16T00:00:00',
      params: { lookback: 10, top_n: 1, rebalance_every: 6 },
      train_objective: 1.4,
      test_metrics: { return_pct: 1.5, sharpe_ratio: 0.7 },
    },
  ],
  summary: {
    avg_oos_return: 1.0,
    avg_train_return: 4.6,
    consistency_pct: 66.67,
    wf_efficiency: 0.42,
    train_returns: [5.1, 6.3, 2.4],
    oos_returns: [2.4, -0.9, 1.5],
    objective_label: 'Sharpe Ratio (higher is better)',
  },
}

function job(over: Partial<Job> & Pick<Job, 'id' | 'kind'>): Job {
  return {
    status: 'done',
    progress: 1,
    message: '',
    created: '2026-10-16T01:00:00Z',
    started: '2026-10-16T01:00:01Z',
    finished: '2026-10-16T01:00:09Z',
    error: null,
    request: { ...backtestRequest },
    backtest: null,
    compare: null,
    optimize: null,
    run_id: null,
    ...over,
  }
}

export const backtestJob = job({ id: 'bt-1', kind: 'backtest', backtest: backtestResult, run_id: '20261016T010009_backtest_a1b2c3' })
export const compareJob = job({
  id: 'cmp-1',
  kind: 'compare',
  request: { ...backtestRequest, strategy: undefined, strategies: compareResult.rows.map((r) => ({ strategy: r.strategy, params: r.params })) },
  compare: compareResult,
})
export const gridJob = job({
  id: 'opt-grid',
  kind: 'optimize',
  request: { ...backtestRequest, strategy: 'Cross-Sectional Reversal', universe: 'sp100', method: 'grid', objective: 'sharpe_ratio' },
  optimize: gridResult,
})
export const walkForwardJob = job({
  id: 'opt-wf',
  kind: 'optimize',
  created: '2026-10-16T00:30:00Z',
  request: { ...backtestRequest, strategy: 'Cross-Sectional Reversal', universe: 'sp100', method: 'walk_forward', objective: 'sharpe_ratio' },
  optimize: walkForwardResult,
})
export const failedJob = job({
  id: 'bt-failed',
  kind: 'backtest',
  status: 'failed',
  created: '2026-10-16T00:10:00Z',
  error: 'No cached 1d candles for US.JPM between 2025-10-16 and 2026-10-16',
})

/** GET /api/jobs: newest first, without results. */
export const jobList: Job[] = [backtestJob, compareJob, gridJob, walkForwardJob, failedJob].map((j) => ({
  ...j,
  backtest: null,
  compare: null,
  optimize: null,
}))

export const jobsById: Record<string, Job> = Object.fromEntries(
  [backtestJob, compareJob, gridJob, walkForwardJob, failedJob].map((j) => [j.id, j]),
)

// ── saved runs (Run history) ────────────────────────────────────────────────

export const savedIds = {
  backtest: '20261016T010009_backtest_a1b2c3',
  compare: '20261016T005500_compare_c0ffee',
  grid: '20261016T004000_grid_search_9f8e7d',
  walkForward: '20261015T203710_walk_forward_d88c7d',
  hk: '20261014T090000_backtest_0a0b0c',
}

/** GET /api/runs: newest first. */
export const savedRuns: SavedRun[] = [
  {
    run_id: savedIds.backtest,
    created: '2026-10-16T01:00:09Z',
    kind: 'backtest',
    source: 'api',
    strategy: 'Z-Score Mean Reversion',
    universe: 'US.AAPL',
    n_symbols: 1,
    start: '2025-10-16',
    end: '2026-10-16',
    git_commit: '1f2639af67e60aac48491155fba8f50742491f5c',
    metrics: { return_pct: 5.43, sharpe_ratio: 1.234, max_drawdown: 4.2, total_trades: 3, win_rate: 55 },
  },
  {
    run_id: savedIds.compare,
    created: '2026-10-16T00:55:00Z',
    kind: 'compare',
    source: 'api',
    strategy: 'SMA Crossover, Z-Score Mean Reversion, Bollinger Bands',
    universe: 'US.AAPL',
    n_symbols: 1,
    start: '2025-10-16',
    end: '2026-10-16',
    git_commit: '1f2639af67e60aac48491155fba8f50742491f5c',
    metrics: { return_pct: 5.43, sharpe_ratio: 1.23, max_drawdown: 4.2, total_trades: 12, n_strategies: 3 },
  },
  {
    run_id: savedIds.grid,
    created: '2026-10-16T00:40:00Z',
    kind: 'grid_search',
    source: 'api',
    strategy: 'Cross-Sectional Reversal',
    universe: 'sp100',
    n_symbols: 100,
    start: '2025-10-16',
    end: '2026-10-16',
    git_commit: '1f2639af67e60aac48491155fba8f50742491f5c',
    metrics: { return_pct: 7.5, sharpe_ratio: 1.9, max_drawdown: 3.1, total_trades: 30, combinations: 4, grid_size: 96 },
  },
  {
    run_id: savedIds.walkForward,
    created: '2026-10-15T20:37:10Z',
    kind: 'walk_forward',
    source: 'script',
    strategy: 'Cross-Sectional Reversal',
    universe: 'sp100',
    n_symbols: 100,
    start: '2025-10-16',
    end: '2026-10-16',
    git_commit: '09e8914',
    metrics: { avg_oos_return: 1.0, avg_train_return: 4.6, consistency_pct: 66.67, wf_efficiency: 0.42, total_windows: 3 },
  },
  {
    run_id: savedIds.hk,
    created: '2026-10-14T09:00:00Z',
    kind: 'backtest',
    source: 'script',
    strategy: 'RSI',
    universe: 'HK.00700',
    n_symbols: 1,
    start: '2026-01-02',
    end: '2026-06-30',
    git_commit: null,
    metrics: { return_pct: -2.1, sharpe_ratio: -0.4, max_drawdown: 6.0, total_trades: 8 },
  },
]

const summaryOf = (id: string) => savedRuns.find((r) => r.run_id === id)!
const commonMeta = {
  market: 'US',
  currency: 'USD',
  timeframe: '1h',
  start: '2025-10-16',
  end: '2026-10-16',
  initial_capital: 100000,
  commission_rate: 0.00005,
  slippage_bps: 0,
  risk: { max_drawdown_pct: 0.1, sizing: 'fixed_quantity', qty: 100 },
  git_commit: '1f2639af67e60aac48491155fba8f50742491f5c',
}

/** GET /api/runs/{id}. */
export const savedRunDetails: Record<string, SavedRunDetail> = {
  [savedIds.backtest]: {
    summary: { ...summaryOf(savedIds.backtest), metrics: { ...backtestResult.metrics, open_positions: undefined } },
    meta: {
      ...commonMeta,
      source: 'api',
      job_id: 'bt-1',
      strategy: 'Z-Score Mean Reversion',
      params: backtestResult.params,
      symbols: ['US.AAPL'],
      universe: 'US.AAPL',
      data_cutoff: '2026-10-15T19:30:00+00:00',
      request: { ...backtestRequest },
    },
    tables: ['equity', 'trades'],
  },
  [savedIds.compare]: {
    summary: summaryOf(savedIds.compare),
    meta: {
      ...commonMeta,
      source: 'api',
      strategy: 'SMA Crossover, Z-Score Mean Reversion, Bollinger Bands',
      strategies: compareResult.rows.map((r) => r.strategy),
      symbols: ['US.AAPL'],
      request: { ...backtestRequest, strategies: compareResult.rows.map((r) => ({ strategy: r.strategy, params: r.params })) },
    },
    tables: ['strategies', 'equity'],
  },
  [savedIds.grid]: {
    summary: summaryOf(savedIds.grid),
    meta: {
      ...commonMeta,
      source: 'api',
      strategy: 'Cross-Sectional Reversal',
      method: 'grid',
      objective: 'sharpe_ratio',
      objective_label: 'Sharpe Ratio (higher is better)',
      best_params: gridResult.rows![0]!.params,
      max_combinations: 4,
    },
    tables: ['grid'],
  },
  [savedIds.walkForward]: {
    summary: summaryOf(savedIds.walkForward),
    meta: {
      ...commonMeta,
      source: 'script',
      strategy: 'Cross-Sectional Reversal',
      method: 'walk_forward',
      objective: 'sharpe_ratio',
      n_splits: 3,
      train_pct: 0.6,
      host: 'midfreq-vm',
    },
    tables: ['windows'],
  },
  [savedIds.hk]: {
    summary: summaryOf(savedIds.hk),
    meta: { source: 'script', strategy: 'RSI', market: 'HK', currency: 'HKD', symbols: ['HK.00700'] },
    tables: ['equity', 'trades', 'positions'],
  },
}

const table = (run_id: string, name: string, rows: Record<string, unknown>[], columns = Object.keys(rows[0] ?? {})): SavedRunTable => ({
  run_id,
  name,
  columns,
  rows,
  total: rows.length,
})

// the walk-forward as a research script saves it: params as columns, every train metric is_, every test metric oos_
const windowRows = walkForwardResult.windows!.map((w, i) => ({
  window: w.index,
  train_start: `${w.train_start.slice(0, 10)}T00:00:00+00:00`,
  train_end: `${w.train_end.slice(0, 10)}T00:00:00+00:00`,
  test_start: `${w.test_start.slice(0, 10)}T00:00:00+00:00`,
  test_end: `${w.test_end.slice(0, 10)}T00:00:00+00:00`,
  ...w.params,
  is_return_pct: [5.1, 6.3, 2.4][i],
  is_sharpe_ratio: w.train_objective,
  is_total_trades: 40,
  oos_return_pct: w.test_metrics.return_pct,
  oos_sharpe_ratio: w.test_metrics.sharpe_ratio,
  oos_total_trades: 12,
  oos_open_positions: '{"US.AAA": 10}',
}))

/** GET /api/runs/{id}/tables/{name} (json), by `${id}/${name}`. */
export const savedRunTables: Record<string, SavedRunTable> = {
  [`${savedIds.backtest}/equity`]: table(savedIds.backtest, 'equity', backtestResult.equity.map((p) => ({ t: p.t, equity: p.v }))),
  [`${savedIds.backtest}/trades`]: table(savedIds.backtest, 'trades', backtestResult.trades.map((t) => ({ ...t }))),
  [`${savedIds.compare}/strategies`]: table(
    savedIds.compare,
    'strategies',
    [...compareResult.rows]
      .sort((a, b) => (b.metrics.return_pct as number) - (a.metrics.return_pct as number))
      .map((r) => ({ label: r.strategy, strategy: r.strategy, params: JSON.stringify(r.params), n_symbols: 1, ...r.metrics })),
  ),
  [`${savedIds.compare}/equity`]: table(
    savedIds.compare,
    'equity',
    Object.entries(compareResult.equity).flatMap(([label, pts]) => pts.map((p) => ({ label, t: p.t, equity: p.v }))),
  ),
  [`${savedIds.grid}/grid`]: table(savedIds.grid, 'grid', gridResult.rows!.map((r) => ({ ...r.params, ...r.metrics }))),
  [`${savedIds.walkForward}/windows`]: table(savedIds.walkForward, 'windows', windowRows),
  [`${savedIds.hk}/equity`]: table(savedIds.hk, 'equity', [{ t: '2026-01-02T02:30:00+00:00', equity: 100000 }]),
  [`${savedIds.hk}/trades`]: table(savedIds.hk, 'trades', [], ['timestamp', 'symbol', 'action', 'qty', 'price', 'commission', 'cash_after']),
  [`${savedIds.hk}/positions`]: table(savedIds.hk, 'positions', [{ symbol: 'HK.00700', qty: 100 }]),
}
