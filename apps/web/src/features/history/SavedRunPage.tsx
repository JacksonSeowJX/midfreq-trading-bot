import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeftIcon, CheckIcon, CopyIcon, Trash2Icon } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { isApiError } from '@/api/client'
import { queryKeys, useDeleteSavedRun, useRunsDeletable, useSavedRun } from '@/api/queries'
import type { SavedRunDetail } from '@/api/types'
import { describeError } from '@/components/errorCopy'
import { KpiGridSkeleton } from '@/components/KpiCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { fmtDateTime } from '@/lib/format'
import { periodText, SAVED_KIND_LABEL, savedRunTitle, SOURCE_LABEL, tradedText } from './logic'
import { RunSettings } from './RunSettings'
import { RunTables } from './RunTables'
import { SavedResult } from './SavedResult'

const SAVED_BY: Record<string, string> = { api: 'from the dashboard', script: 'by a script', cli: 'from the command line' }

function CopyId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={copied ? 'Copied the run id' : 'Copy the run id'}
      onClick={() => {
        void navigator.clipboard?.writeText(id).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        })
      }}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </Button>
  )
}

/**
 * Delete, only where the server allows it: asked for again before it happens; a 403 (the
 * hosted site) says so once and hides every Delete button for the rest of the visit.
 */
function DeleteRun({ id }: { id: string }) {
  const deletable = useRunsDeletable()
  const del = useDeleteSavedRun()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  if (isApiError(del.error) && del.error.status === 403) {
    return (
      <p role="status" className="text-xs text-muted-foreground">
        Deleting runs is turned off on this server.
      </p>
    )
  }
  if (!deletable) return null
  if (!confirming) {
    return (
      <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
        <Trash2Icon aria-hidden /> Delete run
      </Button>
    )
  }
  return (
    <div role="group" aria-label="Confirm delete" className="flex flex-wrap items-center gap-2 rounded-md border border-loss/40 bg-loss/5 px-2.5 py-1.5 text-xs">
      <span>Delete this run and its tables for good?</span>
      <Button
        size="sm"
        className="bg-loss text-white hover:bg-loss/90"
        disabled={del.isPending}
        onClick={() =>
          del.mutate(id, {
            onSuccess: () => {
              void navigate('/runs', { replace: true, state: { deleted: id } })
              qc.removeQueries({ queryKey: queryKeys.savedRun(id) })
            },
          })
        }
      >
        {del.isPending ? 'Deleting…' : 'Delete'}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={del.isPending}>
        Cancel
      </Button>
      {del.error ? (
        <span role="alert" className="basis-full text-loss">
          Not deleted: {describeError(del.error).title.toLowerCase()}
          {isApiError(del.error) && del.error.detail ? ` (${del.error.detail})` : ''}
        </span>
      ) : null}
    </div>
  )
}

function SavedRunView({ detail }: { detail: SavedRunDetail }) {
  const s = detail.summary
  return (
    <>
      <PageHeader
        title={savedRunTitle(s)}
        description={[SAVED_KIND_LABEL[s.kind], tradedText(s), periodText(s)].filter((x) => x && x !== '—').join(' · ')}
        actions={<DeleteRun id={s.run_id} />}
      />
      <div className="space-y-4">
        <Card className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2.5 text-xs">
          <Badge variant="outline">{SAVED_KIND_LABEL[s.kind]}</Badge>
          <span className="text-muted-foreground">
            Saved {fmtDateTime(s.created)} {SAVED_BY[s.source] ?? `(${SOURCE_LABEL[s.source] ?? (s.source || 'source unknown')})`}
          </span>
          <span className="inline-flex min-w-0 items-center gap-0.5">
            <span className="text-muted-foreground">Run</span>
            <code className="ml-1 truncate font-mono text-[11px]" data-testid="run-id">
              {s.run_id}
            </code>
            <CopyId id={s.run_id} />
          </span>
        </Card>
        <section aria-label="Result" className="space-y-4">
          <SavedResult detail={detail} />
        </section>
        <RunSettings detail={detail} />
        <RunTables runId={s.run_id} tables={detail.tables} />
      </div>
    </>
  )
}

function DetailSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading the run">
      <p className="text-xs text-muted-foreground">Reading the run from the run store…</p>
      <KpiGridSkeleton />
      <TableSkeleton rows={6} />
    </div>
  )
}

/** One saved run: its result in the run pages' views, how it was run, and its tables. */
export default function SavedRunPage() {
  const { runId = '' } = useParams()
  const run = useSavedRun(runId)
  return (
    <>
      <Link to="/runs" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeftIcon aria-hidden className="size-3.5" /> Run history
      </Link>
      <QueryState
        query={run}
        skeleton={<DetailSkeleton />}
        notFound={
          <EmptyState title="No such saved run" tone="warning">
            <span className="font-mono">{runId}</span> is not in the run store; it may have been deleted.{' '}
            <Link className="text-primary hover:underline" to="/runs">
              Back to Run history
            </Link>
          </EmptyState>
        }
      >
        {(d) => <SavedRunView detail={d} />}
      </QueryState>
    </>
  )
}
