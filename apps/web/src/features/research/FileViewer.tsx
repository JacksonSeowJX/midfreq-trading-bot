import { useMemo } from 'react'
import { useResearchFile } from '@/api/queries'
import type { ResearchFile, ResearchFileContent } from '@/api/types'
import { EChart, type EChartOption } from '@/components/charts/EChart'
import { baseOption, useChartPalette } from '@/components/charts/palette'
import { DataTable, type Column } from '@/components/DataTable'
import { Section } from '@/components/PageHeader'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { fmtBytes, fmtDateTime, fmtNum } from '@/lib/format'
import { cellText, quickChartSpec, type QuickChartSpec } from './logic'
import { JsonTree } from './JsonTree'

type Row = Record<string, unknown>

const ROW_LIMIT = 5000 // the API's default page

function QuickChart({ rows, spec }: { rows: Row[]; spec: QuickChartSpec }) {
  const p = useChartPalette()
  const base = baseOption(p)
  const points = rows.filter((r) => typeof r[spec.y] === 'number')
  const option: EChartOption = {
    ...base,
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...base.tooltip,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (items: { name: string; value: number }[]) => {
        const it = items[0]
        return it ? `${it.name}<br/>${spec.y}: ${cellText(it.value)}` : ''
      },
    },
    xAxis: {
      type: 'category',
      data: points.map((r) => String(r[spec.x])),
      ...base.axis,
      axisLabel: { ...base.axis.axisLabel, rotate: points.length > 12 ? 45 : 0, interval: 0, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: { type: 'value', ...base.axis },
    series: [
      {
        type: 'bar',
        barMaxWidth: 24,
        data: points.map((r) => {
          const v = r[spec.y] as number
          return { value: v, itemStyle: { color: v >= 0 ? p.gain : p.loss, borderRadius: v >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } }
        }),
        markLine: {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: { color: p.muted, type: 'solid', width: 1 },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  }
  return <EChart option={option} height={320} label={`${spec.y} by ${spec.x}`} />
}

function CsvView({ content }: { content: ResearchFileContent }) {
  const rows = useMemo(() => (content.rows ?? []) as Row[], [content.rows])
  const names = useMemo(
    () => content.columns ?? (rows[0] ? Object.keys(rows[0]) : []),
    [content.columns, rows],
  )
  const columns = useMemo<Column<Row>[]>(
    () =>
      names.map((c) => {
        const numeric = rows.some((r) => typeof r[c] === 'number')
        return {
          id: c,
          header: c,
          align: numeric ? 'right' : 'left',
          cell: (r: Row) => cellText(r[c]),
          sortValue: (r: Row) => {
            const v = r[c]
            return typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean' ? v : null
          },
        }
      }),
    [names, rows],
  )
  const spec = useMemo(() => quickChartSpec(names, rows), [names, rows])
  return (
    <div className="space-y-4">
      <DataTable
        label={content.name}
        columns={columns}
        rows={rows}
        rowKey={(_, i) => String(i)}
        maxHeight={460}
        empty={<EmptyState title="This file has no rows" />}
      />
      {spec ? (
        <Section title={`Quick view: ${spec.y} by ${spec.x}`}>
          <QuickChart rows={rows} spec={spec} />
        </Section>
      ) : null}
    </div>
  )
}

/** One results file: a sortable table for CSV, a collapsible tree for JSON. */
export function FileViewer({ file, title }: { file: ResearchFile; title: string }) {
  const content = useResearchFile(file.name)
  const rowCount = content.data?.rows?.length
  return (
    <Card className="min-w-0">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="truncate">{title}</CardTitle>
          <CardDescription className="truncate">
            <span className="font-mono">{file.name}</span> · {fmtBytes(file.size)} · modified {fmtDateTime(file.modified)}
            {rowCount !== undefined
              ? ` · ${fmtNum(rowCount)} rows${rowCount >= ROW_LIMIT ? ` (first ${fmtNum(ROW_LIMIT)})` : ''}`
              : ''}
          </CardDescription>
        </div>
        <Badge variant="outline" className="uppercase">
          {file.kind}
        </Badge>
      </CardHeader>
      <CardContent>
        <QueryState
          query={content}
          skeleton={<TableSkeleton rows={10} />}
          notFound={<EmptyState title="File not found">It may have been renamed or removed from results/.</EmptyState>}
        >
          {(c) => (c.kind === 'csv' ? <CsvView content={c} /> : <JsonTree data={c.data} />)}
        </QueryState>
      </CardContent>
    </Card>
  )
}
