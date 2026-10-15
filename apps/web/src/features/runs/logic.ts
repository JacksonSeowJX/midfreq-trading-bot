/**
 * Pure functions behind the run views: reading the capabilities' free-form market entries,
 * the backtester's metrics, the best value per compare column, the grid search heatmap and
 * the walk-forward summary. The API leaves these as Dict[str, Any], so every read is defensive.
 */
import type {
  CompareResult,
  GridRow,
  Job,
  JobCapabilities,
  JobKind,
  TradeOut,
  Universe,
  WalkForwardWindow,
} from '@/api/types'
import { currencyFor, fmtNum, fmtPct } from '@/lib/format'
import type { Market } from '@/lib/preferences'

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

export function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

// ── capabilities ────────────────────────────────────────────────────────────

export interface UniverseOption {
  name: string
  label: string
  symbols: number | null
  /** how many of its symbols have cached (hourly) data */
  withData: number | null
  description: string
}

export interface MarketCaps {
  currency: string
  /** per side, as a fraction (0.0016 = 0.16%); null when the API does not say */
  commissionRate: number | null
  /** symbols with cached data */
  symbols: string[]
  /** symbol -> the timeframes it has cached; a symbol missing here has no list (any is assumed) */
  timeframes: Record<string, string[]>
  universes: UniverseOption[]
}

/**
 * One market's entry of /api/jobs/capabilities: {commission_rate, currency, symbols:
 * [{symbol, timeframes}], universes: [{name, label, description, symbols, symbols_with_data}]}.
 * Plain names are accepted for both lists too; labels and sizes missing from a universe are
 * filled in from /api/universes.
 */
export function marketCaps(caps: JobCapabilities | undefined, market: Market, universes: Universe[] = []): MarketCaps {
  const entry = caps?.markets[market] ?? {}
  const symbols: string[] = []
  const timeframes: Record<string, string[]> = {}
  for (const s of Array.isArray(entry.symbols) ? (entry.symbols as unknown[]) : []) {
    if (typeof s === 'string') symbols.push(s)
    else if (isObj(s) && typeof s.symbol === 'string') {
      symbols.push(s.symbol)
      if (Array.isArray(s.timeframes)) timeframes[s.symbol] = s.timeframes.filter((t): t is string => typeof t === 'string')
    }
  }
  const known = new Map(universes.map((u) => [u.name, u]))
  const option = (name: string, extra: Record<string, unknown> = {}): UniverseOption => {
    const u = known.get(name)
    return {
      name,
      label: typeof extra.label === 'string' ? extra.label : (u?.label ?? name),
      symbols: num(extra.symbols) ?? u?.symbols ?? null,
      withData: num(extra.symbols_with_data),
      description: typeof extra.description === 'string' ? extra.description : (u?.description ?? ''),
    }
  }
  let options: UniverseOption[]
  if (Array.isArray(entry.universes)) {
    options = entry.universes.flatMap((u: unknown) =>
      typeof u === 'string' ? [option(u)] : isObj(u) && typeof u.name === 'string' ? [option(u.name, u)] : [],
    )
  } else {
    options = universes.filter((u) => (u.market ?? '').toUpperCase() === market).map((u) => option(u.name))
  }
  return {
    currency: typeof entry.currency === 'string' ? entry.currency : currencyFor(market),
    commissionRate: num(entry.commission_rate),
    symbols,
    timeframes,
    universes: options,
  }
}

/** Only the symbols with cached candles at this timeframe (when the API lists timeframes). */
export function forTimeframe(caps: MarketCaps, timeframe: string): MarketCaps {
  const has = (s: string) => !caps.timeframes[s] || caps.timeframes[s].includes(timeframe)
  return caps.symbols.every(has) ? caps : { ...caps, symbols: caps.symbols.filter(has) }
}

// ── metrics ─────────────────────────────────────────────────────────────────

/** The first of `keys` that holds a finite number. */
export function metric(metrics: Record<string, unknown> | undefined, ...keys: string[]): number | null {
  if (!metrics) return null
  for (const k of keys) {
    const v = num(metrics[k])
    if (v !== null) return v
  }
  return null
}

/**
 * The backtester's headline figures. Percentages are in percent (return_pct 1.23 = 1.23%).
 * Max drawdown is a positive percent; the contract names it max_drawdown_pct, the backtester
 * max_drawdown, so both are read.
 */
/**
 * The API sends an infinite value (a profit factor with no losing trade) as null, so a key
 * that is present and null is infinite; a missing key is unknown.
 */
