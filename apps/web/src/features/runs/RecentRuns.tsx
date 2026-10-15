import { HistoryIcon } from 'lucide-react'
import { useJobs } from '@/api/queries'
import type { JobKind } from '@/api/types'
import { describeError } from '@/components/errorCopy'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fmtAgo, fmtShortDateTime } from '@/lib/format'
import { useNow } from '@/lib/useNow'
import { cn } from '@/lib/utils'
import { JobStatusBadge } from './JobPanel'
import { KIND_LABEL, runSummary } from './logic'

const SHOWN = 12

/** This view's recent runs (GET /api/jobs), newest first; one click reopens a result. */
export function RecentRuns({
  kind,
  currentId,
  onOpen,
}: {
  kind: JobKind
  currentId: string | null
  onOpen: (id: string) => void
}) {
  const jobs = useJobs()
  const now = useNow(30_000)
  const mine = (jobs.data ?? []).filter((j) => j.kind === kind).slice(0, SHOWN)
  return (
    <Card>
      <CardHeader className="py-2.5">
        <div className="flex items-center gap-2">
          <HistoryIcon aria-hidden className="size-3.5 text-muted-foreground" />
          <CardTitle>Recent runs</CardTitle>
        </div>
        <CardDescription>Kept by the API until it restarts</CardDescription>
      </CardHeader>
      {jobs.data === undefined ? (
        jobs.isError ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            Could not list runs: {describeError(jobs.error).title.toLowerCase()}.
          </p>
        ) : (
          <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading recent runs">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full opacity-70" />
          </div>
        )
      ) : mine.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">No {KIND_LABEL[kind].toLowerCase()} runs yet.</p>
      ) : (
        <ul aria-label="Recent runs" className="divide-y">
          {mine.map((j) => {
            const s = runSummary(j)
            const current = j.id === currentId
            return (
              <li key={j.id}>
                <button
                  type="button"
                  aria-current={current || undefined}
                  onClick={() => onOpen(j.id)}
                  className={cn(
                    'flex w-full cursor-pointer items-center gap-3 px-4 py-2 text-left hover:bg-accent',
                    current && 'bg-accent shadow-[inset_2px_0_0_var(--brand)]',
                  )}
                >
                  <JobStatusBadge status={j.status} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{s.title}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{s.detail}</span>
                  </span>
                  <span className="shrink-0 text-right text-[11px] tabular-nums text-muted-foreground" title={fmtShortDateTime(j.created)}>
                    {fmtAgo(Date.parse(j.created), now)}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
