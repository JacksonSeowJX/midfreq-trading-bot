import { Fragment } from 'react'
import type { SavedRunTable } from '@/api/types'
import { Section } from '@/components/PageHeader'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtMetric, signedMetric } from '@/features/runs/logic'
import { tone, toneClass } from '@/lib/format'
import { cn } from '@/lib/utils'
import { dayText, sampleComparison } from './logic'

const LABEL: Record<string, string> = {
  return_pct: 'Return',
  sharpe_ratio: 'Sharpe',
  max_drawdown: 'Max DD',
  max_drawdown_pct: 'Max DD',
  win_rate: 'Win rate',
  profit_factor: 'Profit factor',
  total_trades: 'Trades',
  alpha: 'Alpha',
}

function Value({ k, v, strong }: { k: string; v: number | null; strong?: boolean }) {
  return <span className={cn(signedMetric(k) && toneClass[tone(v, 2)], strong && 'font-semibold')}>{fmtMetric(k, v)}</span>
}

/**
 * A walk-forward's windows with each metric in sample (the train span its parameters were chosen
 * on) and out of sample (the test span after it) side by side, and the mean of each.
 */
export function SampleTable({ windows }: { windows: SavedRunTable }) {
  const { windows: rows, pairs } = sampleComparison(windows)
  if (!pairs.length) return null
  return (
    <Section
      title="In sample against out of sample"
      aside="Per window, and the mean over the windows"
      description="In sample: the training span the parameters were chosen on. Out of sample: the unseen span after it, scored with those parameters. The out-of-sample means are what the method expects; a large gap means the search fitted noise."
    >
      <div className="overflow-auto rounded-md border">
        <Table aria-label="In sample against out of sample">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead rowSpan={2}>Window</TableHead>
              <TableHead rowSpan={2}>Train → test</TableHead>
              {pairs.map((p) => (
                <TableHead key={p.key} colSpan={2} className="border-l text-center text-foreground">
                  {LABEL[p.key] ?? p.key}
                </TableHead>
              ))}
            </TableRow>
            <TableRow className="hover:bg-transparent">
              {pairs.map((p) => (
                <Fragment key={p.key}>
                  <TableHead className="border-l text-right">In sample</TableHead>
                  <TableHead className="text-right">Out of sample</TableHead>
                </Fragment>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((w, i) => (
              <TableRow key={`${w.index}-${i}`}>
                <TableCell className="num">W{w.index}</TableCell>
                <TableCell className="text-muted-foreground">
                  {dayText(w.train[0])} → {dayText(w.train[1])} → {dayText(w.test[1])}
                </TableCell>
                {pairs.map((p) => (
                  <Fragment key={p.key}>
                    <TableCell className="num border-l text-muted-foreground">
                      <Value k={p.key} v={p.inSample[i] ?? null} />
                    </TableCell>
                    <TableCell className="num">
                      <Value k={p.key} v={p.outOfSample[i] ?? null} />
                    </TableCell>
                  </Fragment>
                ))}
              </TableRow>
            ))}
            <TableRow className="bg-muted/60 font-semibold hover:bg-muted/60" aria-label="Mean over the windows">
              <TableCell colSpan={2}>Mean of {rows.length} windows</TableCell>
              {pairs.map((p) => (
                <Fragment key={p.key}>
                  <TableCell className="num border-l" data-testid={`mean-in-${p.key}`}>
                    <Value k={p.key} v={p.meanIn} strong />
                  </TableCell>
                  <TableCell className="num" data-testid={`mean-out-${p.key}`}>
                    <Value k={p.key} v={p.meanOut} strong />
                  </TableCell>
                </Fragment>
              ))}
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </Section>
  )
}
