/**
 * Pure functions behind Run history: what to call a saved run and its headline figures, the
 * list's filters, and a saved run's tables turned back into the models the run pages' result
 * views take. Tables come in the shapes the API saves a job in and the shapes scripts save
 * (midfreq.backtest.runstore's record_from_* helpers), and every read is defensive: a table
 * is free-form in the contract.
 */
import type {
  BacktestResult,
  CompareResult,
  GridRow,
  OptimizeResult,
  SavedRun,
  SavedRunDetail,
  SavedRunKind,
  SavedRunTable,
  SeriesPoint,
  TradeOut,
  WalkForwardWindow,
} from '@/api/types'
import { currencyFor, fmtCalendarDate } from '@/lib/format'
import type { Market } from '@/lib/preferences'
import { metric, num, requestedStrategies, type RunSummary } from '@/features/runs/logic'

type Row = Record<string, unknown>

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

export const SAVED_RUN_KINDS: SavedRunKind[] = ['backtest', 'compare', 'grid_search', 'walk_forward']

export const SAVED_KIND_LABEL: Record<SavedRunKind, string> = {
  backtest: 'Backtest',
  compare: 'Compare',
  grid_search: 'Grid search',
  walk_forward: 'Walk-forward',
}

/** Who saved a run (RunSummary.source). */
export const SOURCE_LABEL: Record<string, string> = { api: 'Dashboard', script: 'Script', cli: 'CLI' }

export function runPath(id: string): string {
  return `/runs/${encodeURIComponent(id)}`
}

// ── what a saved run is ─────────────────────────────────────────────────────

/** Its strategy (a comparison's strategies, joined), else what kind of run it is. */
export function savedRunTitle(run: Pick<SavedRun, 'kind' | 'strategy' | 'metrics'>): string {
  if (run.strategy) return run.strategy
  if (run.kind === 'compare') {
    const n = num(run.metrics?.n_strategies)
    return n ? `${n} strategies` : 'Compare'
  }
  return SAVED_KIND_LABEL[run.kind]
}

/** The universe or symbol it traded, else how many symbols. */
export function tradedText(run: Pick<SavedRun, 'universe' | 'n_symbols'>): string {
  if (run.universe) return run.universe
  if (run.n_symbols) return `${run.n_symbols} symbol${run.n_symbols === 1 ? '' : 's'}`
  return '—'
}

/** "05 Jan 2026" from a date or an ISO time. */
export function dayText(v: string | null | undefined): string {
  return v ? fmtCalendarDate(v.slice(0, 10)) : '—'
}

export function periodText(run: Pick<SavedRun, 'start' | 'end'>): string {
  return run.start || run.end ? `${dayText(run.start)} → ${dayText(run.end)}` : '—'
}

/** Which of the run's figures the list's Return shows. */
export type ReturnBasis = 'run' | 'best' | 'best-in-sample' | 'mean-out-of-sample'

export interface SavedHeadline {
  returnPct: number | null
  basis: ReturnBasis
  sharpe: number | null
  maxDrawdownPct: number | null
  trades: number | null
}

export const BASIS_NOTE: Record<ReturnBasis, string> = {
  run: 'the run',
  best: 'the best strategy',
  'best-in-sample': 'the best combination, in sample',
  'mean-out-of-sample': 'the mean out-of-sample return over the windows',
}

/**
 * The list's figures for any kind: a backtest's own; a comparison's best strategy; a grid
 * search's best combination (in sample, so flattering); a walk-forward's mean out-of-sample
 * return, the honest one.
 */
export function savedHeadline(run: Pick<SavedRun, 'kind' | 'metrics'>): SavedHeadline {
  const m = run.metrics ?? {}
  if (run.kind === 'walk_forward') {
    return {
      returnPct: metric(m, 'avg_oos_return', 'oos_return_pct'),
      basis: 'mean-out-of-sample',
      sharpe: metric(m, 'avg_oos_sharpe', 'oos_sharpe_ratio'),
      maxDrawdownPct: null,
      trades: null,
    }
  }
  return {
    returnPct: metric(m, 'return_pct'),
    basis: run.kind === 'compare' ? 'best' : run.kind === 'grid_search' ? 'best-in-sample' : 'run',
    sharpe: metric(m, 'sharpe_ratio'),
    maxDrawdownPct: metric(m, 'max_drawdown_pct', 'max_drawdown'),
    trades: metric(m, 'total_trades'),
  }
}

/** For the result views: what the run was, read from how it was run (meta). */
export function savedRunSummary(detail: SavedRunDetail): RunSummary {
  const meta = detail.meta
  const s = detail.summary
  const market: Market = meta.market === 'HK' || (s.universe ?? '').startsWith('HK') ? 'HK' : 'US'
  const parts = [tradedText(s), str(meta.timeframe) ?? '', s.start || s.end ? periodText(s) : ''].filter(Boolean)
  return {
    title: savedRunTitle(s),
    detail: parts.join(' · '),
    market,
    currency: str(meta.currency) ?? currencyFor(market),
    initialCapital: num(meta.initial_capital),
  }
}

