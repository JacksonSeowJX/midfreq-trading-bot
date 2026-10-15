import { cn } from '@/lib/utils'

/**
 * A determinate progress bar (0..1), or an indeterminate one when `value` is null. The
 * percentage is also given as text by whoever shows it, so the bar is never the only cue.
 */
export function Progress({ value, label, className }: { value: number | null; label: string; className?: string }) {
  const pct = value === null ? null : Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-muted', className)}
    >
      <div
        className={cn('h-full rounded-full bg-brand transition-[width] duration-500', pct === null && 'w-1/3 animate-pulse')}
        style={pct === null ? undefined : { width: `${Math.max(pct, 2)}%` }}
      />
    </div>
  )
}
