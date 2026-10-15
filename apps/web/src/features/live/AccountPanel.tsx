import { PlugZapIcon, SatelliteDishIcon, TriangleAlertIcon } from 'lucide-react'
import { ToggleGroup as ToggleGroupPrimitive } from 'radix-ui'
import { useState } from 'react'
import { isApiError } from '@/api/client'
import { useAccount, useAccounts, useHealth } from '@/api/queries'
import type { AccountInfo, AccountView, Order, Position } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { KpiCard, KpiGrid, KpiGridSkeleton } from '@/components/KpiCard'
import { Section } from '@/components/PageHeader'
import { SourcePanel } from '@/components/SourcePanel'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { fmtAgo, fmtDateTime, fmtHourMinute, fmtMoney, fmtNum, fmtShortDateTime, tone, toneClass } from '@/lib/format'
import { useMarket } from '@/lib/preferences'
import { useNow } from '@/lib/useNow'
import { cn } from '@/lib/utils'
import { defaultAccount, SNAPSHOT_STALE_MS, snapshotAge } from './logic'

const ORDER_DAYS = 14

export function AccountSelector({
  accounts,
  value,
  onChange,
}: {
  accounts: AccountInfo[]
  value: string
  onChange: (id: string) => void
}) {
  return (
    <ToggleGroupPrimitive.Root
      type="single"
      aria-label="Broker account"
      value={value}
      onValueChange={(v) => {
        if (v) onChange(v)
      }}
      className="flex flex-wrap gap-2"
    >
      {accounts.map((a) => (
        <ToggleGroupPrimitive.Item
          key={a.id}
          value={a.id}
          aria-label={a.available ? a.label : `${a.label} (unavailable: ${a.reason || 'no reason given'})`}
          data-available={a.available}
          className={cn(
            'flex min-w-44 max-w-72 cursor-pointer flex-col items-start rounded-md border bg-card px-3 py-1.5 text-left transition-colors hover:bg-accent',
            'data-[state=on]:border-broker data-[state=on]:ring-1 data-[state=on]:ring-broker',
            !a.available && 'opacity-60',
          )}
        >
          <span className="flex w-full items-center gap-1.5 text-[13px] font-semibold">
            <span
              aria-hidden
              className={cn('size-1.5 shrink-0 rounded-full', a.available ? 'bg-gain' : 'bg-muted-foreground')}
            />
            <span className="truncate">{a.label}</span>
          </span>
          <span className="w-full truncate text-[11px] text-muted-foreground">
            {a.connector}
            {a.kind === 'snapshot' ? ' · VM snapshot' : ''}
            {a.available ? '' : ` · ${a.reason || 'unavailable'}`}
          </span>
        </ToggleGroupPrimitive.Item>
      ))}
    </ToggleGroupPrimitive.Root>
  )
}

/**
 * An account the VM publishes every few minutes rather than one read live: says so, and how
 * old the snapshot is, warning once it is older than SNAPSHOT_STALE_MS.
 */
export function SnapshotNotice({ asOf }: { asOf: string }) {
  const now = useNow(30_000)
  const age = snapshotAge(asOf, now)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="broker">
          <SatelliteDishIcon aria-hidden />
          Snapshot from the VM
        </Badge>
        <span data-testid="snapshot-age" className="tabular-nums">
          as of {fmtHourMinute(asOf)}
          {age ? `, ${fmtAgo(now - age.ageMs, now)}` : ''}
        </span>
      </div>
      {age?.stale ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-warning"
        >
          <TriangleAlertIcon className="size-3.5 shrink-0" aria-hidden />
          <span>
            This snapshot is more than {SNAPSHOT_STALE_MS / 60_000} minutes old: the VM may be offline or outside its schedule.
          </span>
        </div>
      ) : null}
    </div>
  )
}

export function AccountUnavailable({ label, reason }: { label: string; reason?: string }) {
  return (
    <EmptyState icon={PlugZapIcon} tone="warning" title={`${label} is unavailable`}>
      <p>{reason || 'The account cannot be read now.'}</p>
      <p className="mt-1">
        The bot record below is read from the session logs and does not need the broker connection.
      </p>
    </EmptyState>
  )
}

function sideBadge(side: string) {
  const s = side.toUpperCase()
  if (s.startsWith('BUY')) return <Badge variant="gain">{s}</Badge>
  if (s.startsWith('SELL')) return <Badge variant="loss">{s}</Badge>
  return <Badge>{s}</Badge>
}

function positionColumns(ccy: string): Column<Position>[] {
  return [
    { id: 'symbol', header: 'Symbol', cell: (p) => <span className="font-medium">{p.symbol}</span>, sortValue: (p) => p.symbol },
    { id: 'qty', header: 'Qty', align: 'right', cell: (p) => fmtNum(p.quantity, 0), sortValue: (p) => p.quantity },
    { id: 'avg', header: `Avg cost (${ccy})`, align: 'right', cell: (p) => fmtNum(p.average_price, 2), sortValue: (p) => p.average_price },
    { id: 'mkt', header: 'Last', align: 'right', cell: (p) => fmtNum(p.market_price, 2), sortValue: (p) => p.market_price },
    { id: 'value', header: `Value (${ccy})`, align: 'right', cell: (p) => fmtNum(p.market_value, 0), sortValue: (p) => p.market_value },
    {
      id: 'upnl',
      header: `Unrealised P&L (${ccy})`,
      align: 'right',
      cell: (p) => <span className={toneClass[tone(p.unrealized_pnl)]}>{fmtNum(p.unrealized_pnl, 0, true)}</span>,
      sortValue: (p) => p.unrealized_pnl,
    },
  ]
}

