import type { ForwardTrade, Holding } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { EmptyState } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { fmtNum, fmtPct, tone, toneClass } from '@/lib/format'

/** A signed P&L cell: "+1,234" in green, "-56" in red, blank when there is none. */
export function PnlCell({ value, decimals = 0, pct = false }: { value: number | null | undefined; decimals?: number; pct?: boolean }) {
  if (value === null || value === undefined) return null
  return (
    <span className={toneClass[tone(value, decimals)]}>{pct ? fmtPct(value, decimals) : fmtNum(value, decimals, true)}</span>
  )
}

export function SideBadge({ side }: { side: string }) {
  return side === 'BUY' ? <Badge variant="gain">BUY</Badge> : <Badge variant="loss">{side}</Badge>
}

function tradeColumns(ccy: string): Column<ForwardTrade>[] {
  return [
    { id: 'date', header: 'Date (New York)', cell: (t) => t.date, sortValue: (t) => t.date },
    {
      id: 'candle',
      header: 'Decided on candle',
      cell: (t) => <span className="text-muted-foreground">{t.decided_on_candle}</span>,
      sortValue: (t) => t.decided_on_candle,
    },
    { id: 'side', header: 'Side', cell: (t) => <SideBadge side={t.side} />, sortValue: (t) => t.side },
    { id: 'stock', header: 'Stock', cell: (t) => <span className="font-medium">{t.stock}</span>, sortValue: (t) => t.stock },
    { id: 'shares', header: 'Shares', align: 'right', cell: (t) => fmtNum(t.shares, 0), sortValue: (t) => t.shares },
    { id: 'price', header: 'Price', align: 'right', cell: (t) => fmtNum(t.price, 2), sortValue: (t) => t.price },
    { id: 'value', header: `Value (${ccy})`, align: 'right', cell: (t) => fmtNum(t.value, 0), sortValue: (t) => t.value },
    {
      id: 'rpnl',
      header: `Realised P&L (${ccy})`,
      align: 'right',
      cell: (t) => <PnlCell value={t.realised_pnl} />,
      sortValue: (t) => t.realised_pnl,
    },
    {
      id: 'rpct',
      header: 'Realised %',
      align: 'right',
      cell: (t) => <PnlCell value={t.realised_pct} decimals={1} pct />,
      sortValue: (t) => t.realised_pct,
    },
  ]
}

/** Every fill the bot booked, newest first, with the profit realised on each sale. */
export function TradesTable({ trades, currency }: { trades: ForwardTrade[]; currency: string }) {
  if (trades.length === 0) return <EmptyState title="No trades recorded yet" />
  return (
    <div className="space-y-1.5">
      <DataTable
        label="Forward-test trades"
        columns={tradeColumns(currency)}
        rows={[...trades].reverse()}
        rowKey={(t, i) => `${t.date}|${t.decided_on_candle}|${t.side}|${t.stock}|${i}`}
        maxHeight={420}
      />
      <p className="text-xs text-muted-foreground">
        Newest first. Realised profit is on each sale, before fees
        {currency === 'USD' ? ' (about US$0.99 per order)' : ''}.
      </p>
    </div>
  )
}

function holdingColumns(ccy: string): Column<Holding>[] {
  return [
    { id: 'stock', header: 'Stock', cell: (h) => <span className="font-medium">{h.stock}</span>, sortValue: (h) => h.stock },
    { id: 'shares', header: 'Shares', align: 'right', cell: (h) => fmtNum(h.shares, 0), sortValue: (h) => h.shares },
    { id: 'bought', header: 'Bought at', align: 'right', cell: (h) => fmtNum(h.bought_at, 2), sortValue: (h) => h.bought_at },
    { id: 'last', header: 'Last price', align: 'right', cell: (h) => fmtNum(h.last_price, 2), sortValue: (h) => h.last_price },
    { id: 'value', header: `Value (${ccy})`, align: 'right', cell: (h) => fmtNum(h.value, 0), sortValue: (h) => h.value },
    {
      id: 'upnl',
      header: `Unrealised P&L (${ccy})`,
      align: 'right',
      cell: (h) => <PnlCell value={h.unrealised_pnl} />,
      sortValue: (h) => h.unrealised_pnl,
    },
    {
      id: 'upct',
      header: 'Unrealised %',
      align: 'right',
      cell: (h) => <PnlCell value={h.unrealised_pct} decimals={1} pct />,
      sortValue: (h) => h.unrealised_pct,
    },
  ]
}

export function HoldingsTable({ holdings, currency }: { holdings: Holding[]; currency: string }) {
  if (holdings.length === 0) return <EmptyState title="No positions held at the end of the last recorded session" />
  return (
    <DataTable
      label="Forward-test holdings"
      columns={holdingColumns(currency)}
      rows={holdings}
      rowKey={(h) => h.stock}
      maxHeight={420}
    />
  )
}
