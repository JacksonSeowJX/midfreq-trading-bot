/**
 * The run form shared by Backtest, Compare and Optimize: its state, defaults, what it
 * remembers between visits, and validation into the API's request types.
 *
 * Inputs are kept as the strings typed, so a half-typed number ("0.") never jumps; they are
 * parsed and checked against the same limits as the Streamlit sidebar (apps/dashboard/app.py)
 * when the run is submitted. Error keys are the request's own field paths ("risk.qty",
 * "params.lookback"), so a 422 from the API lands beside the same input.
 */
import type {
  BacktestRequest,
  CompareRequest,
  JobKind,
  OptimizeRequest,
  RiskSettings,
  SizingMethod,
  StrategyInfo,
  StrategyParam,
} from '@/api/types'
import type { Market } from '@/lib/preferences'
import { safeStorage } from '@/lib/utils'
import type { MarketCaps } from './logic'

export type Timeframe = BacktestRequest['timeframe']
export type OptimizeMethod = OptimizeRequest['method']

export const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: '1h', label: '1 hour' },
  { value: '1d', label: '1 day' },
]

export const SIZING_METHODS: { value: SizingMethod; label: string; explain: string }[] = [
  { value: 'fixed_quantity', label: 'Fixed quantity', explain: 'The same number of shares on every entry.' },
  { value: 'percent_risk', label: 'Percent risk', explain: 'Risk a fixed share of equity per trade, measured to the stop.' },
  { value: 'kelly', label: 'Kelly', explain: 'A fraction of the Kelly bet from the running win rate, capped per position.' },
  { value: 'equal_dollar', label: 'Equal dollar', explain: 'Split equity evenly by value across the positions.' },
]

/** The cross-sectional strategies rank a whole universe against itself (Streamlit's is_basket). */
export function isBasket(strategy: string): boolean {
  return strategy.startsWith('Cross-Sectional')
}

export function isPairs(strategy: string): boolean {
  return strategy === 'Pairs Trading'
}

export const DEFAULT_STRATEGY = 'Z-Score Mean Reversion'
export const COMPARE_MIN = 2
export const COMPARE_MAX = 8

// ── state ───────────────────────────────────────────────────────────────────

export interface RiskForm {
  stopLossOn: boolean
  stopLossPct: string
  trailingOn: boolean
  trailingPct: string
  takeProfitOn: boolean
  takeProfitPct: string
  maxDrawdownPct: string
  sizing: SizingMethod
  qty: string
  riskPct: string
  kellyFraction: string
  maxPositionPct: string
}

/** The choices that only make sense for one market. */
export interface MarketChoice {
  symbol: string
  pairSymbol: string
  universe: string
  /** commission per side in percent; null = the market's calibrated default */
  commissionPct: string | null
}

export interface RunForm {
  strategy: string
  /** strategy -> parameter -> the value typed; missing = the registry default */
  params: Record<string, Record<string, string>>
  /** Compare: the strategies to run; null = the default selection */
  compare: string[] | null
  markets: Partial<Record<Market, MarketChoice>>
  timeframe: Timeframe
  start: string
  end: string
  capital: string
  slippageBps: string
  risk: RiskForm
  method: OptimizeMethod
  objective: string
  maxCombinations: string
  nSplits: string
  trainPct: string
}

export const DEFAULT_RISK: RiskForm = {
  stopLossOn: false,
  stopLossPct: '3',
  trailingOn: false,
  trailingPct: '5',
  takeProfitOn: false,
  takeProfitPct: '5',
  maxDrawdownPct: '10',
  sizing: 'fixed_quantity',
  qty: '100',
  riskPct: '2',
  kellyFraction: '0.5',
  maxPositionPct: '25',
}

/** "2026-10-16" in the viewer's own calendar. */
export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** The Streamlit defaults: the last year, hourly candles, 100,000 of capital, no slippage. */
export function defaultForm(today: Date = new Date()): RunForm {
  const yearAgo = new Date(today)
  yearAgo.setFullYear(today.getFullYear() - 1)
  return {
    strategy: DEFAULT_STRATEGY,
    params: {},
    compare: null,
    markets: {},
    timeframe: '1h',
    start: isoDate(yearAgo),
    end: isoDate(today),
    capital: '100000',
    slippageBps: '0',
    risk: { ...DEFAULT_RISK },
    method: 'walk_forward',
    objective: 'sharpe_ratio',
    maxCombinations: '200',
    nSplits: '3',
    trainPct: '60',
  }
}

