import type { RealityCheckConfig, ResultRow } from '@/api/types'
import { EChart, type EChartOption } from '@/components/charts/EChart'
import { alpha, baseOption, useChartPalette } from '@/components/charts/palette'
import { fmtPct } from '@/lib/format'
import type { FamilyStat } from './logic'

/** An axis extent on a round step (1, 2, 2.5 or 5 x 10^n), about six ticks across. */
function niceExtent(min: number, max: number) {
  const raw = (max - min || 1) / 6
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag
  const lo = Math.floor(min / step) * step
  let hi = Math.ceil(max / step) * step
  if (hi - max < step * 0.25) hi += step // room for the line's label
  return { lo, hi, step }
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c)
}

/**
 * Config A against config B, one dot per result. Only the top-right quadrant passes, so it is
 * shaded; passing results are larger diamonds as well as green, failing ones small red circles.
 */
export function ConfigScatter({ rows }: { rows: ResultRow[] }) {
  const p = useChartPalette()
  const base = baseOption(p)
  const values = rows.flatMap((r) => [r.config_a_oos, r.config_b_oos])
  const lo = Math.min(0, ...values) - 1
  const hi = Math.max(0, ...values) + 1
  const point = (r: ResultRow) => ({ value: [r.config_a_oos, r.config_b_oos], row: r })
  const tooltip = (params: { data: { row: ResultRow } }) => {
    const r = params.data.row
    return `<b>${esc(r.strategy)}</b> · ${esc(r.subject)}<br/>A ${fmtPct(r.config_a_oos)} (${r.config_a_consistency.toFixed(0)}% of windows +)<br/>B ${fmtPct(r.config_b_oos)} (${r.config_b_consistency.toFixed(0)}% of windows +)<br/>${r.robust ? 'Passes' : 'Fails'} the standard`
  }
  const option: EChartOption = {
    ...base,
    grid: { left: 56, right: 24, top: 36, bottom: 52 },
    legend: { ...base.legend, top: 0, right: 0, data: ['passed', 'failed'] },
    tooltip: { ...base.tooltip, trigger: 'item', formatter: tooltip },
    xAxis: {
      type: 'value',
      min: Math.floor(lo),
      max: Math.ceil(hi),
      name: 'Config A: mean out-of-sample return per window (%)',
      nameLocation: 'middle',
      nameGap: 30,
      ...base.axis,
    },
    yAxis: {
      type: 'value',
      min: Math.floor(lo),
      max: Math.ceil(hi),
      name: 'Config B (%)',
      nameLocation: 'end',
      ...base.axis,
    },
    series: [
      {
        name: 'failed',
        type: 'scatter',
        symbol: 'circle',
        symbolSize: 8,
        itemStyle: { color: alpha(p.loss, 0.55), borderColor: p.surface, borderWidth: 1 },
        data: rows.filter((r) => !r.robust).map(point),
        markArea: {
          silent: true,
          itemStyle: { color: alpha(p.gain, 0.07) },
          label: { show: true, position: 'insideTopRight', color: p.gain, fontSize: 11, formatter: 'both positive = can pass' },
          data: [[{ coord: [0, 0] }, { coord: [Math.ceil(hi), Math.ceil(hi)] }]],
        },
        markLine: {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: { color: p.muted, type: 'solid', width: 1 },
          data: [{ xAxis: 0 }, { yAxis: 0 }],
        },
      },
      {
        name: 'passed',
        type: 'scatter',
        symbol: 'diamond',
        symbolSize: 14,
        itemStyle: { color: p.gain, borderColor: p.surface, borderWidth: 2 },
        data: rows.filter((r) => r.robust).map(point),
        z: 3,
      },
    ],
  }
  return <EChart option={option} height={460} label="Config A against config B mean out-of-sample return, one point per result" />
}

/** Tested and passed per strategy family; the full numbers are in the table beside it. */
export function FamilyBars({ stats }: { stats: FamilyStat[] }) {
  const p = useChartPalette()
  const base = baseOption(p)
  const ordered = [...stats].reverse() // ECharts draws category axes bottom-up
  const option: EChartOption = {
    ...base,
    grid: { left: 8, right: 24, top: 30, bottom: 28, containLabel: true },
    legend: { ...base.legend, top: 0, right: 0, data: ['tested', 'passed'] },
    tooltip: { ...base.tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', name: 'number of results', nameLocation: 'middle', nameGap: 22, minInterval: 1, ...base.axis },
    yAxis: { type: 'category', data: ordered.map((s) => s.family), ...base.axis, splitLine: { show: false } },
    series: [
      {
        name: 'tested',
        type: 'bar',
        barMaxWidth: 18,
        itemStyle: { color: p.neutral, borderRadius: [0, 4, 4, 0] },
        data: ordered.map((s) => s.tested),
      },
      {
        name: 'passed',
        type: 'bar',
        barMaxWidth: 18,
        barGap: '-100%',
        itemStyle: { color: p.gain, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: p.muted, fontSize: 11, formatter: (x: { value: number }) => (x.value ? String(x.value) : '') },
        data: ordered.map((s) => s.passed),
      },
    ],
  }
  return <EChart option={option} height={Math.max(260, 40 * stats.length + 70)} label="Results tested and passed by strategy family" />
}

/**
 * One configuration's candidates by mean out-of-sample return. The winner is green, the rest
 * grey; the dotted red line is the 95th percentile of the best of the same number of
 * no-edge candidates, which the winner has to clear to pass.
 */
export function RealityCheckBars({ config }: { config: RealityCheckConfig }) {
  const p = useChartPalette()
  const base = baseOption(p)
  const entries = Object.entries(config.means) // ascending, as the API sends them
  const luck = config.luck_p95 ?? undefined
  // the axis must reach the luck line: a winner that falls short of an off-chart line would
  // look as if it had cleared it
  const values = [0, ...entries.map(([, v]) => v), ...(luck !== undefined ? [luck] : [])]
  const { lo, hi, step } = niceExtent(Math.min(...values), Math.max(...values))
  const lines: object[] = [{ xAxis: 0, lineStyle: { color: p.muted, type: 'solid', width: 1 }, label: { show: false } }]
  if (luck !== undefined) {
    lines.push({
      xAxis: luck,
      lineStyle: { color: p.loss, type: 'dotted', width: 2 },
      label: { show: true, position: 'end', color: p.loss, fontSize: 11, formatter: 'luck, 95th pct' },
    })
  }
  const option: EChartOption = {
    ...base,
    grid: { left: 8, right: 28, top: 34, bottom: 34, containLabel: true },
    tooltip: {
      ...base.tooltip,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (items: { name: string; value: number }[]) => {
        const it = items[0]
        return it ? `${esc(it.name)}<br/>${fmtPct(it.value)} per window` : ''
      },
    },
    xAxis: {
      type: 'value',
      min: lo,
      max: hi,
      interval: step,
      name: 'mean out-of-sample return per window (%)',
      nameLocation: 'middle',
      nameGap: 22,
      ...base.axis,
    },
    yAxis: { type: 'category', data: entries.map(([k]) => k), ...base.axis, splitLine: { show: false } },
    series: [
      {
        type: 'bar',
        barMaxWidth: 16,
        data: entries.map(([name, v]) => ({
          value: v,
          itemStyle: {
            color: name === config.best ? p.gain : p.neutral,
            borderRadius: v >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
          },
        })),
        markLine: { silent: true, symbol: 'none', data: lines },
      },
    ],
  }
  return (
    <EChart
      option={option}
      height={Math.max(240, 30 * entries.length + 80)}
      label={`${config.name}: mean out-of-sample return per window of each candidate`}
    />
  )
}