// ── the list's filters ──────────────────────────────────────────────────────

export interface RunFilters {
  kind: SavedRunKind | 'all'
  source: string
  query: string
}

export const NO_FILTERS: RunFilters = { kind: 'all', source: 'all', query: '' }

export function filterSavedRuns(runs: SavedRun[], f: RunFilters): SavedRun[] {
  const q = f.query.trim().toLowerCase()
  return runs.filter((r) => {
    if (f.kind !== 'all' && r.kind !== f.kind) return false
    if (f.source !== 'all' && (r.source || 'unknown') !== f.source) return false
    if (!q) return true
    const text = [r.run_id, r.strategy, r.universe, r.source, SAVED_KIND_LABEL[r.kind], r.git_commit].filter(Boolean).join(' ')
    return text.toLowerCase().includes(q)
  })
}

export function countBy<K extends string>(runs: SavedRun[], key: (r: SavedRun) => K): Map<K, number> {
  const out = new Map<K, number>()
  for (const r of runs) out.set(key(r), (out.get(key(r)) ?? 0) + 1)
  return out
}

// ── a saved run's tables, as the result views take them ────────────────────

/** A JSON-text cell (a dict a store wrote as text) or an object, as an object. */
export function objectCell(v: unknown): Record<string, unknown> {
  if (isObj(v)) return v
  if (typeof v === 'string' && v.trim().startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(v)
      return isObj(parsed) ? parsed : {}
    } catch {
      return {}
    }
  }
  return {}
}

/** (t, equity) rows as a curve; rows without a time or a number are skipped. */
export function equityPoints(rows: Row[], time = 't', value = 'equity'): SeriesPoint[] {
  const out: SeriesPoint[] = []
  for (const r of rows) {
    const t = str(r[time])
    const v = num(r[value])
    if (t !== null && v !== null) out.push({ t, v })
  }
  return out
}

/** The trades table: the API's (with pnl) and the scripts' (Portfolio.trade_history, no pnl). */
export function tradesOf(rows: Row[]): TradeOut[] {
  const out: TradeOut[] = []
  for (const r of rows) {
    const timestamp = str(r.timestamp)
    const symbol = str(r.symbol)
    const action = str(r.action) ?? str(r.side)
    const qty = num(r.qty) ?? num(r.quantity)
    const price = num(r.price)
    if (!timestamp || !symbol || !action || qty === null || price === null) continue
    out.push({ timestamp, symbol, action, qty, price, commission: num(r.commission) ?? 0, pnl: num(r.pnl) })
  }
  return out
}

/** A backtest from its equity and trades tables (no price chart: it is not saved with the run). */
export function backtestFromTables(detail: SavedRunDetail, equity?: SavedRunTable, trades?: SavedRunTable): BacktestResult {
  const meta = detail.meta
  return {
    strategy: str(meta.strategy) ?? detail.summary.strategy ?? 'Backtest',
    symbols: Array.isArray(meta.symbols) ? meta.symbols.filter((s): s is string => typeof s === 'string') : [],
    params: objectCell(meta.params),
    metrics: detail.summary.metrics ?? {},
    equity: equity ? equityPoints(equity.rows) : [],
    trades: trades ? tradesOf(trades.rows) : [],
    chart: null,
  }
}

const COMPARE_ROW_COLUMNS = new Set(['label', 'strategy', 'params', 'n_symbols', 'rank'])

/** A comparison from its strategies table (one row per strategy) and its long equity table. */
export function compareFromTables(strategies: SavedRunTable, equity?: SavedRunTable): CompareResult {
  const rows = strategies.rows.map((r) => ({
    strategy: str(r.label) ?? str(r.strategy) ?? '?',
    params: objectCell(r.params),
    metrics: Object.fromEntries(Object.entries(r).filter(([k]) => !COMPARE_ROW_COLUMNS.has(k))),
  }))
  const curves: Record<string, SeriesPoint[]> = {}
  for (const r of equity?.rows ?? []) {
    const label = str(r.label) ?? str(r.strategy)
    const t = str(r.t)
    const v = num(r.equity)
    if (label === null || t === null || v === null) continue
    ;(curves[label] ??= []).push({ t, v })
  }
  return { rows, equity: curves }
}

/** The strategies in the order the run asked for them, so each keeps its colour. */
export function compareOrder(detail: SavedRunDetail): string[] {
  const asked = requestedStrategies(objectCell(detail.meta.request))
  if (asked.length) return asked
  return Array.isArray(detail.meta.strategies) ? detail.meta.strategies.filter((s): s is string => typeof s === 'string') : []
}

// grid_search's metric columns (midfreq.backtest.optimizer); the other columns are the parameters
const GRID_METRICS = new Set(['return_pct', 'sharpe_ratio', 'max_drawdown', 'max_drawdown_pct', 'win_rate', 'profit_factor', 'total_trades', 'alpha', '_objective'])

