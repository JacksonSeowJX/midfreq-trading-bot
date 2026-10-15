import { ChevronRightIcon, DownloadIcon, LoaderCircleIcon, TableIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { downloadSavedRunTable, useSavedRunTable } from '@/api/queries'
import type { SavedRunTable, TableFormat } from '@/api/types'
import { describeError } from '@/components/errorCopy'
import { DataTable, type Column } from '@/components/DataTable'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cellText } from '@/features/research/logic'
import { fmtNum } from '@/lib/format'
import { cn } from '@/lib/utils'

type Row = Record<string, unknown>

const PREVIEW_ROWS = 1000

function DownloadButton({ runId, name, format }: { runId: string; name: string; format: TableFormat }) {
  const [state, setState] = useState<{ busy: boolean; error?: string }>({ busy: false })
  const label = format === 'csv' ? 'CSV' : 'Parquet'
  return (
    <span className="inline-flex items-center gap-1.5">
      <Button
        variant="outline"
        size="sm"
        aria-label={`Download ${name} as ${label}`}
        disabled={state.busy}
        onClick={() => {
          setState({ busy: true })
          downloadSavedRunTable(runId, name, format).then(
            () => setState({ busy: false }),
            (e: unknown) => setState({ busy: false, error: describeError(e).title }),
          )
        }}
      >
        {state.busy ? <LoaderCircleIcon aria-hidden className="animate-spin" /> : <DownloadIcon aria-hidden />}
        {label}
      </Button>
      {state.error ? (
        <span role="alert" className="text-[11px] text-loss">
          {label} download failed: {state.error.toLowerCase()}
        </span>
      ) : null}
    </span>
  )
}

function Preview({ table }: { table: SavedRunTable }) {
  const rows = useMemo(() => table.rows.slice(0, PREVIEW_ROWS), [table.rows])
  const columns = useMemo<Column<Row>[]>(() => {
    const names = table.columns.length ? table.columns : rows[0] ? Object.keys(rows[0]) : []
    return names.map((c) => ({
      id: c,
      header: c,
      align: rows.some((r) => typeof r[c] === 'number') ? 'right' : 'left',
      cell: (r: Row) => cellText(r[c]),
      sortValue: (r: Row) => {
        const v = r[c]
        return typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean' ? v : null
      },
    }))
  }, [table.columns, rows])
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-muted-foreground">
        {fmtNum(table.total)} rows × {fmtNum(table.columns.length)} columns
        {table.total > rows.length ? `; the first ${fmtNum(rows.length)} shown, the download has them all` : ''}
      </p>
      <DataTable
        label={`Table ${table.name}`}
        columns={columns}
        rows={rows}
        rowKey={(_, i) => String(i)}
        maxHeight={360}
        empty={<EmptyState title="This table has no rows" className="py-4" />}
      />
    </div>
  )
}

function TableItem({ runId, name }: { runId: string; name: string }) {
  const [open, setOpen] = useState(false)
  const table = useSavedRunTable(runId, name, { enabled: open })
  return (
    <li className="space-y-2 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="group inline-flex min-w-0 cursor-pointer items-center gap-1.5 rounded-sm text-left text-[13px] font-medium hover:text-primary"
        >
          <ChevronRightIcon aria-hidden className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-90')} />
          <TableIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="font-mono">{name}</span>
          <span className="sr-only">{open ? '(hide the preview)' : '(preview)'}</span>
        </button>
        {table.data ? <span className="text-[11px] text-muted-foreground">{fmtNum(table.data.total)} rows</span> : null}
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <DownloadButton runId={runId} name={name} format="csv" />
          <DownloadButton runId={runId} name={name} format="parquet" />
        </span>
      </div>
      {open ? (
        <QueryState query={table} skeleton={<TableSkeleton rows={5} />}>
          {(t) => <Preview table={t} />}
        </QueryState>
      ) : null}
    </li>
  )
}

/** A saved run's tables: each one previewed on demand, and downloadable as CSV or Parquet. */
export function RunTables({ runId, tables }: { runId: string; tables: string[] }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Tables</CardTitle>
          <CardDescription>
            Everything the run saved, as stored: open one to preview it, or download it whole (Parquet keeps the types; CSV
            opens anywhere).
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {tables.length ? (
          <ul aria-label="Tables" className="divide-y">
            {tables.map((name) => (
              <TableItem key={name} runId={runId} name={name} />
            ))}
          </ul>
        ) : (
          <EmptyState title="This run saved no tables" className="py-4">
            Its headline figures are above.
          </EmptyState>
        )}
      </CardContent>
    </Card>
  )
}
