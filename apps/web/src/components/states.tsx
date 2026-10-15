import type { UseQueryResult } from '@tanstack/react-query'
import { InboxIcon, RefreshCwIcon, TriangleAlertIcon, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { isApiError } from '@/api/client'
import { describeError } from '@/components/errorCopy'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { fmtClock } from '@/lib/format'
import { cn } from '@/lib/utils'

export function EmptyState({
  icon: Icon = InboxIcon,
  title,
  children,
  tone = 'muted',
  action,
  className,
}: {
  icon?: LucideIcon
  title: string
  children?: ReactNode
  tone?: 'muted' | 'warning' | 'loss'
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      role="status"
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-md border border-dashed px-6 py-8 text-center',
        className,
      )}
    >
      <Icon
        aria-hidden
        className={cn(
          'size-6',
          tone === 'muted' && 'text-muted-foreground',
          tone === 'warning' && 'text-warning',
          tone === 'loss' && 'text-loss',
        )}
      />
      <p className="text-sm font-semibold">{title}</p>
      {children ? <div className="max-w-lg text-xs leading-relaxed text-muted-foreground">{children}</div> : null}
      {action}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown
  onRetry?: () => void
  className?: string
}) {
  const copy = describeError(error)
  return (
    <EmptyState
      icon={copy.icon}
      title={copy.title}
      tone={copy.tone}
      className={className}
      action={
        onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
            <RefreshCwIcon /> Retry
          </Button>
        ) : null
      }
    >
      {copy.body}
    </EmptyState>
  )
}

/** Shown above data that is still on screen after a background refresh failed. */
export function StaleBanner({ error, updatedAt }: { error: unknown; updatedAt: number }) {
  const copy = describeError(error)
  return (
    <div
      role="status"
      className="mb-3 flex items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-warning"
    >
      <TriangleAlertIcon className="size-3.5 shrink-0" aria-hidden />
      <span>
        Showing data from {fmtClock(updatedAt)}; the last refresh failed ({copy.title.toLowerCase()}).
      </span>
    </div>
  )
}

/**
 * Loading, error and data for one query, so every panel handles them the same way:
 * a skeleton until the first answer, a clear state on failure (404 has its own), and the
 * previous data kept on screen, with a banner, when a background refresh fails.
 */
export function QueryState<T>({
  query,
  skeleton,
  notFound,
  children,
}: {
  query: UseQueryResult<T, Error>
  skeleton: ReactNode
  notFound?: ReactNode
  children: (data: T) => ReactNode
}) {
  if (query.data !== undefined) {
    return (
      <>
        {query.isError ? <StaleBanner error={query.error} updatedAt={query.dataUpdatedAt} /> : null}
        {children(query.data)}
      </>
    )
  }
  if (query.isError) {
    if (notFound && isApiError(query.error) && query.error.status === 404) return <>{notFound}</>
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  }
  return <>{skeleton}</>
}

export function TableSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-1.5 rounded-md border p-2', className)} aria-busy="true" aria-label="Loading">
      <Skeleton className="h-6 w-full" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-5 w-full opacity-70" />
      ))}
    </div>
  )
}

export function ChartSkeleton({ height = 260 }: { height?: number }) {
  return <Skeleton className="w-full" style={{ height }} aria-label="Loading chart" />
}
