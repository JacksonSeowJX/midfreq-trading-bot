import { ChevronRightIcon, ShieldIcon } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { NativeSelect } from '@/components/ui/select'
import { Field, NumberField, OptionalPctField } from './fields'
import { describedBy, fieldId } from './fieldIds'
import { LIMITS, riskSummary, SIZING_METHODS, type FieldErrors, type RiskForm } from './form'

/** Stops, the drawdown circuit breaker and position sizing: Streamlit's Risk Management expanders. */
export function RiskFields({
  formId,
  risk,
  onChange,
  errors,
  open,
  onOpenChange,
  basket,
}: {
  formId: string
  risk: RiskForm
  onChange: (patch: Partial<RiskForm>) => void
  errors: FieldErrors
  open: boolean
  onOpenChange: (open: boolean) => void
  basket: boolean
}) {
  const id = (k: string) => fieldId(formId, `risk.${k}`)
  const sizing = SIZING_METHODS.find((m) => m.value === risk.sizing)
  const sizingId = id('sizing')
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="border-t">
      <CollapsibleTrigger className="group flex w-full cursor-pointer items-start gap-2 px-4 py-3 text-left hover:bg-accent/60">
        <ChevronRightIcon
          aria-hidden
          className="mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90"
        />
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            <ShieldIcon aria-hidden className="size-3" /> Risk &amp; sizing
          </span>
          <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{riskSummary(risk, basket)}</span>
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <fieldset className="space-y-3 px-4 pb-4">
          <legend className="sr-only">Risk &amp; sizing</legend>
          <div className="grid grid-cols-1 gap-3">
            <OptionalPctField
              id={id('stop_loss_pct')}
              label="Stop-loss"
              on={risk.stopLossOn}
              onToggle={(on) => onChange({ stopLossOn: on })}
              value={risk.stopLossPct}
              onChange={(v) => onChange({ stopLossPct: v })}
              step={0.5}
              {...LIMITS.stopLossPct}
              error={errors['risk.stop_loss_pct']}
            />
            <OptionalPctField
              id={id('trailing_stop_pct')}
              label="Trailing stop"
              on={risk.trailingOn}
              onToggle={(on) => onChange({ trailingOn: on })}
              value={risk.trailingPct}
              onChange={(v) => onChange({ trailingPct: v })}
              step={0.5}
              {...LIMITS.trailingPct}
              error={errors['risk.trailing_stop_pct']}
            />
            <OptionalPctField
              id={id('take_profit_pct')}
              label="Take-profit"
              on={risk.takeProfitOn}
              onToggle={(on) => onChange({ takeProfitOn: on })}
              value={risk.takeProfitPct}
              onChange={(v) => onChange({ takeProfitPct: v })}
              step={0.5}
              {...LIMITS.takeProfitPct}
              error={errors['risk.take_profit_pct']}
            />
          </div>
          <NumberField
            id={id('max_drawdown_pct')}
            label="Max drawdown halt (%)"
            value={risk.maxDrawdownPct}
            onChange={(v) => onChange({ maxDrawdownPct: v })}
            step={1}
            {...LIMITS.maxDrawdownPct}
            hint="Measured from the starting capital, not the peak: while equity is this far below it, the strategy acts on no signals (stops still apply)."
            error={errors['risk.max_drawdown_pct']}
          />
          <Field
            id={sizingId}
            label="Sizing method"
            hint={basket ? 'Cross-sectional strategies always split equity equally by value across the basket; this applies to single-stock strategies.' : sizing?.explain}
            error={errors['risk.sizing']}
          >
            <NativeSelect
              id={sizingId}
              value={risk.sizing}
              aria-describedby={describedBy(sizingId, errors['risk.sizing'], true)}
              onChange={(e) => {
                const m = SIZING_METHODS.find((s) => s.value === e.target.value)
                if (m) onChange({ sizing: m.value })
              }}
            >
              {SIZING_METHODS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {risk.sizing === 'fixed_quantity' ? (
            <NumberField
              id={id('qty')}
              label="Shares per trade"
              value={risk.qty}
              onChange={(v) => onChange({ qty: v })}
              step={10}
              min={LIMITS.qty.min}
              error={errors['risk.qty']}
            />
          ) : null}
          {risk.sizing === 'percent_risk' ? (
            <NumberField
              id={id('risk_pct')}
              label="Risk per trade (%)"
              value={risk.riskPct}
              onChange={(v) => onChange({ riskPct: v })}
              step={0.5}
              {...LIMITS.riskPct}
              error={errors['risk.risk_pct']}
            />
          ) : null}
          {risk.sizing === 'kelly' ? (
            <div className="grid grid-cols-2 gap-3">
              <NumberField
                id={id('fraction')}
                label="Kelly fraction"
                value={risk.kellyFraction}
                onChange={(v) => onChange({ kellyFraction: v })}
                step={0.1}
                {...LIMITS.kellyFraction}
                error={errors['risk.fraction']}
              />
              <NumberField
                id={id('max_pct')}
                label="Max position (%)"
                value={risk.maxPositionPct}
                onChange={(v) => onChange({ maxPositionPct: v })}
                step={5}
                {...LIMITS.maxPositionPct}
                error={errors['risk.max_pct']}
              />
            </div>
          ) : null}
        </fieldset>
      </CollapsibleContent>
    </Collapsible>
  )
}