const orderColumns: Column<Order>[] = [
  { id: 'created', header: 'Created', cell: (o) => fmtShortDateTime(o.created), sortValue: (o) => o.created },
  { id: 'symbol', header: 'Symbol', cell: (o) => <span className="font-medium">{o.symbol}</span>, sortValue: (o) => o.symbol },
  { id: 'side', header: 'Side', cell: (o) => sideBadge(o.side), sortValue: (o) => o.side },
  { id: 'qty', header: 'Qty', align: 'right', cell: (o) => fmtNum(o.quantity, 0), sortValue: (o) => o.quantity },
  { id: 'filled', header: 'Filled', align: 'right', cell: (o) => fmtNum(o.filled_quantity, 0), sortValue: (o) => o.filled_quantity },
  { id: 'price', header: 'Limit', align: 'right', cell: (o) => fmtNum(o.price, 2), sortValue: (o) => o.price },
  { id: 'fill', header: 'Avg fill', align: 'right', cell: (o) => fmtNum(o.average_fill_price, 2), sortValue: (o) => o.average_fill_price },
  { id: 'status', header: 'Status', cell: (o) => <span className="text-muted-foreground">{o.status}</span>, sortValue: (o) => o.status },
]

export function AccountViewBody({ view }: { view: AccountView }) {
  const ccy = view.snapshot.currency
  const positions = [...view.positions].sort((a, b) => (b.market_value ?? 0) - (a.market_value ?? 0))
  const orders = [...view.orders].sort((a, b) => b.created.localeCompare(a.created))
  return (
    <>
      <KpiGrid>
        <KpiCard label="Total assets" value={fmtMoney(view.snapshot.total_assets, ccy, 2)} />
        <KpiCard label="Cash" value={fmtMoney(view.snapshot.cash, ccy, 2)} />
        <KpiCard label="Market value" value={fmtMoney(view.snapshot.market_value, ccy, 2)} />
        <KpiCard label="Open positions" value={fmtNum(view.positions.length)} />
      </KpiGrid>
      <Section title="Open positions">
        <DataTable
          label="Open positions"
          columns={positionColumns(ccy)}
          rows={positions}
          rowKey={(p) => p.instrument_id ?? p.symbol}
          maxHeight={300}
          empty={<EmptyState title="No open positions" className="py-5" />}
        />
      </Section>
      <Section title={`Recent orders (${ORDER_DAYS} days)`} aside={`${orders.length} orders`}>
        <DataTable
          label="Recent orders"
          columns={orderColumns}
          rows={orders}
          rowKey={(o) => o.order_id}
          maxHeight={300}
          empty={<EmptyState title={`No orders in the last ${ORDER_DAYS} days`} className="py-5" />}
        />
      </Section>
    </>
  )
}

function AccountBodySkeleton() {
  return (
    <>
      <KpiGridSkeleton />
      <TableSkeleton rows={4} />
    </>
  )
}

/** The broker's view of one account: its own totals, positions and orders. */
export function AccountPanel() {
  const { market } = useMarket()
  const accounts = useAccounts()
  const health = useHealth()
  const [picked, setPicked] = useState<string | null>(null)
  const list = accounts.data ?? []
  const current = list.find((a) => a.id === picked) ?? defaultAccount(list, market)
  const view = useAccount(current?.id ?? null, { enabled: !!current?.available, orderDays: ORDER_DAYS })
  const unavailable503 = !view.data && isApiError(view.error) && view.error.status === 503

  return (
    <SourcePanel
      source="broker"
      title="Paper account"
      description={
        current
          ? `${current.label} · ${current.connector}. Balance, positions and orders as the broker reports them.`
          : 'Balance, positions and orders as the broker reports them.'
      }
      aside={view.data ? `Broker as of ${fmtDateTime(view.data.as_of)}` : undefined}
    >
      <QueryState query={accounts} skeleton={<Skeleton className="h-12 w-full max-w-md" />}>
        {(all) =>
          all.length === 0 ? (
            health.data?.sources.accounts === false ? (
              <EmptyState title="No broker snapshot yet">
                Reading the broker account needs its gateway (Moomoo OpenD), which runs only on the
                trading VM. The VM publishes a read-only snapshot of the account every few minutes,
                and it appears here once the first one arrives. The bot record below is complete
                without it.
              </EmptyState>
            ) : (
              <EmptyState title="No broker accounts configured">
                Add one to the <code className="font-mono">accounts</code> section of{' '}
                <code className="font-mono">config/connections.yaml</code>.
              </EmptyState>
            )
          ) : (
            <AccountSelector accounts={all} value={current?.id ?? ''} onChange={setPicked} />
          )
        }
      </QueryState>
      {current ? (
        !current.available ? (
          <AccountUnavailable label={current.label} reason={current.reason} />
        ) : unavailable503 ? (
          <AccountUnavailable label={current.label} reason={isApiError(view.error) ? view.error.detail : undefined} />
        ) : (
          <QueryState query={view} skeleton={<AccountBodySkeleton />}>
            {(v) => (
              <>
                {current.kind === 'snapshot' ? <SnapshotNotice asOf={v.as_of} /> : null}
                <AccountViewBody view={v} />
              </>
            )}
          </QueryState>
        )
      ) : null}
    </SourcePanel>
  )
}
