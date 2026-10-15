import { CircleCheckIcon, HistoryIcon, SearchIcon, XIcon } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { SAVED_RUNS_LIMIT, useSavedRuns } from '@/api/queries'
import type { SavedRun, SavedRunKind } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/select'
import { Tooltip } from '@/components/ui/tooltip'
import { fmtNum, fmtPct, fmtShortDateTime, fmtDateTime, tone, toneClass } from '@/lib/format'
import { cn } from '@/lib/utils'
import {
  BASIS_NOTE,
  countBy,
  filterSavedRuns,
  NO_FILTERS,
  periodText,
  runPath,
  SAVED_KIND_LABEL,
  SAVED_RUN_KINDS,
  savedHeadline,
  savedRunTitle,
  SOURCE_LABEL,
  tradedText,
  type ReturnBasis,
  type RunFilters,
} from './logic'

const BASIS_TAG: Record<ReturnBasis, string | null> = {
  run: null,
  best: 'best',
  'best-in-sample': 'IS',
  'mean-out-of-sample': 'OOS',
}

/** The filters, kept in the URL (?kind=&source=&q=) so going back to the list keeps them. */
function useFilters(): [RunFilters, (f: Partial<RunFilters>) => void] {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind')
  const filters: RunFilters = {
    kind: SAVED_RUN_KINDS.includes(kind as SavedRunKind) ? (kind as SavedRunKind) : 'all',
    source: params.get('source') ?? 'all',
    query: params.get('q') ?? '',
  }
  const set = (f: Partial<RunFilters>) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        const merged = { ...filters, ...f }
        for (const [key, value, none] of [
          ['kind', merged.kind, 'all'],
          ['source', merged.source, 'all'],
          ['q', merged.query, ''],
        ] as const) {
          if (value === none) next.delete(key)
          else next.set(key, value)
        }
        return next
      },
      { replace: true },
    )
  return [filters, set]
}

const chip = 'h-6 cursor-pointer rounded-full border px-2.5 text-[11px] font-medium transition-colors'
const chipClass = (on: boolean) => cn(chip, on ? 'border-brand bg-brand/10 text-foreground' : 'text-muted-foreground hover:bg-accent')

function KindChips({ runs, value, onChange }: { runs: SavedRun[]; value: RunFilters['kind']; onChange: (k: RunFilters['kind']) => void }) {
  const counts = countBy(runs, (r) => r.kind)
  return (
    <div role="group" aria-label="Kind" className="flex flex-wrap gap-1.5">
      <button type="button" aria-pressed={value === 'all'} onClick={() => onChange('all')} className={chipClass(value === 'all')}>
        All kinds · {fmtNum(runs.length)}
      </button>
      {SAVED_RUN_KINDS.map((k) => (
        <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)} className={chipClass(value === k)}>
          {SAVED_KIND_LABEL[k]} · {fmtNum(counts.get(k) ?? 0)}
        </button>
      ))}
    </div>
  )
}

const COLUMNS: Column<SavedRun>[] = [
  {
    id: 'created',
    header: 'Saved',
    cell: (r) => <span title={fmtDateTime(r.created)}>{fmtShortDateTime(r.created)}</span>,
    sortValue: (r) => r.created,
  },
  {
    id: 'kind',
    header: 'Kind',
    cell: (r) => <Badge variant="outline">{SAVED_KIND_LABEL[r.kind]}</Badge>,
    sortValue: (r) => SAVED_KIND_LABEL[r.kind],
  },
  {
    id: 'strategy',
    header: 'Strategy',
    cell: (r) => (
      <span className="block max-w-[16rem] truncate font-medium" title={savedRunTitle(r)}>
        {savedRunTitle(r)}
      </span>
    ),
    sortValue: (r) => savedRunTitle(r),
  },
  {
    id: 'traded',
    header: 'Universe / symbol',
    cell: (r) => (
      <span className="block max-w-[12rem] truncate" title={tradedText(r)}>
        {tradedText(r)}
      </span>
    ),
    sortValue: (r) => tradedText(r),
  },
  { id: 'period', header: 'Period', cell: (r) => periodText(r), sortValue: (r) => r.start ?? null },
  {
    id: 'return',
    header: (
      <Tooltip content="A backtest's return; a comparison's best strategy; a grid search's best combination, in sample (IS); a walk-forward's mean out-of-sample return (OOS)">
        <span>Return</span>
      </Tooltip>
    ),
    align: 'right',
    cell: (r) => {
      const h = savedHeadline(r)
      const tag = BASIS_TAG[h.basis]
      return (
        <span title={`${fmtPct(h.returnPct)}: ${BASIS_NOTE[h.basis]}`}>
          {tag && h.returnPct !== null ? <span className="mr-1 text-[10px] font-semibold text-muted-foreground">{tag}</span> : null}
          <span className={toneClass[tone(h.returnPct, 2)]}>{fmtPct(h.returnPct)}</span>
        </span>
      )
    },
    sortValue: (r) => savedHeadline(r).returnPct,
  },
  { id: 'sharpe', header: 'Sharpe', align: 'right', cell: (r) => fmtNum(savedHeadline(r).sharpe, 2), sortValue: (r) => savedHeadline(r).sharpe },
  {
    id: 'drawdown',
    header: 'Max DD',
    align: 'right',
    cell: (r) => fmtPct(savedHeadline(r).maxDrawdownPct, 2, false),
    sortValue: (r) => savedHeadline(r).maxDrawdownPct,
  },
  { id: 'trades', header: 'Trades', align: 'right', cell: (r) => fmtNum(savedHeadline(r).trades), sortValue: (r) => savedHeadline(r).trades },
  {
    id: 'source',
    header: 'Source',
    cell: (r) => <span className="text-muted-foreground">{SOURCE_LABEL[r.source] ?? (r.source || '—')}</span>,
    sortValue: (r) => r.source,
  },
  {
    id: 'run_id',
    header: 'Run id',
    cell: (r) => (
      <Link
        to={runPath(r.run_id)}
        onClick={(e) => e.stopPropagation()}
        className="font-mono text-[11px] text-primary hover:underline"
      >
        {r.run_id}
      </Link>
    ),
    sortValue: (r) => r.run_id,
  },
]