export function isInfinite(metrics: Record<string, unknown> | undefined, key: string): boolean {
  return !!metrics && key in metrics && metrics[key] === null
}

export interface Headline {
  returnPct: number | null
  sharpe: number | null
  maxDrawdownPct: number | null
  winRatePct: number | null
  trades: number | null
  finalValue: number | null
  /** null when infinite (no losing trade) or missing; see profitFactorInfinite */
  profitFactor: number | null
  profitFactorInfinite: boolean
  benchmarkPct: number | null
  alphaPct: number | null
}

export function headline(metrics: Record<string, unknown>, equity: { v: number }[] = []): Headline {
  return {
    returnPct: metric(metrics, 'return_pct'),
    sharpe: metric(metrics, 'sharpe_ratio', 'sharpe'),
    maxDrawdownPct: metric(metrics, 'max_drawdown_pct', 'max_drawdown'),
    winRatePct: metric(metrics, 'win_rate', 'win_rate_pct'),
    trades: metric(metrics, 'total_trades', 'trades'),
    finalValue: metric(metrics, 'final_equity', 'final_value') ?? num(equity.at(-1)?.v),
    profitFactor: metric(metrics, 'profit_factor'),
    profitFactorInfinite: isInfinite(metrics, 'profit_factor'),
    benchmarkPct: metric(metrics, 'benchmark_return_pct'),
    alphaPct: metric(metrics, 'alpha'),
  }
}

// ── compare ─────────────────────────────────────────────────────────────────

export type Better = 'higher' | 'lower' | null

/** The numeric fields of Headline. */
export type Figure = Exclude<keyof Headline, 'profitFactorInfinite'>

export interface MetricColumn {
  id: Figure
  label: string
  better: Better
}

export const COMPARE_COLUMNS: MetricColumn[] = [
  { id: 'returnPct', label: 'Return', better: 'higher' },
  { id: 'alphaPct', label: 'Alpha', better: 'higher' },
  { id: 'sharpe', label: 'Sharpe', better: 'higher' },
  { id: 'maxDrawdownPct', label: 'Max DD', better: 'lower' },
  { id: 'winRatePct', label: 'Win rate', better: 'higher' },
  { id: 'trades', label: 'Trades', better: null },
  { id: 'finalValue', label: 'Final value', better: 'higher' },
]

export interface CompareLine {
  strategy: string
  params: Record<string, unknown>
  figures: Headline
}

export function compareLines(result: CompareResult): CompareLine[] {
  return result.rows.map((r) => ({
    strategy: r.strategy,
    params: r.params,
    figures: headline(r.metrics, result.equity[r.strategy] ?? []),
  }))
}

/** The strategy names a run asked for, in the order asked (Compare's request.strategies). */
export function requestedStrategies(request: Record<string, unknown>): string[] {
  return Array.isArray(request.strategies)
    ? request.strategies.flatMap((s: unknown) => (isObj(s) && typeof s.strategy === 'string' ? [s.strategy] : []))
    : []
}

/**
 * Compare rows in the order the strategies were asked for, so each keeps its colour whatever
 * its rank. The API labels a strategy asked for twice "RSI #1", "RSI #2".
 */
export function entityOrder(rows: string[], requested: string[]): string[] {
  const position = (label: string) => {
    const m = / #(\d+)$/.exec(label)
    const base = m ? label.slice(0, m.index) : label
    let seen = 0
    for (let i = 0; i < requested.length; i++) {
      if (requested[i] === base && ++seen === (m ? Number(m[1]) : 1)) return i
    }
    return requested.length + rows.indexOf(label)
  }
  return [...rows].sort((a, b) => position(a) - position(b))
}

/** The best value of each column that has a direction (ties all count as best). */
export function bestPerColumn(lines: CompareLine[], columns: MetricColumn[] = COMPARE_COLUMNS): Map<string, number> {
  const best = new Map<string, number>()
  for (const col of columns) {
    if (!col.better) continue
    const values = lines.map((l) => l.figures[col.id]).filter((v): v is number => v !== null)
    if (values.length < 2) continue
    best.set(col.id, col.better === 'higher' ? Math.max(...values) : Math.min(...values))
  }
  return best
}

// ── optimize: grid search ───────────────────────────────────────────────────

/** "lookback=10, top_n=2". */
export function paramsText(params: Record<string, unknown>): string {
  const entries = Object.entries(params)
  return entries.length ? entries.map(([k, v]) => `${k}=${fmtValue(v)}`).join(', ') : '—'
}

