import type { WalkForwardWindow } from '@/api/types'
import { EChart, type EChartOption } from '@/components/charts/EChart'
import { alpha, baseOption, HEAT, useChartPalette } from '@/components/charts/palette'
import { fmtPct } from '@/lib/format'
import { useTheme } from '@/lib/preferences'
import { testReturn, type Heatmap } from '../logic'

/**
 * The objective over the two parameters the grid varies. Values on both sides of zero use the
 * diverging pair (red below, blue above, grey at zero); one-signed values a single blue ramp.
 * Each cell carries its value when the grid is small enough to read them.
 */
export function GridHeatmap({
  heat,
  objectiveLabel,
  format,
}: {
  heat: Heatmap
  objectiveLabel: string
  format: (v: number) => string
}) {
  const p = useChartPalette()
  const scheme = HEAT[useTheme().resolved]
  const base = baseOption(p)
  const diverging = heat.min < 0 && heat.max > 0
  const bound = Math.max(Math.abs(heat.min), Math.abs(heat.max))
  const labelled = heat.cells.length <= 64
  const option: EChartOption = {
    ...base,
    legend: { show: false }, // the visual map is the key
    grid: { left: 64, right: 24, top: 16, bottom: 72 },
    tooltip: {
      ...base.tooltip,
      trigger: 'item',
      formatter: (params: { data: [number, number, number] }) => {
        const [xi, yi, v] = params.data
        return `${heat.x} = ${heat.xValues[xi]}<br/>${heat.y} = ${heat.yValues[yi]}<br/><b>${objectiveLabel}: ${format(v)}</b>`
      },
    },
    xAxis: { type: 'category', data: heat.xValues, name: heat.x, nameLocation: 'middle', nameGap: 28, ...base.axis, splitLine: { show: false } },
    yAxis: { type: 'category', data: heat.yValues, name: heat.y, nameLocation: 'end', ...base.axis, splitLine: { show: false } },
    visualMap: {
      type: 'continuous',
      min: diverging ? -bound : heat.min,
      max: diverging ? bound : heat.max,
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: 4,
      itemHeight: 160,
      itemWidth: 10,
      text: [format(diverging ? bound : heat.max), format(diverging ? -bound : heat.min)],
      textStyle: { color: p.muted, fontSize: 11 },
      inRange: { color: diverging ? [scheme.neg, scheme.mid, scheme.pos] : [...scheme.seq] },
    },
    series: [
      {
        type: 'heatmap',
        data: heat.cells,
        itemStyle: { borderColor: p.surface, borderWidth: 2, borderRadius: 2 },
        label: { show: labelled, fontSize: 10, color: p.text, formatter: (x: { data: [number, number, number] }) => format(x.data[2]) },
        emphasis: { itemStyle: { borderColor: p.text, borderWidth: 1 } },
      },
    ],
  }
  return <EChart option={option} height={Math.min(520, 120 + heat.yValues.length * 34)} label={`${objectiveLabel} by ${heat.x} and ${heat.y}`} />
}

/** Out-of-sample return of each walk-forward window, with the mean across windows. */
export function WindowReturnsChart({ windows, mean }: { windows: WalkForwardWindow[]; mean: number | null }) {
  const p = useChartPalette()
  const base = baseOption(p)
  const data = windows.map((w) => {
    const v = testReturn(w) ?? 0
    return { value: v, itemStyle: { color: v >= 0 ? alpha(p.gain, 0.85) : alpha(p.loss, 0.85), borderRadius: v >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } }
  })
  const option: EChartOption = {
    ...base,
    legend: { show: false }, // one series: the title names it
    grid: { left: 56, right: 24, top: 24, bottom: 32 },
    tooltip: {
      ...base.tooltip,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v: number) => fmtPct(v),
    },
    xAxis: { type: 'category', data: windows.map((w) => `W${w.index}`), ...base.axis, splitLine: { show: false } },
    yAxis: { type: 'value', name: 'Test return (%)', ...base.axis, axisLabel: { ...base.axis.axisLabel, formatter: '{value}%' } },
    series: [
      {
        type: 'bar',
        name: 'Test return',
        barMaxWidth: 42,
        data,
        label: { show: windows.length <= 15, position: 'top', fontSize: 10, color: p.muted, formatter: (x: { value: number }) => fmtPct(x.value, 1) },
        markLine:
          mean === null
            ? undefined
            : {
                silent: true,
                symbol: 'none',
                lineStyle: { color: p.text, type: 'dashed', width: 1 },
                label: { position: 'insideEndTop', color: p.text, fontSize: 11, formatter: `mean ${fmtPct(mean)}` },
                data: [{ yAxis: mean }],
              },
      },
    ],
  }
  return <EChart option={option} height={260} label="Out-of-sample return per walk-forward window, with the mean" />
}
