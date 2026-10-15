import { DownloadIcon, SearchIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useOverview } from '@/api/queries'
import type { Overview } from '@/api/types'
import { DataTable } from '@/components/DataTable'
import { KpiCard, KpiGrid, KpiGridSkeleton } from '@/components/KpiCard'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { LabeledSwitch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fmtNum, fmtPct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { familyStats, filterRows, toCsv, type FamilyStat } from './logic'
import { ConfigScatter, FamilyBars } from './OverviewCharts'
import { ResultsTable } from './ResultsTable'
import { SearchCorrectionView } from './SearchCorrectionView'

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  a.click()
  URL.revokeObjectURL(url)
}

function FamilyChips({
  families,
  selected,
  onChange,
}: {
  families: string[]
  selected: Set<string>
  onChange: (s: Set<string>) => void
}) {
  const chip = 'h-6 cursor-pointer rounded-full border px-2.5 text-[11px] font-medium transition-colors'
  return (
    <div role="group" aria-label="Strategy family" className="flex flex-wrap gap-1.5">
      <button
        type="button"
        aria-pressed={selected.size === 0}
        onClick={() => onChange(new Set())}
        className={cn(chip, selected.size === 0 ? 'border-brand bg-brand/10 text-foreground' : 'text-muted-foreground hover:bg-accent')}
      >
        All families
      </button>
      {families.map((f) => {
        const on = selected.has(f)
        return (
          <button
            key={f}
            type="button"
            aria-pressed={on}
            onClick={() => {
              const next = new Set(selected)
              if (on) next.delete(f)
              else next.add(f)
              onChange(next)
            }}
            className={cn(chip, on ? 'border-brand bg-brand/10 text-foreground' : 'text-muted-foreground hover:bg-accent')}
          >
            {f}
          </button>
        )
      })}
    </div>
  )
}

const familyColumns = [
  { id: 'family', header: 'Family', cell: (s: FamilyStat) => s.family, sortValue: (s: FamilyStat) => s.family },
  { id: 'tested', header: 'Tested', align: 'right' as const, cell: (s: FamilyStat) => fmtNum(s.tested), sortValue: (s: FamilyStat) => s.tested },
  { id: 'passed', header: 'Passed', align: 'right' as const, cell: (s: FamilyStat) => fmtNum(s.passed), sortValue: (s: FamilyStat) => s.passed },
  { id: 'rate', header: 'Pass rate', align: 'right' as const, cell: (s: FamilyStat) => fmtPct(s.passRate, 1, false), sortValue: (s: FamilyStat) => s.passRate },
]

function OverviewBody({ data }: { data: Overview }) {
  const { summary, rows, search_correction: sc } = data
  const families = useMemo(() => [...new Set(rows.map((r) => r.family))].sort(), [rows])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [passedOnly, setPassedOnly] = useState(false)
  const view = useMemo(
    () => filterRows(rows, { families: selected, query, passedOnly }),
    [rows, selected, query, passedOnly],
  )
  const stats = useMemo(() => familyStats(view), [view])

  return (
    <div className="space-y-5">
      <section aria-label="Summary" className="space-y-2.5">
        <KpiGrid>
          <KpiCard label="Results tested" value={fmtNum(summary.total)} />
          <KpiCard
            label="Passed the standard"
            value={fmtNum(summary.passed)}
            sub={`${fmtPct(summary.pass_rate, 1, false)} of results`}
            tone={summary.passed > 0 ? 'gain' : 'flat'}
          />
          <KpiCard label="Studies" value={fmtNum(summary.studies)} />
          <KpiCard label="Strategy families" value={fmtNum(summary.families)} />
        </KpiGrid>
        <p className="rounded-md border-l-2 border-brand bg-card px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          <strong className="text-foreground">The standard:</strong> the same 3 years is split two ways, into 9 windows
          (config A) and 15 windows (config B). A result passes only if BOTH configs have a positive mean out-of-sample
          return AND at least 50% of their windows profitable.
        </p>
      </section>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Results</CardTitle>
            <CardDescription>
              {fmtNum(view.length)} of {fmtNum(rows.length)} shown. Results that passed are highlighted.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => download('all_results_overview.csv', toCsv(view))}
            disabled={view.length === 0}
          >
            <DownloadIcon /> CSV
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative w-full max-w-64">
              <SearchIcon className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground" aria-hidden />
              <Input
                aria-label="Search results"
                placeholder="Search strategy, stock, study"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="pl-8"
              />
            </div>
            <LabeledSwitch label="Show only results that passed" checked={passedOnly} onCheckedChange={setPassedOnly} />
          </div>
          <FamilyChips families={families} selected={selected} onChange={setSelected} />
          <Tabs defaultValue="table">
            <TabsList>
              <TabsTrigger value="table">Full table</TabsTrigger>
              <TabsTrigger value="scatter">Config A vs Config B</TabsTrigger>
              <TabsTrigger value="families">Pass rate by family</TabsTrigger>
            </TabsList>
            <TabsContent value="table">
              <ResultsTable rows={view} />
            </TabsContent>
            <TabsContent value="scatter">
              {view.length ? (
                <>
                  <p className="mb-1 text-xs text-muted-foreground">
                    Both configurations must be positive, so only the top-right quadrant passes. Hover a point for the
                    strategy, what it ran on and both configurations&apos; numbers.
                  </p>
                  <ConfigScatter rows={view} />
                </>
              ) : (
                <EmptyState title="No results match these filters" />
              )}
            </TabsContent>
            <TabsContent value="families">
              {stats.length ? (
                <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
                  <FamilyBars stats={stats} />
                  <DataTable label="Pass rate by family" columns={familyColumns} rows={stats} rowKey={(s) => s.family} />
                </div>
              ) : (
                <EmptyState title="No results match these filters" />
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Corrected for the search</CardTitle>
            <CardDescription>Passing the standard is not the end of the test.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {sc ? (
            <SearchCorrectionView sc={sc} />
          ) : (
            <EmptyState title="No Reality Check result found">
              Run <code className="font-mono">research/studies/reality_check_study.py</code>.
            </EmptyState>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default function OverviewPage() {
  const [includeSuperseded, setIncludeSuperseded] = useState(false)
  const overview = useOverview(!includeSuperseded)
  return (
    <>
      <PageHeader
        title="All results overview"
        description="Every backtest brought to the two-configuration validation standard, in one place."
        actions={
          <LabeledSwitch
            label="Include superseded runs (pre-audit studies)"
            checked={includeSuperseded}
            onCheckedChange={setIncludeSuperseded}
          />
        }
      />
      <div className={cn(overview.isPlaceholderData && 'opacity-60 transition-opacity')}>
        <QueryState
          query={overview}
          skeleton={
            <div className="space-y-4">
              <KpiGridSkeleton />
              <TableSkeleton rows={10} />
            </div>
          }
        >
          {(d) =>
            d.rows.length === 0 ? (
              <EmptyState title="No two-configuration results found in results/" />
            ) : (
              <OverviewBody data={d} />
            )
          }
        </QueryState>
      </div>
    </>
  )
}
