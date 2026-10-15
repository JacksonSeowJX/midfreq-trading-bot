import { useSession } from '@/api/queries'
import type { SessionDetail, SessionTrade } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { KpiCard, KpiGrid, KpiGridSkeleton } from '@/components/KpiCard'
import { Section } from '@/components/PageHeader'
import { SourceTag } from '@/components/SourcePanel'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  fmtDateTime,
  fmtDuration,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtShortDateTime,
  tone,
} from '@/lib/format'
import { SideBadge } from './ForwardTestTables'
import { StatusBadge } from './StatusBadge'

const tradeColumns: Column<SessionTrade>[] = [
  { id: 'time', header: 'Time', cell: (t) => fmtShortDateTime(t.timestamp), sortValue: (t) => t.timestamp },
  { id: 'symbol', header: 'Symbol', cell: (t) => <span className="font-medium">{t.symbol}</span>, sortValue: (t) => t.symbol },
  { id: 'side', header: 'Side', cell: (t) => <SideBadge side={t.side} />, sortValue: (t) => t.side },
  { id: 'qty', header: 'Qty', align: 'right', cell: (t) => fmtNum(t.qty, 0), sortValue: (t) => t.qty },
  { id: 'price', header: 'Price', align: 'right', cell: (t) => fmtNum(t.price, 2), sortValue: (t) => t.price },
  { id: 'value', header: 'Value', align: 'right', cell: (t) => fmtNum(t.qty * t.price, 0), sortValue: (t) => t.qty * t.price },
]

function SessionBody({ detail, currency }: { detail: SessionDetail; currency: string }) {
  const s = detail.summary
  const start = detail.equity[0]
  const end = detail.equity.length > 1 ? detail.equity.at(-1) : undefined
  const change = start && end ? end.value - start.value : undefined
  const changePct = start && end && start.value ? ((end.value - start.value) / start.value) * 100 : undefined
  const events = Object.entries(detail.event_counts).sort((a, b) => b[1] - a[1])
  return (
    <>
      <KpiGrid className="lg:grid-cols-3">
        <KpiCard label="Started" value={fmtShortDateTime(s.started_at)} sub={s.timeframe ? `${s.timeframe} candles` : undefined} />
        <KpiCard label="Ended" value={s.ended_at ? fmtShortDateTime(s.ended_at) : 'No end yet'} sub={fmtDuration(s.started_at, s.ended_at)} />
        <KpiCard label="Status" value={<StatusBadge session={s} />} />
        <KpiCard label="Symbols" value={fmtNum(s.symbols)} />
        <KpiCard label="Candles" value={fmtNum(s.candles)} />
        <KpiCard label="Trades" value={fmtNum(s.trades)} />
      </KpiGrid>

      <Section
        title="Account total"
        description="The broker's account total as the bot recorded it at the session's start and end."
      >
        {start ? (
          <KpiGrid className="lg:grid-cols-3">
            <KpiCard label="At start" value={fmtMoney(start.value, currency)} sub={fmtShortDateTime(start.timestamp)} />
            <KpiCard
              label="At end"
              value={end ? fmtMoney(end.value, currency) : '—'}
              sub={end ? fmtShortDateTime(end.timestamp) : 'no end recorded'}
            />
            <KpiCard
              label="Change"
              value={change !== undefined ? fmtMoney(change, currency, 0, true) : '—'}
              tone={tone(change)}
              sub={changePct !== undefined ? fmtPct(changePct, 3) : undefined}
              subTone={tone(changePct, 3)}
            />
          </KpiGrid>
        ) : (
          <EmptyState title="No account total recorded for this session" className="py-4" />
        )}
      </Section>

      <Section title="Trades" aside={`${detail.trades.length} fills`}>
        <DataTable
          label="Session trades"
          columns={tradeColumns}
          rows={detail.trades}
          rowKey={(t, i) => `${t.timestamp}|${t.symbol}|${i}`}
          maxHeight={320}
          empty={<EmptyState title="No trades in this session" className="py-4" />}
        />
      </Section>

      <Section title="Events logged">
        {events.length ? (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-3">
            {events.map(([type, n]) => (
              <div key={type} className="flex justify-between gap-3 border-b border-dashed py-0.5">
                <dt className="truncate font-mono text-xs text-muted-foreground">{type}</dt>
                <dd className="tabular-nums">{fmtNum(n)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-xs text-muted-foreground">No events.</p>
        )}
      </Section>
    </>
  )
}

/** One recorded session in a side drawer: its summary, account totals, fills and event counts. */
export function SessionDrawer({
  sessionId,
  currency,
  onClose,
}: {
  sessionId: string | null
  currency: string
  onClose: () => void
}) {
  const detail = useSession(sessionId)
  const s = detail.data?.summary
  return (
    <Sheet open={!!sessionId} onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent>
        <SheetHeader>
          <div>
            <SourceTag source="bot" />
          </div>
          <SheetTitle>{s ? `${s.strategy} · ${s.market}` : 'Session'}</SheetTitle>
          <SheetDescription className="font-mono">
            {sessionId}
            {s?.started_at ? ` · started ${fmtDateTime(s.started_at)}` : ''}
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          <QueryState
            query={detail}
            skeleton={
              <>
                <KpiGridSkeleton count={6} />
                <TableSkeleton rows={5} />
              </>
            }
            notFound={<EmptyState title="No such session">It may have been renamed or removed.</EmptyState>}
          >
            {(d) => <SessionBody detail={d} currency={currency} />}
          </QueryState>
        </div>
      </SheetContent>
    </Sheet>
  )
}
