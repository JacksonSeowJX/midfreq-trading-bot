import type { ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { describedBy } from './fieldIds'

/** Label above, the control, then either the error or the hint below it. */
export function Field({
  id,
  label,
  hint,
  error,
  children,
  className,
}: {
  id: string
  label: ReactNode
  hint?: ReactNode
  error?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('min-w-0 space-y-1', className)}>
      <label htmlFor={id} className="block text-xs font-medium text-foreground">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-[11px] font-medium text-loss">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-[11px] leading-snug text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

export function NumberField({
  id,
  label,
  value,
  onChange,
  min,
  max,
  step,
  hint,
  error,
  disabled,
  className,
}: {
  id: string
  label: ReactNode
  value: string
  onChange: (v: string) => void
  min?: number | null
  max?: number | null
  step?: number | null
  hint?: ReactNode
  error?: string
  disabled?: boolean
  className?: string
}) {
  return (
    <Field id={id} label={label} hint={hint} error={error} className={className}>
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        className="tabular-nums"
        value={value}
        min={min ?? undefined}
        max={max ?? undefined}
        // the arrows step by this; any value in range is accepted
        step={step ?? 'any'}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  )
}

/** A percentage that can be switched off (stop-loss, trailing stop, take-profit). */
export function OptionalPctField({
  id,
  label,
  on,
  onToggle,
  value,
  onChange,
  min,
  max,
  step,
  error,
  hint,
}: {
  id: string
  label: string
  on: boolean
  onToggle: (on: boolean) => void
  value: string
  onChange: (v: string) => void
  min: number
  max: number
  step: number
  error?: string
  hint?: ReactNode
}) {
  const switchId = `${id}-on`
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={switchId} className="cursor-pointer text-xs font-medium">
          {label}
        </label>
        <Switch id={switchId} checked={on} onCheckedChange={onToggle} />
      </div>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          aria-label={`${label} (%)`}
          className="tabular-nums"
          value={value}
          min={min}
          max={max}
          step={step}
          disabled={!on}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, error, hint)}
          onChange={(e) => onChange(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">%</span>
      </div>
      {error ? (
        <p id={`${id}-error`} className="text-[11px] font-medium text-loss">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-[11px] text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

/** A titled group of fields inside the form. */
export function FormSection({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <fieldset className="min-w-0 space-y-3 border-t px-4 pb-4 pt-3 first:border-t-0">
      <legend className="sr-only">{title}</legend>
      <div aria-hidden className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{title}</span>
        {aside ? <span className="text-[11px] text-muted-foreground">{aside}</span> : null}
      </div>
      {children}
    </fieldset>
  )
}