/** One line for the closed section, so the settings in force are visible without opening it. */
export function riskSummary(r: RiskForm, basket: boolean): string {
  const exits = [
    r.stopLossOn ? `stop ${r.stopLossPct}%` : '',
    r.trailingOn ? `trail ${r.trailingPct}%` : '',
    r.takeProfitOn ? `take ${r.takeProfitPct}%` : '',
  ].filter(Boolean)
  const sizing = basket
    ? 'equal value across the basket'
    : r.sizing === 'fixed_quantity'
      ? `${r.qty} shares`
      : r.sizing === 'percent_risk'
        ? `${r.riskPct}% risk`
        : r.sizing === 'kelly'
          ? `Kelly ×${r.kellyFraction}, max ${r.maxPositionPct}%`
          : 'equal value'
  return [sizing, exits.length ? exits.join(', ') : 'no stops', `halt at ${r.maxDrawdownPct}% drawdown`].join(' · ')
}

// ── remembered between visits (per view) ─────────────────────────────────────

export const formKey = (kind: JobKind) => `midfreq.run.${kind}`

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const str = (v: unknown, fallback: string) => (typeof v === 'string' ? v : fallback)
const bool = (v: unknown, fallback: boolean) => (typeof v === 'boolean' ? v : fallback)

function strRecord(v: unknown): Record<string, string> {
  if (!isObj(v)) return {}
  return Object.fromEntries(Object.entries(v).filter((e): e is [string, string] => typeof e[1] === 'string'))
}

function readRisk(v: unknown): RiskForm {
  const d = DEFAULT_RISK
  if (!isObj(v)) return { ...d }
  const sizing = SIZING_METHODS.some((m) => m.value === v.sizing) ? (v.sizing as SizingMethod) : d.sizing
  return {
    stopLossOn: bool(v.stopLossOn, d.stopLossOn),
    stopLossPct: str(v.stopLossPct, d.stopLossPct),
    trailingOn: bool(v.trailingOn, d.trailingOn),
    trailingPct: str(v.trailingPct, d.trailingPct),
    takeProfitOn: bool(v.takeProfitOn, d.takeProfitOn),
    takeProfitPct: str(v.takeProfitPct, d.takeProfitPct),
    maxDrawdownPct: str(v.maxDrawdownPct, d.maxDrawdownPct),
    sizing,
    qty: str(v.qty, d.qty),
    riskPct: str(v.riskPct, d.riskPct),
    kellyFraction: str(v.kellyFraction, d.kellyFraction),
    maxPositionPct: str(v.maxPositionPct, d.maxPositionPct),
  }
}

function readChoice(v: unknown): MarketChoice | undefined {
  if (!isObj(v)) return undefined
  return {
    symbol: str(v.symbol, ''),
    pairSymbol: str(v.pairSymbol, ''),
    universe: str(v.universe, ''),
    commissionPct: typeof v.commissionPct === 'string' ? v.commissionPct : null,
  }
}

/** The form as last used in this view, checked field by field; anything unreadable is the default. */
export function loadForm(kind: JobKind, today: Date = new Date()): RunForm {
  const d = defaultForm(today)
  let saved: unknown
  try {
    const raw = safeStorage.get(formKey(kind))
    saved = raw ? JSON.parse(raw) : undefined
  } catch {
    saved = undefined
  }
  if (!isObj(saved)) return d
  const params = isObj(saved.params)
    ? Object.fromEntries(Object.entries(saved.params).map(([k, v]) => [k, strRecord(v)]))
    : {}
  const markets: RunForm['markets'] = {}
  if (isObj(saved.markets)) {
    for (const m of ['US', 'HK'] as const) {
      const c = readChoice(saved.markets[m])
      if (c) markets[m] = c
    }
  }
  const compare = Array.isArray(saved.compare) ? saved.compare.filter((s): s is string => typeof s === 'string') : null
  return {
    strategy: str(saved.strategy, d.strategy),
    params,
    compare,
    markets,
    timeframe: saved.timeframe === '1d' || saved.timeframe === '1h' ? saved.timeframe : d.timeframe,
    start: str(saved.start, d.start),
    end: str(saved.end, d.end),
    capital: str(saved.capital, d.capital),
    slippageBps: str(saved.slippageBps, d.slippageBps),
    risk: readRisk(saved.risk),
    method: saved.method === 'grid' || saved.method === 'walk_forward' ? saved.method : d.method,
    objective: str(saved.objective, d.objective),
    maxCombinations: str(saved.maxCombinations, d.maxCombinations),
    nSplits: str(saved.nSplits, d.nSplits),
    trainPct: str(saved.trainPct, d.trainPct),
  }
}

