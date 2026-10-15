/**
 * Mock API responses, typed from the generated schema: if the contract changes, these stop
 * compiling, which is the point.
 */
import type {
  AccountInfo,
  AccountView,
  ForwardTest,
  Health,
  Overview,
  ResearchFile,
  ResearchFileContent,
  SessionDetail,
  SessionSummary,
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
