import { useMemo } from 'react'
import type { CompareResult } from '@/api/types'
import { MultiLineChart } from '@/components/charts/MultiLineChart'
import { useSeriesColors } from '@/components/charts/palette'
import { DataTable, type Column } from '@/components/DataTable'
import { Section } from '@/components/PageHeader'
import { EmptyState } from '@/components/states'
import { Card, CardContent } from '@/components/ui/card'
import { fmtMoney, fmtNum, fmtPct, tone, toneClass, type Tone } from '@/lib/format'
import { cn } from '@/lib/utils'
import { isBasket } from '../form'
import { bestPerColumn, COMPARE_COLUMNS, compareLines, entityOrder, paramsText, type CompareLine, type Figure, type RunSummary } from '../logic'

function format(id: Figure, v: number | null, ccy: string): { text: string; tone: Tone } {
  switch (id) {
    case 'returnPct':
    case 'alphaPct':
    case 'benchmarkPct':
      return { text: fmtPct(v), tone: tone(v, 2) }
    case 'maxDrawdownPct':
      return { text: fmtPct(v, 2, false), tone: 'flat' }
    case 'winRatePct':
      return { text: fmtPct(v, 1, false), tone: 'flat' }
    case 'sharpe':
    case 'profitFactor':
      return { text: fmtNum(v, 2), tone: 'flat' }
    case 'trades':
      return { text: fmtNum(v), tone: 'flat' }
    case 'finalValue':
      return { text: fmtMoney(v, ccy), tone: 'flat' }
  }
}

/** Every strategy on the same data, side by side: metrics (best per column marked) and equity curves. */
export function CompareResultView({
  result,
  summary,
  requested = [],
}: {
  result: CompareResult
  summary: RunSummary
  /** the strategies in the order the run asked for them: their colours follow it */
  requested?: string[]
}) {
  const ccy = summary.currency
  const colors = useSeriesColors()
  const lines = useMemo(() => compareLines(result), [result])
  // a colour follows the strategy (its place in the request), never its rank
  const ordered = useMemo(() => entityOrder(lines.map((l) => l.strategy), requested), [lines, requested])
  const colorOf = useMemo(() => new Map(ordered.map((name, i) => [name, colors[i % colors.length]!])), [ordered, colors])
  const anyBasket = lines.some((l) => isBasket(l.strategy))
  const best = useMemo(() => bestPerColumn(lines), [lines])
  const shownColumns = COMPARE_COLUMNS.filter((c) => lines.some((l) => l.figures[c.id] !== null))
  const capital = summary.initialCapital ?? undefined

  const columns: Column<CompareLine>[] = [
    {
      id: 'strategy',
      header: 'Strategy',
      cell: (l) => (
        <span className="inline-flex items-center gap-2" title={paramsText(l.params)}>
          <span aria-hidden className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: colorOf.get(l.strategy) }} />
          <span className="font-medium">{l.strategy}</span>
        </span>
      ),
      sortValue: (l) => l.strategy,
    },
    ...shownColumns.map(
      (c): Column<CompareLine> => ({
        id: c.id,
        header: c.label,
        align: 'right',
        sortValue: (l) => l.figures[c.id],
        cell: (l) => {
          const v = l.figures[c.id]
          const f = format(c.id, v, ccy)
          const isBest = v !== null && best.get(c.id) === v
          return (
            <span
              data-best={isBest || undefined}
              // relative: keeps the sr-only text inside the table's scroll box
              className={cn('relative', toneClass[f.tone], isBest && 'rounded-sm bg-[var(--row-highlight)] px-1 font-semibold ring-1 ring-gain/30')}
            >
              {f.text}
              {isBest ? <span className="sr-only"> (best)</span> : null}
            </span>
          )
        },
      }),
    ),
  ]

  const chartLines = ordered
    .filter((name) => (result.equity[name]?.length ?? 0) > 0)
    .map((name) => ({ name, color: colorOf.get(name)!, points: result.equity[name] ?? [] }))

  if (!lines.length) return <EmptyState title="No strategy produced a result" className="py-8" />

  return (
    <div className="space-y-4">
      <Section
        title="Metrics"
        aside="Best in each column outlined; drawdown lower is better"
        description={
          anyBasket ? 'Cross-sectional rows ran on the whole universe and are not like-for-like with single-stock rows.' : undefined
        }
      >
        <DataTable
          label="Strategy comparison"
          columns={columns}
          rows={lines}
          rowKey={(l) => l.strategy}
          initialSort={{ id: 'returnPct', desc: true }}
          maxHeight={400}
        />
      </Section>
      <Card>
        <CardContent>
          <Section title="Equity curves" aside={capital ? `starting from ${fmtMoney(capital, ccy)}` : undefined}>
            {chartLines.length ? (
              <MultiLineChart
                lines={chartLines}
                currency={ccy}
                baseline={capital}
                label={`Equity curves of ${chartLines.length} strategies`}
              />
            ) : (
              <EmptyState title="No equity curves in this result" className="py-6" />
            )}
          </Section>
        </CardContent>
      </Card>
    </div>
  )
}