/** A grid search from its grid table (best first). */
export function gridFromTable(detail: SavedRunDetail, grid: SavedRunTable): OptimizeResult {
  const rows: GridRow[] = grid.rows.map((r) => {
    const params: Record<string, unknown> = {}
    const metrics: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(r)) (GRID_METRICS.has(k) ? metrics : params)[k] = v
    return { params, metrics }
  })
  const m = detail.summary.metrics ?? {}
  return {
    method: 'grid',
    objective: str(detail.meta.objective) ?? 'sharpe_ratio',
    rows,
    windows: [],
    summary: {
      grid_size: num(m.grid_size) ?? num(detail.meta.grid_size),
      combinations: num(m.combinations) ?? rows.length,
      objective_label: str(detail.meta.objective_label),
      best_params: objectCell(detail.meta.best_params),
    },
  }
}

const WINDOW_COLUMNS = new Set(['window', 'window_id', 'train_start', 'train_end', 'test_start', 'test_end'])

/**
 * A walk-forward from its windows table: the chosen parameters are the unprefixed columns
 * (param_<name> where a name was taken), is_<metric> in sample and oos_<metric> out of sample.
 */
export function walkForwardFromTable(detail: SavedRunDetail, windows: SavedRunTable): OptimizeResult {
  const objective = str(detail.meta.objective) ?? 'sharpe_ratio'
  const out: WalkForwardWindow[] = windows.rows.map((r, i) => {
    const params: Record<string, unknown> = {}
    const test: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(r)) {
      if (WINDOW_COLUMNS.has(k) || k.startsWith('is_')) continue
      if (k.startsWith('oos_')) test[k.slice(4)] = v
      else params[k.startsWith('param_') ? k.slice(6) : k] = v
    }
    return {
      index: num(r.window) ?? num(r.window_id) ?? i + 1,
      train_start: str(r.train_start) ?? '',
      train_end: str(r.train_end) ?? '',
      test_start: str(r.test_start) ?? '',
      test_end: str(r.test_end) ?? '',
      params,
      train_objective: num(r[`is_${objective}`]),
      test_metrics: test,
    }
  })
  const m = detail.summary.metrics ?? {}
  const keep = ['avg_oos_return', 'avg_train_return', 'consistency_pct', 'wf_efficiency', 'total_windows', 'n_windows']
  return {
    method: 'walk_forward',
    objective,
    rows: [],
    windows: out,
    summary: {
      ...Object.fromEntries(keep.filter((k) => k in m).map((k) => [k, m[k]])),
      train_returns: windows.rows.map((r) => num(r.is_return_pct)),
      objective_label: str(detail.meta.objective_label),
    },
  }
}

// ── walk-forward: in sample against out of sample ───────────────────────────

export interface SamplePair {
  /** the metric, without its prefix: return_pct */
  key: string
  inSample: (number | null)[]
  outOfSample: (number | null)[]
  meanIn: number | null
  meanOut: number | null
}

export interface SampleComparison {
  windows: { index: number; train: [string, string]; test: [string, string] }[]
  pairs: SamplePair[]
}

const PAIR_ORDER = ['return_pct', 'sharpe_ratio', 'max_drawdown', 'max_drawdown_pct', 'win_rate', 'profit_factor', 'total_trades', 'alpha']

function mean(values: (number | null)[]): number | null {
  const xs = values.filter((v): v is number => v !== null)
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
}

/**
 * Every metric the windows table has both in sample (is_) and out of sample (oos_), per window,
 * with the mean of each over the windows: the usual metrics first, then the rest by name.
 */
export function sampleComparison(windows: SavedRunTable): SampleComparison {
  const cols = new Set(windows.columns.length ? windows.columns : windows.rows.flatMap((r) => Object.keys(r)))
  const numeric = (col: string) => windows.rows.some((r) => num(r[col]) !== null)
  const keys = [...cols]
    .filter((c) => c.startsWith('is_') && cols.has(`oos_${c.slice(3)}`))
    .map((c) => c.slice(3))
    .filter((k) => numeric(`is_${k}`) || numeric(`oos_${k}`))
  const rank = (k: string) => (PAIR_ORDER.includes(k) ? PAIR_ORDER.indexOf(k) : PAIR_ORDER.length)
  keys.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
  return {
    windows: windows.rows.map((r, i) => ({
      index: num(r.window) ?? num(r.window_id) ?? i + 1,
      train: [str(r.train_start) ?? '', str(r.train_end) ?? ''],
      test: [str(r.test_start) ?? '', str(r.test_end) ?? ''],
    })),
    pairs: keys.map((key) => {
      const inSample = windows.rows.map((r) => num(r[`is_${key}`]))
      const outOfSample = windows.rows.map((r) => num(r[`oos_${key}`]))
      return { key, inSample, outOfSample, meanIn: mean(inSample), meanOut: mean(outOfSample) }
    }),
  }
}

/** Which tables a kind's result view needs, in the order it shows them. */
export const VIEW_TABLES: Record<SavedRunKind, string[]> = {
  backtest: ['equity', 'trades'],
  compare: ['strategies', 'equity'],
  grid_search: ['grid'],
  walk_forward: ['windows'],
}
