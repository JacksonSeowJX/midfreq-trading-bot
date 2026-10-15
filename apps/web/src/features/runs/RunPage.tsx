import { PlayCircleIcon } from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { isApiError } from '@/api/client'
import { isJobActive, useCapabilities, useJobs, useStartJob, useStrategies, useUniverses } from '@/api/queries'
import type { Job, JobCapabilities, JobKind } from '@/api/types'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, QueryState } from '@/components/states'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useMarket } from '@/lib/preferences'
import { detailFieldErrors, resolveCompare, serverFieldErrors } from './form'
import { JobPanel, LocalOnly, StartError } from './JobPanel'
import { marketCaps } from './logic'
import { RecentRuns } from './RecentRuns'
import { RunForm } from './RunForm'

function FormSkeleton() {
  return (
    <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading the form">
      {Array.from({ length: 7 }, (_, i) => (
        <div key={i} className="space-y-1.5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-full" />
        </div>
      ))}
    </div>
  )
}

/** The open run, kept in the URL (?job=...) so it can be linked to and survives a reload. */
function useJobParam(): [string | null, (id: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const set = (id: string | null) =>
    setParams((p) => {
      const next = new URLSearchParams(p)
      if (id) next.set('job', id)
      else next.delete('job')
      return next
    })
  return [params.get('job'), set]
}

/**
 * Backtest, Compare and Optimize share this page: the run form on the left; on the right the
 * open run (progress while it runs, then the result) and the recent runs.
 */
export function RunPage({
  kind,
  title,
  description,
  empty,
  renderResult,
}: {
  kind: JobKind
  title: string
  description: ReactNode
  empty: ReactNode
  renderResult: (job: Job, caps: JobCapabilities | undefined) => ReactNode
}) {
  const { market } = useMarket()
  const caps = useCapabilities()
  const strategies = useStrategies()
  const universes = useUniverses()
  const start = useStartJob()
  const jobs = useJobs() // shared with RecentRuns: one request
  const [jobId, setJobId] = useJobParam()
  const mc = useMemo(() => marketCaps(caps.data, market, universes.data), [caps.data, market, universes.data])

  const allowed = caps.data ? caps.data.kinds.includes(kind) : true
  const startError = start.error
  const serverErrors = useMemo(() => {
    if (!isApiError(startError) || startError.status !== 422) return {}
    const sent = start.variables
    const names =
      sent?.kind === 'compare'
        ? sent.body.strategies.map((s) => s.strategy)
        : sent
          ? [sent.body.strategy]
          : resolveCompare(null, strategies.data ?? [])
    // FastAPI's own validation is a list of {loc, msg}; the API's run checks one sentence
    const errs = serverFieldErrors(startError.fields, names)
    if (Object.keys(errs).length) return errs
    return detailFieldErrors(startError.detail ?? 'Invalid settings', {
      strategies: names,
      compare: sent?.kind === 'compare',
      symbol: sent?.body.symbol,
      pairSymbol: sent?.body.pair_symbol,
    })
  }, [startError, start.variables, strategies.data])

  if (!allowed) {
    return (
      <>
        <PageHeader title={title} description={description} />
        <LocalOnly kind={kind} />
      </>
    )
  }

  return (
    <>
      <PageHeader title={title} description={description} />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(300px,340px)_minmax(0,1fr)]">
        <Card className="min-w-0 overflow-visible">
          <QueryState query={caps} skeleton={<FormSkeleton />}>
            {(c) => (
              <QueryState query={strategies} skeleton={<FormSkeleton />}>
                {(s) => (
                  <RunForm
                    kind={kind}
                    strategies={s}
                    caps={mc}
                    objectives={c.objectives}
                    submitting={start.isPending}
                    serverErrors={serverErrors}
                    onEdit={() => {
                      if (start.error) start.reset()
                    }}
                    onSubmit={(req) => start.mutate(req, { onSuccess: (job) => setJobId(job.id) })}
                  />
                )}
              </QueryState>
            )}
          </QueryState>
        </Card>
        <div className="min-w-0 space-y-4">
          {startError ? (
            <StartError
              error={startError}
              kind={kind}
              maxConcurrent={caps.data?.max_concurrent}
              running={(jobs.data ?? []).filter(isJobActive)}
            />
          ) : null}
          {jobId ? (
            <JobPanel key={jobId} jobId={jobId} kind={kind} onClose={() => setJobId(null)}>
              {(job) => renderResult(job, caps.data)}
            </JobPanel>
          ) : (
            <EmptyState icon={PlayCircleIcon} title="No run open" className="py-10">
              {empty}
            </EmptyState>
          )}
          <RecentRuns
            kind={kind}
            currentId={jobId}
            onOpen={(id) => {
              start.reset()
              setJobId(id)
            }}
          />
        </div>
      </div>
    </>
  )
}