export function fmtValue(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : fmtNum(v, Math.abs(v) < 1 ? 3 : 2)
  if (v === null || v === undefined) return '—'
  return String(v)
}

/** The parameter names in first-seen order, and those that take more than one value. */
export function gridParams(rows: GridRow[]): { all: string[]; varying: string[] } {
  const all: string[] = []
  const values = new Map<string, Set<string>>()
  for (const r of rows) {
    for (const [k, v] of Object.entries(r.params)) {
      if (!values.has(k)) {
        values.set(k, new Set())
        all.push(k)
      }
      values.get(k)!.add(JSON.stringify(v))
    }
  }
  return { all, varying: all.filter((k) => (values.get(k)?.size ?? 0) > 1) }
}

/** Metric columns for the grid table: the usual ones first, then any other numeric field. */
const KNOWN_METRICS = ['return_pct', 'sharpe_ratio', 'max_drawdown', 'max_drawdown_pct', 'win_rate', 'profit_factor', 'total_trades', 'alpha']

export function gridMetricKeys(rows: GridRow[]): string[] {
  const present = new Set<string>()
  for (const r of rows) for (const [k, v] of Object.entries(r.metrics)) if (typeof v === 'number' || v === null) present.add(k)
  const known = KNOWN_METRICS.filter((k) => present.has(k))
  const rest = [...present].filter((k) => !KNOWN_METRICS.includes(k) && !k.startsWith('_')).sort()
  return [...known, ...rest]
}

/** A metric by its key: percentages with %, returns and alpha with their sign. */
export function fmtMetric(key: string, v: number | null, infinite = false): string {
  if (v === null) return infinite ? '∞' : '—'
  if (key === 'total_trades') return fmtNum(v)
  if (key === 'win_rate') return fmtPct(v, 1, false)
  if (key.includes('drawdown')) return fmtPct(v, 2, false)
  if (signedMetric(key) || key.endsWith('_pct')) return fmtPct(v)
  return fmtNum(v, 2)
}

/** Returns and alpha are P&L-like: coloured by sign. */
export function signedMetric(key: string): boolean {
  return key.includes('return') || key === 'alpha'
}

/** Drawdown is the one objective where lower is better; the optimizer ranks on it negated. */
export function isNegatedObjective(objective: string): boolean {
  return objective.includes('drawdown')
}

/**
 * The value a grid row was ranked on, always higher-is-better: the optimizer's _objective
 * (max_drawdown negated, an infinite profit factor as 999), else the metric itself.
 */
export function objectiveValue(row: { metrics: Record<string, unknown> }, objective: string): number | null {
  const ranked = metric(row.metrics, '_objective')
  if (ranked !== null) return ranked
  const v = metric(row.metrics, objective)
  return v !== null && isNegatedObjective(objective) ? -v : v
}

export interface Heatmap {
  x: string
  y: string
  xValues: string[]
  yValues: string[]
  /** [xIndex, yIndex, value] */
  cells: [number, number, number][]
  min: number
  max: number
}

function sortValues(values: unknown[]): string[] {
  const uniq = [...new Map(values.map((v) => [JSON.stringify(v), v])).values()]
  uniq.sort((a, b) => (typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b))))
  return uniq.map(fmtValue)
}

/**
 * The ranked objective over the two parameters that vary, when exactly two do. A cell seen
 * twice keeps the better (higher) value.
 */
export function gridHeatmap(rows: GridRow[], objective: string): Heatmap | null {
  const { varying } = gridParams(rows)
  if (varying.length !== 2) return null
  const [x, y] = varying as [string, string]
  const xValues = sortValues(rows.map((r) => r.params[x]))
  const yValues = sortValues(rows.map((r) => r.params[y]))
  const cell = new Map<string, [number, number, number]>()
  for (const r of rows) {
    const v = objectiveValue(r, objective)
    if (v === null) continue
    const xi = xValues.indexOf(fmtValue(r.params[x]))
    const yi = yValues.indexOf(fmtValue(r.params[y]))
    const key = `${xi},${yi}`
    const prev = cell.get(key)
    if (!prev || v > prev[2]) cell.set(key, [xi, yi, v])
  }
  const cells = [...cell.values()]
  if (!cells.length) return null
  const vals = cells.map((c) => c[2])
  return { x, y, xValues, yValues, cells, min: Math.min(...vals), max: Math.max(...vals) }
}

