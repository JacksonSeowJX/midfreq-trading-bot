import {
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { SeriesPoint } from '@/api/types'
import { fmtMoney, fmtNum, fmtPct, tone, toneClass } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useChartPalette } from './palette'
import { toLine } from './time'

export interface LineSpec {
  name: string
  color: string
  points: SeriesPoint[]
}

/**
 * Several equity curves on one axis (Compare), with a dotted line at the starting capital.
 * The legend names every line and gives its value at the crosshair (or its last value), so a
 * line is never identified by colour alone.
 */
export function MultiLineChart({
  lines,
  currency,
  baseline,
  baselineLabel = 'initial capital',
  label,
  height = 320,
}: {
  lines: LineSpec[]
  currency: string
  baseline?: number
  baselineLabel?: string
  label: string
  height?: number
}) {
  const palette = useChartPalette()
  const container = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const data = useMemo(() => lines.map((l) => ({ ...l, data: toLine(l.points) })), [lines])
  const [hover, setHover] = useState<Map<string, number> | null>(null)

  useEffect(() => {
    const el = container.current
    if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: palette.muted,
        fontSize: 11,
        fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: palette.grid, style: LineStyle.Solid } },
      rightPriceScale: { borderColor: palette.axis, scaleMargins: { top: 0.1, bottom: 0.08 } },
      timeScale: { borderColor: palette.axis, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { priceFormatter: (p: number) => fmtNum(p, 0) },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: false, pinch: true },
    })
    const byName = new Map<ISeriesApi<'Line'>, string>()
    data.forEach((l, i) => {
      const s = chart.addSeries(LineSeries, {
        color: l.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      s.setData(l.data)
      byName.set(s, l.name)
      if (i === 0 && baseline !== undefined) {
        s.createPriceLine({
          price: baseline,
          color: palette.muted,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: '',
        })
      }
    })
    chart.timeScale().fitContent()
    const onMove = (param: MouseEventParams) => {
      if (param.time === undefined) return setHover(null)
      const m = new Map<string, number>()
      for (const [s, name] of byName) {
        const d = param.seriesData.get(s)
        if (d && 'value' in d && typeof d.value === 'number') m.set(name, d.value)
      }
      setHover(m)
    }
    chart.subscribeCrosshairMove(onMove)
    chartRef.current = chart
    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
    }
  }, [palette, data, baseline])

  return (
    <div className="space-y-2">
      <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {data.map((l) => {
          const v = hover?.get(l.name) ?? l.data.at(-1)?.value
          const vs = v !== undefined && baseline ? ((v - baseline) / baseline) * 100 : undefined
          return (
            <li key={l.name} className="inline-flex items-center gap-1.5">
              <span aria-hidden className="h-0.5 w-3.5 rounded-full" style={{ background: l.color }} />
              <span className="text-foreground">{l.name}</span>
              <span className="tabular-nums text-muted-foreground">{v !== undefined ? fmtMoney(v, currency) : ''}</span>
              {vs !== undefined ? <span className={cn('tabular-nums', toneClass[tone(vs, 2)])}>{fmtPct(vs)}</span> : null}
            </li>
          )
        })}
        {baseline !== undefined ? (
          <li className="inline-flex items-center gap-1.5 text-muted-foreground">
            <span aria-hidden className="w-3.5 border-t border-dotted border-current" />
            {baselineLabel} {fmtMoney(baseline, currency)}
          </li>
        ) : null}
      </ul>
      <div ref={container} role="img" aria-label={label} style={{ height }} className="w-full" />
    </div>
  )
}