export function saveForm(kind: JobKind, form: RunForm) {
  try {
    safeStorage.set(formKey(kind), JSON.stringify(form))
  } catch {
    /* not serialisable or storage full: the form still works, it is just not remembered */
  }
}

// ── resolving the stored choices against what the API offers now ─────────────

const PREFERRED_SYMBOL: Record<Market, string[]> = { US: ['US.AAPL'], HK: ['HK.00700'] }

/** Percent with three decimals, as the Streamlit input showed it: 0.0016 -> "0.160". */
export function commissionPctText(rate: number | null): string {
  return rate === null ? '' : (rate * 100).toFixed(3)
}

/** The market's choices, falling back to valid ones when a stored symbol or universe is gone. */
export function resolveChoice(saved: MarketChoice | undefined, market: Market, caps: MarketCaps): MarketChoice {
  const symbols = caps.symbols
  const symbol =
    saved?.symbol && symbols.includes(saved.symbol)
      ? saved.symbol
      : (PREFERRED_SYMBOL[market].find((s) => symbols.includes(s)) ?? symbols[0] ?? '')
  const pairOk = saved?.pairSymbol && saved.pairSymbol !== symbol && symbols.includes(saved.pairSymbol)
  const pairSymbol = pairOk ? saved.pairSymbol : (symbols.find((s) => s !== symbol) ?? '')
  const names = caps.universes.map((u) => u.name)
  const universe = saved?.universe && names.includes(saved.universe) ? saved.universe : (names[0] ?? '')
  const commissionPct = saved?.commissionPct ?? commissionPctText(caps.commissionRate)
  return { symbol, pairSymbol, universe, commissionPct }
}

export function resolveStrategy(name: string, strategies: StrategyInfo[]): StrategyInfo | undefined {
  return (
    strategies.find((s) => s.name === name) ??
    strategies.find((s) => s.name === DEFAULT_STRATEGY) ??
    strategies[0]
  )
}

/** Up to six single-stock strategies (neither pairs nor basket): Streamlit compared them all. */
export function defaultCompareSelection(strategies: StrategyInfo[]): string[] {
  return strategies
    .filter((s) => !isPairs(s.name) && !isBasket(s.name))
    .slice(0, 6)
    .map((s) => s.name)
}

export function resolveCompare(saved: string[] | null, strategies: StrategyInfo[]): string[] {
  const names = new Set(strategies.map((s) => s.name))
  return saved === null ? defaultCompareSelection(strategies) : saved.filter((n) => names.has(n))
}

// ── strategy parameters ─────────────────────────────────────────────────────

const isInt = (v: unknown) => typeof v === 'number' && Number.isInteger(v)

/**
 * Whole-number parameter? The registry's int/float distinction does not survive JSON (2.0 is
 * 2), so a parameter counts as whole when its default and all its limits are whole numbers.
 */
export function isIntegerParam(p: StrategyParam): boolean {
  return [p.default, p.min, p.max, p.step].every((v) => v === null || v === undefined || isInt(v))
}

export function paramDefaultText(p: StrategyParam): string {
  return typeof p.default === 'number' || typeof p.default === 'string' ? String(p.default) : ''
}

/** The values to show for a strategy: what was typed, else the registry default. */
export function paramTexts(form: RunForm, strategy: StrategyInfo): Record<string, string> {
  const typed = form.params[strategy.name] ?? {}
  return Object.fromEntries(strategy.params.map((p) => [p.name, typed[p.name] ?? paramDefaultText(p)]))
}

/**
 * How many values min..max by step gives, counted the way the optimizer's generate_param_grid
 * does: adding the step in floating point while the value is <= max, so 1.0 + 0.1 x 25 just
 * overshoots 3.5 and the last value is dropped (Z-Score's entry_z has 25 values, not 26).
 */
