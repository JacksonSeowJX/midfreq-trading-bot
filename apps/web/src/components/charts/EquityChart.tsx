import {
  BaselineSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
  LineType,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtMoney, fmtNum, fmtPct, fmtShortDateTime, tone, toneClass } from '@/lib/format'
import { cn } from '@/lib/utils'
import { alpha, useChartPalette } from './palette'

export interface EquityPointLike {
  timestamp: string
  value: number
}

interface Plotted {
  time: UTCTimestamp
  value: number
  at: number // the real instant, ms
}

/**
 * lightweight-charts draws its time axis in UTC. Shift each instant by the viewer's UTC offset
 * at that moment so the axis and crosshair read in local wall-clock time.
 */
function toPlotted(points: EquityPointLike[]): Plotted[] {
  const byTime = new Map<number, Plotted>()
  for (const p of points) {
    const at = Date.parse(p.timestamp)
    if (Number.isNaN(at) || !Number.isFinite(p.value)) continue
    const local = Math.floor(at / 1000) - new Date(at).getTimezoneOffset() * 60
    byTime.set(local, { time: local as UTCTimestamp, value: p.value, at }) // last one wins
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time)
}

/**
 * An equity curve in the TradingView style. With a baseline (the capital allocated) the line
 * is green above it and red below, and a dotted price line marks the baseline itself, so the
 * colour is never the only cue. Hover shows the value at the crosshair.
 */
export function EquityChart({
  points,
  currency,
  baseline,
  baselineLabel,
  height = 280,
  step = false,
  pointMarkers = true,
  label,
}: {
  points: EquityPointLike[]
  currency: string
  baseline?: number
  baselineLabel?: string
  height?: number
  /** hold each value flat until the next point (a balance between sessions) */
  step?: boolean
  /** a dot on every point: right for a few sessions, clutter on a per-candle curve */
  pointMarkers?: boolean
  label: string
}) {
  const palette = useChartPalette()
  const container = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Baseline'> | ISeriesApi<'Line'> | null>(null)
  const data = useMemo(() => toPlotted(points), [points])
  const [hover, setHover] = useState<Plotted | null>(null)

  // build the chart; rebuilt when the theme or the baseline changes
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
      grid: {
        vertLines: { visible: false },
        horzLines: { color: palette.grid, style: LineStyle.Solid },
      },
      rightPriceScale: { borderColor: palette.axis, scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderColor: palette.axis, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: CrosshairMode.Magnet },
      localization: { priceFormatter: (p: number) => fmtNum(p, 0) },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: false, pinch: true },
    })
    const common = {
      lineWidth: 2 as const,
      lineType: step ? LineType.WithSteps : LineType.Simple,
      pointMarkersVisible: pointMarkers,
      pointMarkersRadius: 3,
      priceLineVisible: false,
    }
    const series =
      baseline !== undefined
        ? chart.addSeries(BaselineSeries, {
            ...common,
            baseValue: { type: 'price', price: baseline },
            topLineColor: palette.gain,
            topFillColor1: alpha(palette.gain, 0.14),
            topFillColor2: alpha(palette.gain, 0.02),
            bottomLineColor: palette.loss,
            bottomFillColor1: alpha(palette.loss, 0.02),
            bottomFillColor2: alpha(palette.loss, 0.14),
          })
        : chart.addSeries(LineSeries, { ...common, color: palette.bot })
    if (baseline !== undefined) {
      series.createPriceLine({
        price: baseline,
        color: palette.muted,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: baselineLabel ?? '',
      })
    }
    const onMove = (param: MouseEventParams) => {
      const d = param.time !== undefined ? param.seriesData.get(series) : undefined
      const value = d && 'value' in d && typeof d.value === 'number' ? d.value : undefined
      setHover(value !== undefined ? { time: param.time as UTCTimestamp, value, at: 0 } : null)
    }
    chart.subscribeCrosshairMove(onMove)
    chartRef.current = chart
    seriesRef.current = series
    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [palette, baseline, baselineLabel, step, pointMarkers])

  useEffect(() => {
    seriesRef.current?.setData(data.map(({ time, value }) => ({ time, value })))
    chartRef.current?.timeScale().fitContent()
  }, [data, palette, baseline, baselineLabel, step, pointMarkers])

  const last = data.at(-1)
  const shown = hover ?? last ?? null
  const shownAt = shown ? (hover ? data.find((d) => d.time === hover.time)?.at : last?.at) : undefined
  const vsBase = shown && baseline ? ((shown.value - baseline) / baseline) * 100 : undefined

  return (
    <div className="relative pt-5">
      <div className="pointer-events-none absolute left-1 top-0 z-10 flex flex-wrap items-baseline gap-x-3 text-xs">
        {shown ? (
          <>
            <span className="text-muted-foreground">{fmtShortDateTime(shownAt)}</span>
            <span className="font-semibold tabular-nums">{fmtMoney(shown.value, currency)}</span>
            {vsBase !== undefined ? (
              <span className={cn('tabular-nums', toneClass[tone(vsBase, 2)])}>
                {fmtPct(vsBase)} vs {baselineLabel ?? 'baseline'}
              </span>
            ) : null}
          </>
        ) : null}
      </div>
      <div ref={container} role="img" aria-label={label} style={{ height }} className="w-full" />
    </div>
  )
}
