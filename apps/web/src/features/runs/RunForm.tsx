import { BookOpenIcon, CircleAlertIcon, InfoIcon, LoaderCircleIcon, PlayIcon } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import type { StartJob } from '@/api/queries'
import type { JobKind, StrategyInfo } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Combobox } from '@/components/ui/combobox'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { fmtNum } from '@/lib/format'
import { MARKET_LABEL, useMarket } from '@/lib/preferences'
import { cn } from '@/lib/utils'
import { Field, FormSection, NumberField } from './fields'
import { describedBy, fieldId } from './fieldIds'
import {
  checkBacktest,
  checkCompare,
  checkOptimize,
  COMPARE_MAX,
  COMPARE_MIN,
  commissionPctText,
  compareSelection,
  fitsCompare,
  gridSize,
  isBasket,
  isIntegerParam,
  isPairs,
  LIMITS,
  loadForm,
  paramTexts,
  resolveChoice,
  resolveCompareOn,
  resolveStrategy,
  saveForm,
  TIMEFRAMES,
  type Checked,
  type CompareOn,
  type FieldErrors,
  type FormContext,
  type MarketChoice,
  type RiskForm,
  type RunForm as RunFormState,
} from './form'
import { forTimeframe, type MarketCaps } from './logic'
import type { RunPreset } from './presets'
import { RiskFields } from './RiskFields'

function fmtLimit(v: number | null | undefined): string {
  return v === null || v === undefined ? '' : String(Number(v.toFixed(6)))
}

/** One input per strategy parameter, within the registry's min, max and step. */
export function ParamFields({
  formId,
  strategy,
  values,
  onChange,
  errors,
  prefix,
}: {
  formId: string
  strategy: StrategyInfo
  values: Record<string, string>
  onChange: (name: string, value: string) => void
  errors: FieldErrors
  prefix: string
}) {
  if (strategy.params.length === 0) return <p className="text-xs text-muted-foreground">No parameters.</p>
  return (
    <div className="grid grid-cols-2 gap-3">
      {strategy.params.map((p) => {
        const key = `${prefix}${p.name}`
        const range =
          p.min != null && p.max != null ? `${fmtLimit(p.min)}–${fmtLimit(p.max)}${p.step ? `, step ${fmtLimit(p.step)}` : ''}` : undefined
        return (
          <NumberField
            key={p.name}
            id={fieldId(formId, key)}
            label={p.label || p.name}
            value={values[p.name] ?? ''}
            onChange={(v) => onChange(p.name, v)}
            min={p.min}
            max={p.max}
            step={p.step ?? (isIntegerParam(p) ? 1 : undefined)}
            hint={range}
            error={errors[key]}
          />
        )
      })}
    </div>
  )
}

const KIND_SUBMIT: Record<JobKind, string> = {
  backtest: 'Run backtest',
  compare: 'Compare strategies',
  optimize: 'Run optimization',
}

/** The error keys this form shows beside an input; anything else goes in the summary. */
function shownKeys(
  kind: JobKind,
  form: RunFormState,
  strategy: StrategyInfo | undefined,
  compare: StrategyInfo[],
  risk: RiskForm,
  data: { symbol: boolean; pair: boolean; universe: boolean },
) {
  const keys = new Set([
    'start',
    'end',
    'initial_capital',
    'commission_rate',
    'slippage_bps',
    'risk.stop_loss_pct',
    'risk.trailing_stop_pct',
    'risk.take_profit_pct',
    'risk.max_drawdown_pct',
    'risk.sizing',
  ])
  if (data.symbol) keys.add('symbol')
  if (data.pair) keys.add('pair_symbol')
  if (data.universe) keys.add('universe')
  if (risk.sizing === 'fixed_quantity') keys.add('risk.qty')
  if (risk.sizing === 'percent_risk') keys.add('risk.risk_pct')
  if (risk.sizing === 'kelly') ['risk.fraction', 'risk.max_pct'].forEach((k) => keys.add(k))
  if (kind === 'compare') {
    keys.add('strategies')
    for (const s of compare) for (const p of s.params) keys.add(`strategies.${s.name}.${p.name}`)
  } else {
    keys.add('strategy')
    for (const p of strategy?.params ?? []) if (kind === 'backtest') keys.add(`params.${p.name}`)
  }
  if (kind === 'optimize') {
    keys.add('objective')
    if (form.method === 'grid') keys.add('max_combinations')
    else ['n_splits', 'train_pct'].forEach((k) => keys.add(k))
  }
  return keys
}