export function gridValues(p: StrategyParam): number {
  if (p.min == null || p.max == null || !p.step || p.step <= 0 || p.max < p.min) return 1
  let n = 0
  for (let v = p.min; v <= p.max && n < 100_000; v += p.step) n++
  return n
}

export function gridSize(strategy: StrategyInfo): number {
  return strategy.params.reduce((n, p) => n * gridValues(p), 1)
}

// ── validation ──────────────────────────────────────────────────────────────

export type FieldErrors = Record<string, string>

interface NumRule {
  min?: number
  max?: number
  integer?: boolean
  /** an empty input is allowed and means null */
  optional?: boolean
}

function fmtLimit(n: number): string {
  return String(Number(n.toFixed(6)))
}

/** Parse one input against its limits: a number, or the message to show beside it. */
export function checkNumber(raw: string, rule: NumRule): { value: number | null } | { error: string } {
  const text = raw.trim()
  if (text === '') return rule.optional ? { value: null } : { error: 'Required' }
  const n = Number(text)
  if (!Number.isFinite(n)) return { error: 'Not a number' }
  if (rule.integer && !Number.isInteger(n)) return { error: 'A whole number' }
  const { min, max } = rule
  if (min !== undefined && max !== undefined && (n < min || n > max)) return { error: `Between ${fmtLimit(min)} and ${fmtLimit(max)}` }
  if (min !== undefined && n < min) return { error: `At least ${fmtLimit(min)}` }
  if (max !== undefined && n > max) return { error: `At most ${fmtLimit(max)}` }
  return { value: n }
}

/** The limits of every number input, Streamlit's where it had them. */
export const LIMITS = {
  capital: { min: 1000 },
  commissionPct: { min: 0, max: 1 },
  slippageBps: { min: 0, max: 50 },
  stopLossPct: { min: 1, max: 15 },
  trailingPct: { min: 1, max: 15 },
  takeProfitPct: { min: 1, max: 30 },
  maxDrawdownPct: { min: 5, max: 50 },
  qty: { min: 1, integer: true },
  riskPct: { min: 0.5, max: 10 },
  kellyFraction: { min: 0.1, max: 1 },
  maxPositionPct: { min: 5, max: 50 },
  maxCombinations: { min: 5, max: 2000, integer: true },
  nSplits: { min: 2, max: 15, integer: true },
  trainPct: { min: 50, max: 90 },
} satisfies Record<string, NumRule>

export function paramRule(p: StrategyParam): NumRule {
  return { min: p.min ?? undefined, max: p.max ?? undefined, integer: isIntegerParam(p) }
}

export interface FormContext {
  market: Market
  caps: MarketCaps
  strategies: StrategyInfo[]
  objectives: Record<string, string>
}

class Checker {
  errors: FieldErrors = {}

  num(key: string, raw: string, rule: NumRule): number {
    const r = checkNumber(raw, rule)
    if ('error' in r) {
      this.errors[key] ??= r.error
      return Number.NaN
    }
    return r.value ?? Number.NaN
  }

  optionalNum(key: string, raw: string, rule: NumRule): number | null {
    const r = checkNumber(raw, { ...rule, optional: true })
    if ('error' in r) {
      this.errors[key] ??= r.error
      return null
    }
    return r.value
  }

  fail(key: string, message: string) {
    this.errors[key] ??= message
  }
}

const pct = (n: number) => n / 100

function checkParams(c: Checker, form: RunForm, strategy: StrategyInfo, prefix: string): Record<string, unknown> {
  const texts = paramTexts(form, strategy)
  return Object.fromEntries(
    strategy.params.map((p) => [p.name, c.num(`${prefix}${p.name}`, texts[p.name] ?? '', paramRule(p))]),
  )
}