function NoRunsYet() {
  return (
    <EmptyState icon={HistoryIcon} title="No saved runs yet">
      Every run that finishes on the <Link className="text-primary hover:underline" to="/backtest">Backtest</Link>,{' '}
      <Link className="text-primary hover:underline" to="/compare">Compare</Link> and{' '}
      <Link className="text-primary hover:underline" to="/optimize">Optimize</Link> pages is saved here, with the runs the
      research scripts save.
    </EmptyState>
  )
}

function Loading() {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading saved runs">
      <p className="text-xs text-muted-foreground">Reading the saved runs from the run store…</p>
      <TableSkeleton rows={8} />
    </div>
  )
}

function DeletedNote({ id, onDismiss }: { id: string; onDismiss: () => void }) {
  return (
    <div role="status" className="mb-3 flex items-center gap-2 rounded-md border border-gain/30 bg-gain/5 px-3 py-1.5 text-xs">
      <CircleCheckIcon aria-hidden className="size-3.5 shrink-0 text-gain" />
      <span className="min-w-0 flex-1">
        Deleted run <span className="font-mono">{id}</span>.
      </span>
      <Button variant="ghost" size="icon-sm" aria-label="Dismiss" onClick={onDismiss}>
        <XIcon />
      </Button>
    </div>
  )
}

function RunList({ runs }: { runs: SavedRun[] }) {
  const navigate = useNavigate()
  const [filters, setFilters] = useFilters()
  const shown = useMemo(() => filterSavedRuns(runs, filters), [runs, filters])
  const sources = useMemo(() => [...countBy(runs, (r) => r.source || 'unknown').keys()].sort(), [runs])
  const filtered = filters.kind !== 'all' || filters.source !== 'all' || filters.query !== ''

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Saved runs</CardTitle>
          <CardDescription>
            {fmtNum(shown.length)} of {fmtNum(runs.length)} shown
            {runs.length >= SAVED_RUNS_LIMIT ? ` (the newest ${fmtNum(SAVED_RUNS_LIMIT)})` : ''}. Click a run to open it.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative w-full max-w-72">
            <SearchIcon className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground" aria-hidden />
            <Input
              aria-label="Search runs"
              placeholder="Search strategy, universe, run id"
              value={filters.query}
              onChange={(e) => setFilters({ query: e.target.value })}
              className="pl-8"
            />
          </div>
          <NativeSelect aria-label="Source" value={filters.source} onChange={(e) => setFilters({ source: e.target.value })} className="w-40">
            <option value="all">All sources</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {SOURCE_LABEL[s] ?? s}
              </option>
            ))}
          </NativeSelect>
          {filtered ? (
            <Button variant="ghost" size="sm" onClick={() => setFilters(NO_FILTERS)}>
              Clear filters
            </Button>
          ) : null}
        </div>
        <KindChips runs={runs} value={filters.kind} onChange={(kind) => setFilters({ kind })} />
        <DataTable
          label="Saved runs"
          columns={COLUMNS}
          rows={shown}
          rowKey={(r) => r.run_id}
          initialSort={{ id: 'created', desc: true }}
          onRowClick={(r) => void navigate(runPath(r.run_id))}
          maxHeight={640}
          empty={<EmptyState title="No runs match these filters" />}
        />
      </CardContent>
    </Card>
  )
}

/** Every saved run (the run store), newest first: filter, sort, open one. */
export default function RunHistoryPage() {
  const runs = useSavedRuns()
  const location = useLocation()
  const navigate = useNavigate()
  const deleted = (location.state as { deleted?: string } | null)?.deleted
  return (
    <>
      <PageHeader
        title="Run history"
        description="Every backtest, comparison, grid search and walk-forward saved by the dashboard or a research script, with its tables to download."
      />
      {deleted ? (
        <DeletedNote id={deleted} onDismiss={() => void navigate({ search: location.search }, { replace: true, state: null })} />
      ) : null}
      <QueryState query={runs} skeleton={<Loading />}>
        {(all) => (all.length === 0 ? <NoRunsYet /> : <RunList runs={all} />)}
      </QueryState>
    </>
  )
}
