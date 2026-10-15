import { InfoIcon } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip } from '@/components/ui/tooltip'
import { toneClass, type Tone } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface KpiCardProps {
  label: string
  value: ReactNode
  /** a second line under the value: a delta, a unit, a date */
  sub?: ReactNode
  /** colours the value (and sub) by P&L direction; the sign in the text carries it too */
  tone?: Tone
  /** colour only the sub line, e.g. a +% delta under a neutral total */
  subTone?: Tone
  hint?: ReactNode
  className?: string
}

/** One headline number. Proportional figures at this size; tables use tabular ones. */
export function KpiCard({ label, value, sub, tone = 'flat', subTone, hint, className }: KpiCardProps) {
  const id = useId()
  return (
    <div
      role="group"
      aria-labelledby={id}
      className={cn('min-w-0 rounded-lg border bg-card px-3.5 py-2.5 shadow-[0_1px_2px_rgb(16_24_40/0.04)]', className)}
    >
      <div className="flex items-center gap-1">
        <span id={id} className="truncate text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        {hint ? (
          <Tooltip content={hint}>
            <button
              type="button"
              aria-label={`About ${label}`}
              className="cursor-help text-muted-foreground/70 hover:text-foreground"
            >
              <InfoIcon className="size-3" />
            </button>
          </Tooltip>
        ) : null}
      </div>
      <div className={cn('mt-0.5 truncate text-xl font-semibold leading-7', toneClass[tone])} data-testid="kpi-value">
        {value}
      </div>
      {sub !== undefined && sub !== null ? (
        <div className={cn('truncate text-xs text-muted-foreground', toneClass[subTone ?? 'flat'])}>{sub}</div>
      ) : null}
    </div>
  )
}

export function KpiGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4', className)}>{children}</div>
}

export function KpiGridSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <KpiGrid className={className}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="rounded-lg border bg-card px-3.5 py-2.5">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="mt-2 h-6 w-28" />
        </div>
      ))}
    </KpiGrid>
  )
}