// ── optimize: walk-forward ──────────────────────────────────────────────────

export interface WalkForwardSummary {
  meanTestReturn: number | null
  meanTrainReturn: number | null
  positiveShare: number | null
  efficiency: number | null
  windows: number
}

export function testReturn(w: WalkForwardWindow): number | null {
  return metric(w.test_metrics, 'return_pct')
}

/** Each window's train return: the windows carry none, the summary lists them in window order. */
export function trainReturns(windows: WalkForwardWindow[], summary: Record<string, unknown> = {}): (number | null)[] {
  const list = Array.isArray(summary.train_returns) ? summary.train_returns : []
  return windows.map((_, i) => num(list[i]))
}

/** The optimizer's own summary where it gives one, else computed from the windows. */
export function walkForwardSummary(windows: WalkForwardWindow[], summary: Record<string, unknown> = {}): WalkForwardSummary {
  const returns = windows.map(testReturn).filter((v): v is number => v !== null)
  const mean = returns.length ? returns.reduce((a, b) => a + b, 0) / returns.length : null
  const positive = returns.length ? (returns.filter((r) => r > 0).length / returns.length) * 100 : null
  return {
    meanTestReturn: metric(summary, 'avg_oos_return') ?? mean,
    meanTrainReturn: metric(summary, 'avg_train_return'),
    positiveShare: metric(summary, 'consistency_pct') ?? positive,
    efficiency: metric(summary, 'wf_efficiency'),
    windows: windows.length,
  }
}

// ── trades on the price chart ───────────────────────────────────────────────

export type TradeSide = 'buy' | 'sell' | 'other'

export function tradeSide(action: string): TradeSide {
  const a = action.trim().toUpperCase()
  if (a.startsWith('BUY') || a === 'LONG' || a === 'COVER') return 'buy'
  if (a.startsWith('SELL') || a === 'SHORT') return 'sell'
  return 'other'
}

export interface TradeMarker {
  /** ms since epoch */
  at: number
  side: 'buy' | 'sell'
  price: number
  qty: number
}

/** The buys and sells of the charted symbol, in time order. */
export function tradeMarkers(trades: TradeOut[], symbol: string): TradeMarker[] {
  const out: TradeMarker[] = []
  for (const t of trades) {
    if (t.symbol !== symbol) continue
    const side = tradeSide(t.action)
    const at = Date.parse(t.timestamp)
    if (side === 'other' || Number.isNaN(at)) continue
    out.push({ at, side, price: t.price, qty: t.qty })
  }
  return out.sort((a, b) => a.at - b.at)
}

// ── jobs ────────────────────────────────────────────────────────────────────

/** What a run was asked to do, read from its request (free-form in the contract). */
export interface RunSummary {
  title: string
  detail: string
  market: Market
  currency: string
  initialCapital: number | null
}

export function runSummary(job: Pick<Job, 'kind' | 'request'>): RunSummary {
  const r = job.request
  const market: Market = r.market === 'HK' ? 'HK' : 'US'
  const strategies = requestedStrategies(r)
  const strategy = typeof r.strategy === 'string' ? r.strategy : ''
  const title =
    job.kind === 'compare'
      ? strategies.length
        ? `${strategies.length} strategies`
        : 'Compare'
      : strategy || (job.kind === 'optimize' ? 'Optimize' : 'Backtest')
  const on = typeof r.universe === 'string' && r.universe ? r.universe : typeof r.symbol === 'string' ? r.symbol : ''
  const pair = typeof r.pair_symbol === 'string' && r.pair_symbol ? ` / ${r.pair_symbol}` : ''
  const method = job.kind === 'optimize' ? (r.method === 'grid' ? 'grid search' : 'walk-forward') : ''
  const parts = [
    on + pair,
    typeof r.timeframe === 'string' ? r.timeframe : '',
    typeof r.start === 'string' && typeof r.end === 'string' ? `${r.start} → ${r.end}` : '',
    method,
  ].filter(Boolean)
  return {
    title,
    detail: parts.join(' · '),
    market,
    currency: currencyFor(market),
    initialCapital: num(r.initial_capital),
  }
}

export const KIND_LABEL: Record<JobKind, string> = {
  backtest: 'Backtest',
  compare: 'Compare',
  optimize: 'Optimize',
}

export const KIND_PATH: Record<JobKind, string> = {
  backtest: '/backtest',
  compare: '/compare',
  optimize: '/optimize',
}
