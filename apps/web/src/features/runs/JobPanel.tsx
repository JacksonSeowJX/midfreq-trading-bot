import {
  ArchiveIcon,
  BanIcon,
  CircleCheckIcon,
  ClockIcon,
  EyeOffIcon,
  LaptopIcon,
  LoaderCircleIcon,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { isApiError } from '@/api/client'
import { isJobActive, useJob } from '@/api/queries'
import type { Job, JobKind, JobStatus } from '@/api/types'
import { EmptyState, ErrorState } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { fmtClock, fmtDateTime, fmtDuration } from '@/lib/format'
import { useNow } from '@/lib/useNow'
import { KIND_LABEL, KIND_PATH, runSummary } from './logic'
import { describeStartError, localInstallBody } from './startErrors'

/** The page of a run kind this deployment does not allow (capabilities.kinds lacks it). */
export function LocalOnly({ kind }: { kind: JobKind }) {
  return (
    <EmptyState icon={LaptopIcon} title={`${KIND_LABEL[kind]} is available on a local install`} className="py-10">
      {localInstallBody(kind)}
    </EmptyState>
  )
}

/**
 * Why a run did not start. When the API is busy (429), the runs in progress are linked, of
 * whatever kind, since this page's own list shows only its kind.
 */
export function StartError({
  error,
  kind,
  maxConcurrent,
  running = [],
}: {
  error: unknown
  kind: JobKind
  maxConcurrent?: number
  running?: Job[]
}) {
  const copy = describeStartError(error, kind, maxConcurrent)
  const busy = isApiError(error) && error.status === 429
  return (
    <div role="alert" className="flex gap-2.5 rounded-lg border border-warning/40 bg-warning/5 px-4 py-3 text-xs">
      <copy.icon aria-hidden className={copy.tone === 'loss' ? 'size-4 shrink-0 text-loss' : 'size-4 shrink-0 text-warning'} />
      <div className="min-w-0 space-y-1">
        <p className="text-[13px] font-semibold">{copy.title}</p>
        <div className="leading-relaxed text-muted-foreground">{copy.body}</div>
        {busy && running.length ? (
          <ul className="space-y-0.5">
            {running.map((j) => (
              <li key={j.id}>
                <Link className="text-primary hover:underline" to={`${KIND_PATH[j.kind]}?job=${encodeURIComponent(j.id)}`}>
                  Open the running {KIND_LABEL[j.kind].toLowerCase()} run: {runSummary(j).title}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  )
}

const STATUS: Record<JobStatus, { label: string; icon: LucideIcon; variant: 'outline' | 'warning' | 'gain' | 'loss' }> = {
  queued: { label: 'Queued', icon: ClockIcon, variant: 'outline' },
  running: { label: 'Running', icon: LoaderCircleIcon, variant: 'warning' },
  done: { label: 'Done', icon: CircleCheckIcon, variant: 'gain' },
  failed: { label: 'Failed', icon: BanIcon, variant: 'loss' },
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = STATUS[status]
  return (
    <Badge variant={s.variant}>
      <s.icon aria-hidden className={status === 'running' ? 'animate-spin' : undefined} />
      {s.label}
    </Badge>
  )
}

/** A run that has not finished: how far along it is and what it is doing. */
export function JobProgress({ job }: { job: Job }) {
  const now = useNow()
  const queued = job.status === 'queued'
  const pct = Math.round(Math.min(1, Math.max(0, job.progress)) * 100)
  const message = job.message || (queued ? 'Waiting for a free worker' : 'Working…')
  return (
    <div role="status" aria-live="polite" className="space-y-2">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="min-w-0 truncate" data-testid="job-message">
          {message}
        </span>
        <span className="shrink-0 tabular-nums text-muted-foreground">{queued ? 'queued' : `${pct}%`}</span>
      </div>
      <Progress value={queued ? null : job.progress} label="Run progress" />
      <p className="text-[11px] text-muted-foreground">
        {job.started
          ? `Started ${fmtClock(job.started)}, ${fmtDuration(job.started, new Date(now).toISOString())} so far.`
          : `Asked for at ${fmtClock(job.created)}.`}{' '}
        You can leave this page: the run carries on in the API and stays under Recent runs.
      </p>
    </div>
  )
}

/**
 * One run, opened from the URL (?job=...): polled while it runs, then its result. A run of
 * another kind links to its own page.
 */
export function JobPanel({
  jobId,
  kind,
  onClose,
  children,
}: {
  jobId: string
  kind: JobKind
  onClose: () => void
  children: (job: Job) => ReactNode
}) {
  const query = useJob(jobId)
  const job = query.data

  if (!job) {
    if (query.isError) {
      if (isApiError(query.error) && query.error.status === 404) {
        return (
          <EmptyState title="This run is no longer on the API" tone="warning" action={<CloseButton onClose={onClose} label="Start a new run" />}>
            Runs are kept in the API’s memory, so a restart clears them. Start it again from the form.
          </EmptyState>
        )
      }
      return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    }
    return (
      <Card className="space-y-3 p-4" aria-busy="true" aria-label="Loading run">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-1.5 w-full" />
      </Card>
    )
  }

  if (job.kind !== kind) {
    return (
      <EmptyState title={`This is a ${KIND_LABEL[job.kind].toLowerCase()} run`}>
        <Link className="text-primary hover:underline" to={`${KIND_PATH[job.kind]}?job=${encodeURIComponent(job.id)}`}>
          Open it on the {KIND_LABEL[job.kind]} page
        </Link>
      </EmptyState>
    )
  }

  const summary = runSummary(job)
  const active = isJobActive(job)
  return (
    <section aria-label="Run" className="space-y-4">
      <Card className="space-y-3 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0 space-y-0.5">
            <div className="flex flex-wrap items-center gap-2">
              <JobStatusBadge status={job.status} />
              <h2 className="text-sm font-semibold">{summary.title}</h2>
            </div>
            <p className="text-xs text-muted-foreground">
              {summary.detail}
              {summary.detail ? ' · ' : ''}
              {job.finished ? `finished ${fmtDateTime(job.finished)}` : `asked for ${fmtDateTime(job.created)}`}
            </p>
          </div>
          <CloseButton onClose={onClose} label={active ? 'Stop watching' : 'Close'} icon={active} />
        </div>
        {active ? <JobProgress job={job} /> : null}
        {job.status === 'done' ? <SavedAs runId={job.run_id ?? null} /> : null}
        {job.status === 'failed' ? (
          <div role="alert" className="rounded-md border border-loss/40 bg-loss/5 px-3 py-2 text-xs">
            <p className="font-semibold text-loss">The run failed</p>
            <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-foreground">
              {job.error || job.message || 'No reason was given.'}
            </pre>
          </div>
        ) : null}
      </Card>
      {job.status === 'done' ? children(job) : null}
    </section>
  )
}

/** Where a finished run was saved (Run history), or that it was not. */
function SavedAs({ runId }: { runId: string | null }) {
  if (!runId) {
    return <p className="text-[11px] text-muted-foreground">This run was not saved to Run history.</p>
  }
  return (
    <p className="flex flex-wrap items-center gap-1 text-xs">
      <ArchiveIcon aria-hidden className="size-3.5 text-muted-foreground" />
      Saved as run{' '}
      <Link className="font-mono text-[11px] text-primary hover:underline" to={`/runs/${encodeURIComponent(runId)}`}>
        {runId}
      </Link>
      <span className="text-muted-foreground">in Run history</span>
    </p>
  )
}

function CloseButton({ onClose, label, icon }: { onClose: () => void; label: string; icon?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onClose}>
      {icon ? <EyeOffIcon aria-hidden /> : null}
      {label}
    </Button>
  )
}
