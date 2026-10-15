import { ScaleIcon } from 'lucide-react'
import { useMemo } from 'react'
import type { GridRow, OptimizeResult, WalkForwardWindow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { KpiCard, KpiGrid } from '@/components/KpiCard'
import { Section } from '@/components/PageHeader'
import { EmptyState } from '@/components/states'
import { Card, CardContent } from '@/components/ui/card'
import { fmtCalendarDate, fmtNum, fmtPct, tone, toneClass } from '@/lib/format'
import {
  fmtMetric,
  fmtValue,
  gridHeatmap,
  gridMetricKeys,
  gridParams,
  isInfinite,
  isNegatedObjective,
  metric,
  num,
  objectiveValue,
  paramsText,
  signedMetric,
  testReturn,
  trainReturns,
  walkForwardSummary,
} from '../logic'
import { GridHeatmap, WindowReturnsChart } from './OptimizeCharts'

const METRIC_LABEL: Record<string, string> = {
  return_pct: 'Return',
  sharpe_ratio: 'Sharpe',
  max_drawdown: 'Max DD',
  max_drawdown_pct: 'Max DD',
  win_rate: 'Win rate',
  profit_factor: 'Profit factor',
  total_trades: 'Trades',
  alpha: 'Alpha',
}

const dateOf = (t: string) => fmtCalendarDate(t.slice(0, 10))

/** A note that the grid's best row is in-sample, and walk-forward is the honest measure. */
function HonestyNote({ method }: { method: OptimizeResult['method'] }) {
  return (
    <p className="flex items-start gap-1.5 rounded-md border-l-2 border-brand bg-card px-3 py-2 text-xs leading-relaxed text-muted-foreground">
      <ScaleIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-brand" />
      <span>
        {method === 'grid' ? (
          <>
            <strong className="text-foreground">In-sample.</strong> The best row was picked on the same data it is scored on, so
            it overstates what to expect. Walk-forward is the honest out-of-sample measure: it picks parameters on each training
            span and scores them on the unseen span that follows.
          </>
        ) : (
          <>
            <strong className="text-foreground">Walk-forward is the honest out-of-sample measure.</strong> Each window’s parameters
            are chosen on its training span only, then scored on the unseen test span that follows; the test returns are what
            the strategy would have earned. Test returns far below the training ones mean the search fitted noise.
          </>
        )}
      </span>
    </p>
  )
}

interface RankedRow extends GridRow {
  rank: number
}

function GridView({ result, objectiveLabel }: { result: OptimizeResult; objectiveLabel: string }) {
  const rows = useMemo(() => result.rows ?? [], [result.rows])
  const ranked: RankedRow[] = useMemo(() => rows.map((r, i) => ({ ...r, rank: i + 1 })), [rows])
  const { all, varying } = useMemo(() => gridParams(rows), [rows])
  const metricKeys = useMemo(() => gridMetricKeys(rows), [rows])
  const heat = useMemo(() => gridHeatmap(rows, result.objective), [rows, result.objective])
  const negated = isNegatedObjective(result.objective)
  // the ranked value gets its own column unless a metric column already shows it as it is
  const rankedColumn = negated || !metricKeys.includes(result.objective)
  const rankedLabel = negated ? `${objectiveLabel} (negated)` : objectiveLabel
  const best = rows[0]
  if (!best) return <EmptyState title="The grid search returned no rows" className="py-8" />

  const columns: Column<RankedRow>[] = [
    { id: 'rank', header: '#', align: 'right', cell: (r) => fmtNum(r.rank), sortValue: (r) => r.rank },
    ...all.map(
      (k): Column<RankedRow> => ({
        id: `p:${k}`,
        header: k,
        align: 'right',
        cell: (r) => <span className={varying.includes(k) ? 'font-medium' : 'text-muted-foreground'}>{fmtValue(r.params[k])}</span>,
        sortValue: (r) => (typeof r.params[k] === 'number' ? (r.params[k] as number) : String(r.params[k] ?? '')),
      }),
    ),
    ...(rankedColumn
      ? [
          {
            id: 'objective',
            header: rankedLabel,
            align: 'right' as const,
            headerClassName: 'text-foreground',
            cell: (r: RankedRow) => <span className="font-semibold">{fmtMetric(result.objective, objectiveValue(r, result.objective))}</span>,
            sortValue: (r: RankedRow) => objectiveValue(r, result.objective),
          },
        ]
      : []),
    ...metricKeys.map(
      (k): Column<RankedRow> => ({
        id: `m:${k}`,
        header: METRIC_LABEL[k] ?? k,
        align: 'right',
        headerClassName: k === result.objective ? 'text-foreground' : undefined,
        cell: (r) => {
          const v = metric(r.metrics, k)
          return (
            <span className={signedMetric(k) ? toneClass[tone(v, 2)] : k === result.objective ? 'font-semibold' : undefined}>
              {fmtMetric(k, v, isInfinite(r.metrics, k))}
            </span>
          )
        },
        sortValue: (r) => metric(r.metrics, k),
      }),
    ),
  ]

  // as the metric reads (a drawdown positive), not as it was ranked
  const bestObjective = metric(best.metrics, result.objective) ?? objectiveValue(best, result.objective)
  const gridSize = num(result.summary.grid_size)
  return (
    <div className="space-y-4">
      <KpiGrid>
        <KpiCard
          label="Best parameters"
          value={
            <span className="block whitespace-normal font-mono text-[13px] leading-5" title={paramsText(best.params)}>
              {paramsText(best.params)}
            </span>
          }
          sub={`${fmtNum(rows.length)} tested${gridSize !== null && gridSize > rows.length ? ` of ${fmtNum(gridSize)}` : ''}`}
        />
        <KpiCard
          label={objectiveLabel}
          value={fmtMetric(result.objective, bestObjective, isInfinite(best.metrics, result.objective))}
          sub="the objective, best row"
        />
        <KpiCard label="Return" value={fmtPct(metric(best.metrics, 'return_pct'))} tone={tone(metric(best.metrics, 'return_pct'), 2)} />
        <KpiCard label="Max drawdown" value={fmtPct(metric(best.metrics, 'max_drawdown_pct', 'max_drawdown'), 2, false)} />
      </KpiGrid>
      <HonestyNote method="grid" />
      <Section title="Every combination" aside="Best first; click a header to sort">
        <DataTable
          label="Grid search results"
          columns={columns}
          rows={ranked}
          rowKey={(r) => String(r.rank)}
          initialSort={{ id: 'rank', desc: false }}
          rowClassName={(r) => (r.rank === 1 ? 'bg-[var(--row-highlight)]' : undefined)}
          maxHeight={420}
        />
      </Section>
      <Card>
        <CardContent>
          <Section
            title={heat ? `${rankedLabel} by ${heat.x} and ${heat.y}` : 'Heatmap'}
            aside={heat ? `${fmtNum(heat.cells.length)} cells${negated ? '; higher (smaller drawdown) is better' : ''}` : undefined}
          >
            {heat ? (
              <GridHeatmap heat={heat} objectiveLabel={rankedLabel} format={(v) => fmtMetric(result.objective, v)} />
            ) : (
              <p className="text-xs text-muted-foreground">
                A heatmap needs exactly two parameters that vary; this grid varies {varying.length === 0 ? 'none' : `${varying.length} (${varying.join(', ')})`}.
              </p>
            )}
          </Section>
        </CardContent>
      </Card>
    </div>
  )
}

function WalkForwardView({ result, objectiveLabel }: { result: OptimizeResult; objectiveLabel: string }) {
  const windows = result.windows ?? []
  const s = walkForwardSummary(windows, result.summary)
  const trainByWindow = new Map(trainReturns(windows, result.summary).map((v, i) => [windows[i]?.index, v]))
  const error = typeof result.summary.error === 'string' ? result.summary.error : null
  if (!windows.length) {
    return (
      <EmptyState title="The walk-forward produced no windows" tone="warning" className="py-8">
        {error ?? 'Check that the period holds enough data for the windows asked for.'}
      </EmptyState>
    )
  }
  const columns: Column<WalkForwardWindow>[] = [
    { id: 'index', header: 'Window', align: 'right', cell: (w) => `W${w.index}`, sortValue: (w) => w.index },
    { id: 'train', header: 'Train', cell: (w) => `${dateOf(w.train_start)} → ${dateOf(w.train_end)}`, sortValue: (w) => w.train_start },
    { id: 'test', header: 'Test', cell: (w) => `${dateOf(w.test_start)} → ${dateOf(w.test_end)}`, sortValue: (w) => w.test_start },
    {
      id: 'train_objective',
      header: `Train ${METRIC_LABEL[result.objective] ?? objectiveLabel}`,
      align: 'right',
      cell: (w) => fmtMetric(result.objective, w.train_objective ?? null),
      sortValue: (w) => w.train_objective,
    },
    {
      id: 'train_return',
      header: 'Train return',
      align: 'right',
      cell: (w) => <span className="text-muted-foreground">{fmtPct(trainByWindow.get(w.index))}</span>,
      sortValue: (w) => trainByWindow.get(w.index),
    },
    {
      id: 'test_return',
      header: 'Test return',
      align: 'right',
      cell: (w) => {
        const v = testReturn(w)
        return <span className={toneClass[tone(v, 2)]}>{fmtPct(v)}</span>
      },
      sortValue: (w) => testReturn(w),
    },
    {
      id: 'test_sharpe',
      header: 'Test Sharpe',
      align: 'right',
      cell: (w) => fmtNum(metric(w.test_metrics, 'sharpe_ratio'), 2),
      sortValue: (w) => metric(w.test_metrics, 'sharpe_ratio'),
    },
    { id: 'params', header: 'Chosen parameters', cell: (w) => <span className="font-mono text-[12px]">{paramsText(w.params)}</span> },
  ]
  return (
    <div className="space-y-4">
      <KpiGrid>
        <KpiCard
          label="Mean test return"
          value={fmtPct(s.meanTestReturn)}
          tone={tone(s.meanTestReturn, 2)}
          sub={`out of sample, ${fmtNum(s.windows)} windows`}
        />
        <KpiCard label="Mean train return" value={fmtPct(s.meanTrainReturn)} sub="in sample, for contrast" />
        <KpiCard label="Windows positive" value={fmtPct(s.positiveShare, 0, false)} sub="test return above zero" />
        {s.efficiency !== null ? (
          <KpiCard
            label="WF efficiency"
            value={fmtPct(s.efficiency * 100, 0, false)}
            hint="Test return per day as a share of train return per day (Pardo); below about 50% is a warning"
          />
        ) : null}
      </KpiGrid>
      <HonestyNote method="walk_forward" />
      {error ? <p className="text-xs text-warning">{error}</p> : null}
      <Section title="Windows" aside="Parameters chosen on each train span, scored on its test span">
        <DataTable label="Walk-forward windows" columns={columns} rows={windows} rowKey={(w) => String(w.index)} initialSort={{ id: 'index', desc: false }} />
      </Section>
      <Card>
        <CardContent>
          <Section title="Test return by window" aside={s.meanTestReturn !== null ? `mean ${fmtPct(s.meanTestReturn)}` : undefined}>
            <WindowReturnsChart windows={windows} mean={s.meanTestReturn} />
          </Section>
        </CardContent>
      </Card>
    </div>
  )
}

export function OptimizeResultView({ result, objectives }: { result: OptimizeResult; objectives: Record<string, string> }) {
  // "Sharpe Ratio (higher is better)" -> "Sharpe Ratio" for column headers
  const objectiveLabel = (objectives[result.objective] ?? METRIC_LABEL[result.objective] ?? result.objective).replace(/\s*\(.*\)\s*$/, '')
  return result.method === 'grid' ? (
    <GridView result={result} objectiveLabel={objectiveLabel} />
  ) : (
    <WalkForwardView result={result} objectiveLabel={objectiveLabel} />
  )
}