export interface RunFormProps {
  kind: JobKind
  strategies: StrategyInfo[]
  caps: MarketCaps
  objectives: Record<string, string>
  onSubmit: (req: StartJob) => void
  submitting: boolean
  /** a 422's errors, keyed like the form's own */
  serverErrors?: FieldErrors
  /** called on any edit, so stale server errors can be cleared */
  onEdit?: () => void
  /** a guide example to fill the form with when it mounts, instead of the remembered inputs */
  preset?: RunPreset
}

/**
 * The run settings: strategy and parameters, the data
 * (market from the top bar, symbol, pair, universe, timeframe, dates), money (capital,
 * commission, slippage) and, folded away, risk and sizing. Remembered per view; a preset
 * (a guide example) replaces the remembered inputs when the form mounts, and says so until
 * the first edit or run.
 *
 * A cross-sectional strategy ranks a universe, so it asks for the universe and no symbol. A
 * comparison runs on one stock or on one universe, chosen first; the list of strategies is
 * then those that run there.
 */
export function RunForm({
  kind,
  strategies,
  caps: allCaps,
  objectives,
  onSubmit,
  submitting,
  serverErrors = {},
  onEdit,
  preset,
}: RunFormProps) {
  const formId = useId()
  const formRef = useRef<HTMLFormElement>(null)
  const { market } = useMarket()
  const [form, setForm] = useState<RunFormState>(() => (preset ? preset.form() : loadForm(kind)))
  const [presetNote, setPresetNote] = useState(preset?.title ?? null)
  const [submitted, setSubmitted] = useState(false)
  const [riskOpen, setRiskOpen] = useState(false)
  const [focusTick, setFocusTick] = useState(0)

  useEffect(() => saveForm(kind, form), [kind, form])
  // the symbols on offer are those with candles cached at the chosen timeframe
  const caps = useMemo(() => forTimeframe(allCaps, form.timeframe), [allCaps, form.timeframe])

  const strategy = resolveStrategy(form.strategy, strategies)
  const compareOn = resolveCompareOn(form, strategies)
  const compareNames = compareSelection(form, strategies)
  const compareInfos = strategies.filter((s) => compareNames.includes(s.name))
  const choice = resolveChoice(form.markets[market], market, caps)
  const selected = kind === 'compare' ? compareInfos : strategy ? [strategy] : []
  // a basket run takes a universe and no symbol; a comparison is one or the other, never both
  const basket = kind === 'compare' ? compareOn === 'universe' : !!strategy && isBasket(strategy.name)
  const needsPair = !basket && selected.some((s) => isPairs(s.name))

  const ctx: FormContext = useMemo(() => ({ market, caps, strategies, objectives }), [market, caps, strategies, objectives])
  const check = useMemo((): Checked<StartJob> => {
    if (kind === 'backtest') {
      const r = checkBacktest(form, ctx)
      return r.ok ? { ok: true, request: { kind, body: r.request } } : r
    }
    if (kind === 'compare') {
      const r = checkCompare(form, ctx)
      return r.ok ? { ok: true, request: { kind, body: r.request } } : r
    }
    const r = checkOptimize(form, ctx)
    return r.ok ? { ok: true, request: { kind, body: r.request } } : r
  }, [kind, form, ctx])

  const errors: FieldErrors = { ...(submitted && !check.ok ? check.errors : {}), ...serverErrors }
  const hasRiskError = Object.keys(errors).some((k) => k.startsWith('risk.'))
  const shown = shownKeys(kind, form, strategy, compareInfos, form.risk, { symbol: !basket, pair: needsPair, universe: basket })
  const unplaced = Object.entries(errors).filter(([k]) => !shown.has(k))

  // after a failed submit, focus the first input with an error (it may have just been revealed)
  useEffect(() => {
    if (focusTick) formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  }, [focusTick])

  function touched() {
    onEdit?.()
    setPresetNote(null)
  }
  function edit(patch: Partial<RunFormState>) {
    touched()
    setForm((f) => ({ ...f, ...patch }))
  }
  function editChoice(patch: Partial<MarketChoice>) {
    touched()
    setForm((f) => ({ ...f, markets: { ...f.markets, [market]: { ...resolveChoice(f.markets[market], market, caps), ...patch } } }))
  }
  function editParam(name: string, param: string, value: string) {
    touched()
    setForm((f) => ({ ...f, params: { ...f.params, [name]: { ...f.params[name], [param]: value } } }))
  }
  function editRisk(patch: Partial<RiskForm>) {
    touched()
    setForm((f) => ({ ...f, risk: { ...f.risk, ...patch } }))
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    setSubmitted(true)
    if (check.ok) {
      setPresetNote(null)
      onSubmit(check.request)
    }
    else setFocusTick((n) => n + 1)
  }

  const id = (key: string) => fieldId(formId, key)
  const symbolHint = `${fmtNum(caps.symbols.length)} symbols with cached ${form.timeframe} data`
  const universe = caps.universes.find((u) => u.name === choice.universe)
  const defaultCommission = commissionPctText(caps.commissionRate)
  const submitLabel =
    kind === 'optimize' ? (form.method === 'grid' ? 'Run grid search' : 'Run walk-forward') : KIND_SUBMIT[kind]

  return (
    <form ref={formRef} noValidate onSubmit={submit} aria-label={`${KIND_SUBMIT[kind]} settings`}>
      {presetNote ? <PresetNote title={presetNote} submitLabel={submitLabel} /> : null}
      {kind === 'compare' ? (
        <FormSection title="Strategies" aside={`${compareNames.length} picked, ${COMPARE_MIN}–${COMPARE_MAX}`}>
          {strategies.some((s) => isBasket(s.name)) ? (
            <CompareOnToggle formId={formId} value={compareOn} onChange={(v) => edit({ compareOn: v })} />
          ) : null}
          <CompareChecklist
            formId={formId}
            strategies={strategies.filter((s) => fitsCompare(s.name, compareOn))}
            selected={compareNames}
            onToggle={(name, on) => {
              const names = on ? [...compareNames, name] : compareNames.filter((n) => n !== name)
              edit(compareOn === 'universe' ? { compareBaskets: names } : { compare: names })
            }}
            form={form}
            onParam={editParam}
            errors={errors}
          />
        </FormSection>
      ) : (
        <FormSection title="Strategy">
          <Field id={id('strategy')} label="Strategy" error={errors.strategy}>
            <NativeSelect
              id={id('strategy')}
              value={strategy?.name ?? ''}
              aria-invalid={errors.strategy ? true : undefined}
              onChange={(e) => edit({ strategy: e.target.value })}
            >
              {strategies.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {strategy && kind === 'backtest' ? (
            <ParamFields
              formId={formId}
              strategy={strategy}
              values={paramTexts(form, strategy)}
              onChange={(p, v) => editParam(strategy.name, p, v)}
              errors={errors}
              prefix="params."
            />
          ) : null}
          {strategy && kind === 'optimize' ? <GridNote strategy={strategy} /> : null}
        </FormSection>
      )}

      <FormSection title="Data" aside={`${market} · ${caps.currency}`}>
        <p className="text-[11px] leading-snug text-muted-foreground">
          {MARKET_LABEL[market]}. Switch the market in the top bar.
        </p>
        {!basket ? (
          <Field
            id={id('symbol')}
            label="Symbol"
            hint={caps.symbols.length ? symbolHint : undefined}
            error={errors.symbol ?? (caps.symbols.length ? undefined : `No ${market} symbols have cached ${form.timeframe} data`)}
          >
            <Combobox
              id={id('symbol')}
              value={choice.symbol}
              options={caps.symbols}
              onChange={(v) => editChoice({ symbol: v })}
              placeholder="Search symbols"
              invalid={!!errors.symbol}
              describedBy={describedBy(id('symbol'), errors.symbol, symbolHint)}
            />
          </Field>
        ) : null}
        {needsPair ? (
          <Field id={id('pair_symbol')} label="Pair symbol" hint="The second stock of the pair." error={errors.pair_symbol}>
            <Combobox
              id={id('pair_symbol')}
              value={choice.pairSymbol}
              options={caps.symbols.filter((s) => s !== choice.symbol)}
              onChange={(v) => editChoice({ pairSymbol: v })}
              placeholder="Search symbols"
              invalid={!!errors.pair_symbol}
              describedBy={describedBy(id('pair_symbol'), errors.pair_symbol, true)}
            />
          </Field>
        ) : null}
        {basket ? (
          <Field
            id={id('universe')}
            label="Universe"
            hint={universe?.description || undefined}
            error={errors.universe ?? (caps.universes.length ? undefined : `No ${market} universes`)}
          >
            <NativeSelect
              id={id('universe')}
              value={choice.universe}
              aria-invalid={errors.universe ? true : undefined}
              aria-describedby={describedBy(id('universe'), errors.universe, universe?.description)}
              onChange={(e) => editChoice({ universe: e.target.value })}
            >
              {/* the API's labels end in their size already ("US — S&P 100 (100)"); others get it added */}
              {caps.universes.map((u) => (
                <option key={u.name} value={u.name}>
                  {u.label}
                  {u.symbols !== null && !/\(\d+\)\s*$/.test(u.label) ? ` (${u.symbols})` : ''}
                </option>
              ))}
            </NativeSelect>
          </Field>
        ) : null}
        {basket ? (
          <BasketNote
            kind={kind}
            universe={universe?.label ?? choice.universe}
            size={universe?.withData ?? universe?.symbols ?? null}
            withData={universe?.withData !== null && universe?.withData !== undefined}
          />
        ) : null}
        <div className="space-y-1">
          <span id={id('timeframe')} className="block text-xs font-medium">
            Timeframe
          </span>
          <ToggleGroup
            type="single"
            aria-labelledby={id('timeframe')}
            value={form.timeframe}
            onValueChange={(v) => {
              const tf = TIMEFRAMES.find((t) => t.value === v)
              if (tf) edit({ timeframe: tf.value })
            }}
          >
            {TIMEFRAMES.map((t) => (
              <ToggleGroupItem key={t.value} value={t.value} aria-label={t.label} className="px-3">
                {t.value}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field id={id('start')} label="Start" error={errors.start}>
            <Input
              id={id('start')}
              type="date"
              value={form.start}
              max={form.end || undefined}
              aria-invalid={errors.start ? true : undefined}
              aria-describedby={describedBy(id('start'), errors.start)}
              onChange={(e) => edit({ start: e.target.value })}
            />
          </Field>
          <Field id={id('end')} label="End" error={errors.end}>
            <Input
              id={id('end')}
              type="date"
              value={form.end}
              min={form.start || undefined}
              aria-invalid={errors.end ? true : undefined}
              aria-describedby={describedBy(id('end'), errors.end)}
              onChange={(e) => edit({ end: e.target.value })}
            />
          </Field>
        </div>
      </FormSection>

      <FormSection title="Money">
        <NumberField
          id={id('initial_capital')}
          label={`Starting capital (${caps.currency})`}
          value={form.capital}
          onChange={(v) => edit({ capital: v })}
          min={LIMITS.capital.min}
          step={10000}
          error={errors.initial_capital}
        />
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            id={id('commission_rate')}
            label="Commission per side (%)"
            value={choice.commissionPct ?? ''}
            onChange={(v) => editChoice({ commissionPct: v })}
            step={0.005}
            {...LIMITS.commissionPct}
            hint={
              defaultCommission ? (
                choice.commissionPct === defaultCommission ? (
                  `${market} default`
                ) : (
                  <button
                    type="button"
                    className="cursor-pointer text-primary hover:underline"
                    onClick={() => editChoice({ commissionPct: defaultCommission })}
                  >
                    Reset to {defaultCommission}%
                  </button>
                )
              ) : (
                'Empty: the market’s default'
              )
            }
            error={errors.commission_rate}
          />
          <NumberField
            id={id('slippage_bps')}
            label="Slippage (bps)"
            value={form.slippageBps}
            onChange={(v) => edit({ slippageBps: v })}
            step={1}
            {...LIMITS.slippageBps}
            hint="1 bps = 0.01%"
            error={errors.slippage_bps}
          />
        </div>
        <p className="text-[11px] leading-snug text-muted-foreground">
          {market === 'HK'
            ? 'HK 0.160% is broker commission, platform fee, 0.1% stamp duty and levies.'
            : 'US 0.005% is moomoo Singapore’s flat US$0.99 + GST per order, at these position sizes.'}{' '}
          Slippage: buys pay more, sells receive less.
        </p>
      </FormSection>

      {kind === 'optimize' ? (
        <FormSection title="Optimization">
          <div className="space-y-1">
            <span id={id('method')} className="block text-xs font-medium">
              Method
            </span>
            <ToggleGroup
              type="single"
              aria-labelledby={id('method')}
              value={form.method}
              onValueChange={(v) => {
                if (v === 'grid' || v === 'walk_forward') edit({ method: v })
              }}
            >
              <ToggleGroupItem value="walk_forward" className="px-2.5">
                Walk-forward
              </ToggleGroupItem>
              <ToggleGroupItem value="grid" className="px-2.5">
                Grid search
              </ToggleGroupItem>
            </ToggleGroup>
          </div>
          <Field id={id('objective')} label="Objective" error={errors.objective}>
            <NativeSelect
              id={id('objective')}
              value={form.objective}
              aria-invalid={errors.objective ? true : undefined}
              onChange={(e) => edit({ objective: e.target.value })}
            >
              {Object.keys(objectives).length === 0 ? <option value={form.objective}>{form.objective}</option> : null}
              {Object.entries(objectives).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {form.method === 'grid' ? (
            <NumberField
              id={id('max_combinations')}
              label="Max combinations"
              value={form.maxCombinations}
              onChange={(v) => edit({ maxCombinations: v })}
              step={5}
              min={LIMITS.maxCombinations.min}
              max={LIMITS.maxCombinations.max}
              error={errors.max_combinations}
            />
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <NumberField
                id={id('n_splits')}
                label="Rolling windows"
                value={form.nSplits}
                onChange={(v) => edit({ nSplits: v })}
                step={1}
                min={LIMITS.nSplits.min}
                max={LIMITS.nSplits.max}
                error={errors.n_splits}
              />
              <NumberField
                id={id('train_pct')}
                label="Train share (%)"
                value={form.trainPct}
                onChange={(v) => edit({ trainPct: v })}
                step={5}
                {...LIMITS.trainPct}
                hint="The rest of each window is the test"
                error={errors.train_pct}
              />
            </div>
          )}
        </FormSection>
      ) : null}

      <RiskFields
        formId={formId}
        risk={form.risk}
        onChange={editRisk}
        errors={errors}
        open={riskOpen || hasRiskError}
        onOpenChange={setRiskOpen}
        basket={basket}
      />

      <div className="space-y-2 border-t p-4">
        {unplaced.length ? (
          <div role="alert" className="rounded-md border border-loss/40 bg-loss/5 px-3 py-2 text-xs text-loss">
            <p className="flex items-center gap-1.5 font-semibold">
              <CircleAlertIcon className="size-3.5" aria-hidden /> The API rejected these settings
            </p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {unplaced.map(([k, msg]) => (
                <li key={k}>
                  {k ? <code className="font-mono">{k}</code> : null}
                  {k ? ': ' : ''}
                  {msg}
                </li>
              ))}
            </ul>
          </div>
        ) : submitted && !check.ok ? (
          <p role="alert" className="text-xs text-loss">
            Fix the highlighted {Object.keys(errors).length === 1 ? 'field' : 'fields'} to run.
          </p>
        ) : null}
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? <LoaderCircleIcon className="animate-spin" aria-hidden /> : <PlayIcon aria-hidden />}
          {submitting ? 'Starting…' : submitLabel}
        </Button>
      </div>
    </form>
  )
}

/** The form holds a guide example: what was filled in, and that nothing has run yet. */
function PresetNote({ title, submitLabel }: { title: string; submitLabel: string }) {
  return (
    <div role="status" className="px-4 pb-3 pt-4">
      <p className="flex gap-2 rounded-md border-l-2 border-brand bg-muted/60 px-2.5 py-2 text-xs leading-snug text-muted-foreground">
        <BookOpenIcon aria-hidden className="mt-px size-3.5 shrink-0 text-brand" />
        <span>
          <strong className="text-foreground">Filled in from the guide:</strong> {title}. Nothing has run yet; press{' '}
          <strong className="text-foreground">{submitLabel}</strong> below to run it.
        </span>
      </p>
    </div>
  )
}

function GridNote({ strategy }: { strategy: StrategyInfo }) {
  const n = gridSize(strategy)
  return (
    <p className="flex gap-1.5 text-[11px] leading-snug text-muted-foreground">
      <InfoIcon aria-hidden className="mt-px size-3 shrink-0" />
      <span>
        The search covers each parameter from its minimum to its maximum by its step:{' '}
        {strategy.params.map((p) => p.label || p.name).join(', ') || 'no parameters'}. The full grid has{' '}
        <strong className="text-foreground">{fmtNum(n)}</strong> combination{n === 1 ? '' : 's'}.
      </span>
    </p>
  )
}

function BasketNote({ kind, universe, size, withData }: { kind: JobKind; universe: string; size: number | null; withData: boolean }) {
  const count = size !== null ? ` (${fmtNum(size)} symbols${withData ? ' with data' : ''})` : ''
  const runs = kind === 'compare' ? 'they run' : 'this runs'
  return (
    <p className="flex gap-1.5 rounded-md border-l-2 border-brand bg-muted/60 px-2.5 py-2 text-[11px] leading-snug text-muted-foreground">
      <InfoIcon aria-hidden className="mt-px size-3 shrink-0 text-brand" />
      <span>
        {`Cross-sectional strategies rank stocks against each other, so ${runs} on the whole ${universe} universe${count}, not on one stock. Positions are sized equally by value across the basket.`}
        {kind === 'optimize'
          ? ' On the S&P 100 or Hang Seng a walk-forward takes roughly 10–15 minutes; the smaller universes 1–2.'
          : ' The result charts the equity against holding every stock of the universe in equal amounts.'}
      </span>
    </p>
  )
}

/** What a comparison runs on: a stock or a universe. The list of strategies below follows it. */
function CompareOnToggle({ formId, value, onChange }: { formId: string; value: CompareOn; onChange: (v: CompareOn) => void }) {
  const labelId = fieldId(formId, 'compare_on')
  return (
    <div className="space-y-1">
      <span id={labelId} className="block text-xs font-medium">
        Compare on
      </span>
      <ToggleGroup
        type="single"
        aria-labelledby={labelId}
        aria-describedby={`${labelId}-hint`}
        value={value}
        onValueChange={(v) => {
          if (v === 'stock' || v === 'universe') onChange(v)
        }}
      >
        <ToggleGroupItem value="stock" className="px-2.5">
          One stock
        </ToggleGroupItem>
        <ToggleGroupItem value="universe" className="px-2.5">
          A universe
        </ToggleGroupItem>
      </ToggleGroup>
      <p id={`${labelId}-hint`} className="text-[11px] leading-snug text-muted-foreground">
        {value === 'universe'
          ? 'The cross-sectional strategies, which rank the stocks of a universe against each other, compared with each other.'
          : 'The strategies that trade one stock (Pairs Trading, a pair). The cross-sectional ones run on a universe.'}
      </p>
    </div>
  )
}

function CompareChecklist({
  formId,
  strategies,
  selected,
  onToggle,
  form,
  onParam,
  errors,
}: {
  formId: string
  strategies: StrategyInfo[]
  selected: string[]
  onToggle: (name: string, on: boolean) => void
  form: RunFormState
  onParam: (strategy: string, param: string, value: string) => void
  errors: FieldErrors
}) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const errorId = fieldId(formId, 'strategies')
  return (
    <div className="space-y-2">
      <ul
        aria-label="Strategies to compare"
        aria-describedby={errors.strategies ? `${errorId}-error` : undefined}
        className={cn('divide-y rounded-md border', errors.strategies && 'border-loss')}
      >
        {strategies.map((s) => {
          const on = selected.includes(s.name)
          const boxId = fieldId(formId, `compare.${s.name}`)
          const paramError = s.params.some((p) => errors[`strategies.${s.name}.${p.name}`])
          const open = on && (expanded === s.name || paramError)
          const note = isPairs(s.name) ? 'needs a pair' : ''
          return (
            <li key={s.name} className="px-2.5 py-1.5">
              <div className="flex items-center gap-2">
                <input
                  id={boxId}
                  type="checkbox"
                  checked={on}
                  onChange={(e) => onToggle(s.name, e.target.checked)}
                  className="size-3.5 cursor-pointer accent-[var(--brand)]"
                />
                <label htmlFor={boxId} className="min-w-0 flex-1 cursor-pointer truncate text-[13px]">
                  {s.name}
                  {note ? <span className="ml-1.5 text-[11px] text-muted-foreground">{note}</span> : null}
                </label>
                {on && s.params.length ? (
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-label={`${s.name} parameters`}
                    onClick={() => setExpanded(open ? null : s.name)}
                    className={cn(
                      'cursor-pointer rounded px-1.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground',
                      paramError && 'text-loss',
                    )}
                  >
                    {open ? 'Hide' : 'Params'}
                  </button>
                ) : null}
              </div>
              {open ? (
                <div className="mb-1 mt-2">
                  <ParamFields
                    formId={formId}
                    strategy={s}
                    values={paramTexts(form, s)}
                    onChange={(p, v) => onParam(s.name, p, v)}
                    errors={errors}
                    prefix={`strategies.${s.name}.`}
                  />
                </div>
              ) : null}
            </li>
          )
        })}
      </ul>
      {errors.strategies ? (
        <p id={`${errorId}-error`} className="text-[11px] font-medium text-loss">
          {errors.strategies}
        </p>
      ) : (
        <p className="text-[11px] text-muted-foreground">Each runs with its own parameters (defaults unless changed).</p>
      )}
    </div>
  )
}
