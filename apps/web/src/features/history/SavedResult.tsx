import { FileQuestionIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useSavedRunTable } from '@/api/queries'
import type { SavedRunDetail, SavedRunTable } from '@/api/types'
import { KpiGridSkeleton } from '@/components/KpiCard'
import { ChartSkeleton, EmptyState, ErrorState } from '@/components/states'
import { BacktestResultView } from '@/features/runs/results/BacktestResultView'
import { CompareResultView } from '@/features/runs/results/CompareResultView'
import { OptimizeResultView } from '@/features/runs/results/OptimizeResultView'
import {
  backtestFromTables,
  compareFromTables,
  compareOrder,
  gridFromTable,
  savedRunSummary,
  VIEW_TABLES,
  walkForwardFromTable,
} from './logic'
import { SampleTable } from './SampleTable'

function ResultSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading the result">
      <KpiGridSkeleton />
      <ChartSkeleton />
    </div>
  )
}

function NoView({ children }: { children: ReactNode }) {
  return (
    <EmptyState icon={FileQuestionIcon} title="No result view for this run" className="py-8">
      {children}
    </EmptyState>
  )
}

/**
 * A saved run's result, in the view its kind has on the run pages, rebuilt from its tables:
 * a backtest's KPIs, equity curve and trades; a comparison's table and curves; a grid search's
 * table and heatmap; a walk-forward's windows, with in sample against out of sample.
 */
export function SavedResult({ detail }: { detail: SavedRunDetail }) {
  const { kind, run_id: id } = detail.summary
  const [first, second] = VIEW_TABLES[kind]
  const has = (name?: string) => !!name && detail.tables.includes(name)
  const a = useSavedRunTable(id, first ?? '', { enabled: has(first) })
  const b = useSavedRunTable(id, second ?? '', { enabled: has(second) })

  const loading = (has(first) && a.isPending) || (has(second) && b.isPending)
  const failed = a.isError ? a : b.isError ? b : null
  if (failed) return <ErrorState error={failed.error} onRetry={() => void failed.refetch()} />
  if (loading) return <ResultSkeleton />
  const t1: SavedRunTable | undefined = has(first) ? a.data : undefined
  const t2: SavedRunTable | undefined = has(second) ? b.data : undefined

  switch (kind) {
    case 'backtest':
      if (!t1 && !t2) return <NoView>It was saved without an equity or a trades table.</NoView>
      return (
        <BacktestResultView
          result={backtestFromTables(detail, t1, t2)}
          summary={savedRunSummary(detail)}
          noChart="The price chart is not saved with a run; run it again on the Backtest page to see one."
        />
      )
    case 'compare':
      if (!t1) return <NoView>It was saved without a strategies table.</NoView>
      return <CompareResultView result={compareFromTables(t1, t2)} summary={savedRunSummary(detail)} requested={compareOrder(detail)} />
    case 'grid_search': {
      if (!t1) return <NoView>It was saved without a grid table.</NoView>
      const result = gridFromTable(detail, t1)
      return <OptimizeResultView result={result} objectives={objectives(detail, result.objective)} />
    }
    case 'walk_forward': {
      if (!t1) return <NoView>It was saved without a windows table.</NoView>
      const result = walkForwardFromTable(detail, t1)
      return (
        <div className="space-y-5">
          <OptimizeResultView result={result} objectives={objectives(detail, result.objective)} />
          <SampleTable windows={t1} />
        </div>
      )
    }
  }
}

function objectives(detail: SavedRunDetail, objective: string): Record<string, string> {
  const label = detail.meta.objective_label
  return typeof label === 'string' && label ? { [objective]: label } : {}
}
