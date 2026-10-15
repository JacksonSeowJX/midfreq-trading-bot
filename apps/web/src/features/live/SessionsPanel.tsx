import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { useSessions } from '@/api/queries'
import type { SessionSummary } from '@/api/types'
import { EquityChart } from '@/components/charts/EquityChart'
import { DataTable, type Column } from '@/components/DataTable'
import { KpiCard, KpiGrid } from '@/components/KpiCard'
import { Section } from '@/components/PageHeader'
import { SourcePanel } from '@/components/SourcePanel'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { currencyFor, fmtDuration, fmtMoney, fmtNum, fmtPct, fmtShortDateTime, tone } from '@/lib/format'
import type { Market } from '@/lib/preferences'
import { SessionDrawer } from './SessionDrawer'
import { accountEquity, sessionStatus } from './logic'
import { StatusBadge } from './StatusBadge'

/** Sessions the history reads: the API's maximum, so the equity curve spans the deployment. */
const SESSION_LIMIT = 500

function columns(ccy: string): Column<SessionSummary>[] {
  return [
    { id: 'started', header: 'Started', cell: (s) => fmtShortDateTime(s.started_at), sortValue: (s) => s.started_at },
    { id: 'length', header: 'Length', cell: (s) => fmtDuration(s.started_at, s.ended_at), sortValue: (s) => s.ended_at && s.started_at ? Date.parse(s.ended_at) - Date.parse(s.started_at) : null },
    { id: 'strategy', header: 'Strategy', cell: (s) => <span className="font-medium">{s.strategy}</span>, sortValue: (s) => s.strategy },
    { id: 'tf', header: 'TF', cell: (s) => s.timeframe ?? '—', sortValue: (s) => s.timeframe },
    { id: 'symbols', header: 'Symbols', align: 'right', cell: (s) => fmtNum(s.symbols), sortValue: (s) => s.symbols },
    { id: 'candles', header: 'Candles', align: 'right', cell: (s) => fmtNum(s.candles), sortValue: (s) => s.candles },
    { id: 'trades', header: 'Trades', align: 'right', cell: (s) => fmtNum(s.trades), sortValue: (s) => s.trades },
    {
      id: 'end_assets',
      header: `End assets (${ccy})`,
      align: 'right',
      cell: (s) => fmtNum(s.end_total_assets, 0),
      sortValue: (s) => s.end_total_assets,
    },
    { id: 'status', header: 'Status', cell: (s) => <StatusBadge session={s} />, sortValue: (s) => sessionStatus(s) },
  ]
}

function AccountEquity({ sessions, currency }: { sessions: SessionSummary[]; currency: string }) {
  const points = useMemo(() => accountEquity(sessions), [sessions])
  const candles = sessions.reduce((n, s) => n + s.candles, 0)
  const first = points[0]
  const last = points.at(-1)
  if (!first || !last || points.length < 2) {
    return <EmptyState title="Not enough recorded account totals yet to draw an equity curve" className="py-5" />
  }
  const changePct = ((last.value - first.value) / first.value) * 100
  const spanDays = Math.floor((Date.parse(last.timestamp) - Date.parse(first.timestamp)) / 86_400_000)
  return (
    <>
      <KpiGrid>
        <KpiCard label="Sessions" value={fmtNum(sessions.length)} />
        <KpiCard label="Candles recorded" value={fmtNum(candles)} />
        <KpiCard
          label="Account equity"
          value={fmtMoney(last.value, currency)}
          sub={`${fmtPct(changePct, 3)} since first session`}
          subTone={tone(changePct, 3)}
        />
        <KpiCard label="Span" value={`${fmtNum(spanDays)} days`} />
      </KpiGrid>
      <EquityChart
        points={points}
        currency={currency}
        baseline={first.value}
        baselineLabel="starting equity"
        step
        height={260}
        label={`Account equity over time, from ${fmtMoney(first.value, currency)} to ${fmtMoney(last.value, currency)}`}
      />
      <p className="text-xs text-muted-foreground">
        Total account value as reported by the broker at the start and end of each session. Flat stretches are days
        when no position was held or no session ran.
      </p>
    </>
  )
}

/** Every recorded session of the bot in this market, with the account totals they logged. */
export function SessionsPanel({ market }: { market: Market }) {
  const ccy = currencyFor(market)
  const sessions = useSessions(market, SESSION_LIMIT)
  const [params, setParams] = useSearchParams()
  const selected = params.get('session')
  const cols = useMemo(() => columns(ccy), [ccy])

  const select = (id: string | null) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        if (id) next.set('session', id)
        else next.delete('session')
        return next
      },
      { replace: true },
    )

  return (
    <SourcePanel
      source="bot"
      title="Session history"
      description="Every session the bot recorded for this market, newest first. Select a row for its fills and events."
      aside={sessions.data ? `${sessions.data.length} sessions` : undefined}
    >
      <QueryState query={sessions} skeleton={<TableSkeleton rows={8} />}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title={`No ${market} live sessions recorded yet`}>
              HK: <code className="font-mono">./ops/run_daily_candidates.sh</code>; US:{' '}
              <code className="font-mono">./ops/run_sp100_forward_test.sh</code>. Session logs from the VM reach this
              machine with <code className="font-mono">./ops/fetch_live_logs.sh</code>.
            </EmptyState>
          ) : (
            <div className="space-y-4">
              <DataTable
                label="Session history"
                columns={cols}
                rows={rows}
                rowKey={(s) => s.id}
                onRowClick={(s) => select(s.id)}
                selectedKey={selected}
                maxHeight={360}
              />
              <Section title="Account equity over time">
                <AccountEquity sessions={rows} currency={ccy} />
              </Section>
            </div>
          )
        }
      </QueryState>
      <SessionDrawer sessionId={selected} currency={ccy} onClose={() => select(null)} />
    </SourcePanel>
  )
}