function checkRisk(c: Checker, r: RiskForm): RiskSettings {
  const d = DEFAULT_RISK
  const parsedOr = (raw: string, fallback: string) => {
    const n = Number(raw.trim())
    return raw.trim() !== '' && Number.isFinite(n) ? n : Number(fallback)
  }
  // only the chosen sizing method's own fields are checked; the others go as they are (or default)
  const sizingNum = (key: keyof RiskForm & keyof typeof LIMITS, path: string, active: boolean) =>
    active ? c.num(path, r[key] as string, LIMITS[key]) : parsedOr(r[key] as string, d[key] as string)
  return {
    stop_loss_pct: r.stopLossOn ? pct(c.num('risk.stop_loss_pct', r.stopLossPct, LIMITS.stopLossPct)) : null,
    trailing_stop_pct: r.trailingOn ? pct(c.num('risk.trailing_stop_pct', r.trailingPct, LIMITS.trailingPct)) : null,
    take_profit_pct: r.takeProfitOn ? pct(c.num('risk.take_profit_pct', r.takeProfitPct, LIMITS.takeProfitPct)) : null,
    max_drawdown_pct: pct(c.num('risk.max_drawdown_pct', r.maxDrawdownPct, LIMITS.maxDrawdownPct)),
    sizing: r.sizing,
    qty: Math.round(sizingNum('qty', 'risk.qty', r.sizing === 'fixed_quantity')),
    risk_pct: pct(sizingNum('riskPct', 'risk.risk_pct', r.sizing === 'percent_risk')),
    fraction: sizingNum('kellyFraction', 'risk.fraction', r.sizing === 'kelly'),
    max_pct: pct(sizingNum('maxPositionPct', 'risk.max_pct', r.sizing === 'kelly')),
  }
}

const DATE = /^\d{4}-\d{2}-\d{2}$/

function checkCommon(c: Checker, form: RunForm, ctx: FormContext, needs: { pair: boolean; basket: boolean }) {
  const choice = resolveChoice(form.markets[ctx.market], ctx.market, ctx.caps)
  if (!choice.symbol) c.fail('symbol', 'Pick a symbol')
  else if (!ctx.caps.symbols.includes(choice.symbol)) c.fail('symbol', `No cached data for ${choice.symbol}`)
  if (needs.pair) {
    if (!choice.pairSymbol) c.fail('pair_symbol', 'Pick the second stock of the pair')
    else if (choice.pairSymbol === choice.symbol) c.fail('pair_symbol', 'Must differ from the first symbol')
  }
  if (needs.basket && !choice.universe) c.fail('universe', 'Pick a universe')

  if (!DATE.test(form.start)) c.fail('start', 'A date')
  if (!DATE.test(form.end)) c.fail('end', 'A date')
  if (DATE.test(form.start) && DATE.test(form.end) && form.start >= form.end) c.fail('end', 'After the start date')

  const commission = c.optionalNum('commission_rate', choice.commissionPct ?? '', LIMITS.commissionPct)
  return {
    market: ctx.market,
    symbol: choice.symbol,
    pair_symbol: needs.pair ? choice.pairSymbol : null,
    universe: needs.basket ? choice.universe : null,
    timeframe: form.timeframe,
    start: form.start,
    end: form.end,
    initial_capital: c.num('initial_capital', form.capital, LIMITS.capital),
    commission_rate: commission === null ? null : pct(commission),
    slippage_bps: c.num('slippage_bps', form.slippageBps, LIMITS.slippageBps),
    risk: checkRisk(c, form.risk),
  }
}

export type Checked<T> = { ok: true; request: T } | { ok: false; errors: FieldErrors }

function done<T>(c: Checker, request: T): Checked<T> {
  return Object.keys(c.errors).length ? { ok: false, errors: c.errors } : { ok: true, request }
}

export function checkBacktest(form: RunForm, ctx: FormContext): Checked<BacktestRequest> {
  const c = new Checker()
  const strategy = resolveStrategy(form.strategy, ctx.strategies)
  if (!strategy) {
    c.fail('strategy', 'Pick a strategy')
    return { ok: false, errors: c.errors }
  }
  const common = checkCommon(c, form, ctx, { pair: isPairs(strategy.name), basket: isBasket(strategy.name) })
  const params = checkParams(c, form, strategy, 'params.')
  return done(c, { ...common, strategy: strategy.name, params })
}

export function checkCompare(form: RunForm, ctx: FormContext): Checked<CompareRequest> {
  const c = new Checker()
  const names = resolveCompare(form.compare, ctx.strategies)
  if (names.length < COMPARE_MIN || names.length > COMPARE_MAX)
    c.fail('strategies', `Pick ${COMPARE_MIN} to ${COMPARE_MAX} strategies (${names.length} picked)`)
  const picked = ctx.strategies.filter((s) => names.includes(s.name))
  const common = checkCommon(c, form, ctx, {
    pair: picked.some((s) => isPairs(s.name)),
    basket: picked.some((s) => isBasket(s.name)),
  })
  const strategies = picked.map((s) => ({ strategy: s.name, params: checkParams(c, form, s, `strategies.${s.name}.`) }))
  return done(c, { ...common, strategies })
}

export function checkOptimize(form: RunForm, ctx: FormContext): Checked<OptimizeRequest> {
  const c = new Checker()
  const strategy = resolveStrategy(form.strategy, ctx.strategies)
  if (!strategy) {
    c.fail('strategy', 'Pick a strategy')
    return { ok: false, errors: c.errors }
  }
  const common = checkCommon(c, form, ctx, { pair: isPairs(strategy.name), basket: isBasket(strategy.name) })
  const objectives = Object.keys(ctx.objectives)
  if (objectives.length && !objectives.includes(form.objective)) c.fail('objective', 'Pick an objective')
  const grid = form.method === 'grid'
  return done(c, {
    ...common,
    strategy: strategy.name,
    method: form.method,
    objective: form.objective,
    max_combinations: grid ? c.num('max_combinations', form.maxCombinations, LIMITS.maxCombinations) : 200,
    n_splits: grid ? 3 : c.num('n_splits', form.nSplits, LIMITS.nSplits),
    train_pct: grid ? 0.6 : pct(c.num('train_pct', form.trainPct, LIMITS.trainPct)),
  })
}

/**
 * A 422's errors keyed like the form's own: loc ["body", "risk", "qty"] -> "risk.qty",
 * ["body", "strategies", 1, "params", "lookback"] -> "strategies.<name>.lookback". Anything
 * the form has no input for goes under "" so it is still shown.
 */
export function serverFieldErrors(
  fields: { loc: (string | number)[]; msg: string }[],
  compareNames: string[] = [],
): FieldErrors {
  const out: FieldErrors = {}
  for (const f of fields) {
    const path = f.loc[0] === 'body' ? f.loc.slice(1) : f.loc
    let key = path.map(String).join('.')
    if (path[0] === 'strategies' && typeof path[1] === 'number') {
      const name = compareNames[path[1]]
      key = name && path[2] === 'params' && path[3] !== undefined ? `strategies.${name}.${String(path[3])}` : 'strategies'
    }
    const msg = f.msg.replace(/^Value error, /, '')
    out[key] = out[key] ? `${out[key]}; ${msg}` : msg
  }
  return out
}

const PREFIXED_FIELDS = ['symbol', 'pair_symbol', 'universe', 'objective', 'start', 'end', 'strategy', 'strategies']
const LEADING_FIELD = /^(initial_capital|commission_rate|slippage_bps|risk\.[a-z_]+)\b\s*(.*)$/

/**
 * The API's own run checks answer 422 with one sentence, not FastAPI's list. Where the
 * sentence names a field ("symbol: US.X is not a US symbol", "RSI: lookback must be between
 * 2 and 50, not 80", "risk.qty must be at least 1"), it goes beside that input; anything else
 * under "" for the form's summary.
 */
export function detailFieldErrors(
  detail: string,
  sent: { strategies: string[]; compare: boolean; symbol?: string | null; pairSymbol?: string | null },
): FieldErrors {
  const text = detail.trim()
  const prefixed = /^([a-z_]+): (.+)$/.exec(text)
  if (prefixed && PREFIXED_FIELDS.includes(prefixed[1]!)) return { [prefixed[1]!]: prefixed[2]! }
  const leading = LEADING_FIELD.exec(text)
  if (leading) return { [leading[1]!]: leading[2] || text }
  if (/^start \(.*\) is after end/.test(text)) return { end: text }
  for (const name of sent.strategies) {
    if (!text.startsWith(`${name}: `)) continue
    const param = /^([A-Za-z_][A-Za-z0-9_]*) (must .+)$/.exec(text.slice(name.length + 2))
    if (param) return { [sent.compare ? `strategies.${name}.${param[1]}` : `params.${param[1]}`]: param[2]! }
  }
  const candles = /^no cached \S+ candles for (\S+)$/.exec(text)
  if (candles && candles[1] === sent.symbol) return { symbol: text }
  if (candles && candles[1] === sent.pairSymbol) return { pair_symbol: text }
  return { '': text }
}
