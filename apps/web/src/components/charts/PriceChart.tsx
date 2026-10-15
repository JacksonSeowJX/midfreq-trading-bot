import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type MouseEventParams,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Candle, Indicator } from '@/api/types'
import { fmtNum, fmtShortDateTime } from '@/lib/format'
import { alpha, useChartPalette, useSeriesColors } from './palette'
import { chartTime, toLine } from './time'

export interface ChartTrade {
  at: number
  side: 'buy' | 'sell'
  price: number
  qty: number
}

interface Bar {
  time: UTCTimestamp
  open: number
  high: number
  low: number
  close: number
  at: number
}

function toBars(candles: Candle[]): Bar[] {
  const byTime = new Map<number, Bar>()
  for (const c of candles) {
    const at = Date.parse(c.t)
    if (Number.isNaN(at) || ![c.o, c.h, c.l, c.c].every(Number.isFinite)) continue
    const time = chartTime(at)
    byTime.set(time, { time, open: c.o, high: c.h, low: c.l, close: c.c, at })
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time)
}

/** Each trade on the candle it happened in (the last candle at or before it). */
function toMarkers(bars: Bar[], trades: ChartTrade[], colors: { buy: string; sell: string }): SeriesMarker<UTCTimestamp>[] {
  if (!bars.length) return []
  const out: SeriesMarker<UTCTimestamp>[] = []
  for (const t of trades) {
    const time = chartTime(t.at)
    let lo = 0
    let hi = bars.length - 1
    if (time < bars[0]!.time) continue
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (bars[mid]!.time <= time) lo = mid
      else hi = mid - 1
    }
    const bar = bars[lo]!
    out.push(
      t.side === 'buy'
        ? { time: bar.time, position: 'belowBar', shape: 'arrowUp', color: colors.buy, size: 1 }
        : { time: bar.time, position: 'aboveBar', shape: 'arrowDown', color: colors.sell, size: 1 },
    )
  }
  return out.sort((a, b) => a.time - b.time)
}

/**
 * Candles with the strategy's indicators: price-pane ones drawn over the candles, lower-pane
 * ones (RSI, MACD, z-score) in a pane of their own below, sharing the time axis. Buys are up
 * arrows under the candle, sells down arrows above it, so the side never rests on colour.
 */
export function PriceChart({
  candles,
  indicators,
  trades,
  label,
  height = 420,
}: {
  candles: Candle[]
  indicators: Indicator[]
  trades: ChartTrade[]
  label: string
  height?: number
}) {
  const palette = useChartPalette()
  const series = useSeriesColors()
  const container = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const bars = useMemo(() => toBars(candles), [candles])
  const lower = indicators.filter((i) => i.pane === 'lower')
  const hasLower = lower.length > 0
  const colorOf = useMemo(() => {
    const map = new Map<string, string>()
    indicators.forEach((ind, i) => map.set(ind.name, series[i % series.length]!))
    return map
  }, [indicators, series])
  const [hover, setHover] = useState<Bar | null>(null)

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
        panes: { separatorColor: palette.axis, enableResize: true },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: palette.grid, style: LineStyle.Solid } },
      rightPriceScale: { borderColor: palette.axis, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: palette.axis, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: false, pinch: true },
    })
    const candlesSeries = chart.addSeries(CandlestickSeries, {
      upColor: palette.gain,
      downColor: palette.loss,
      borderUpColor: palette.gain,
      borderDownColor: palette.loss,
      wickUpColor: alpha(palette.gain, 0.8),
      wickDownColor: alpha(palette.loss, 0.8),
      priceLineVisible: false,
    })
    candlesSeries.setData(bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })))
    createSeriesMarkers(candlesSeries, toMarkers(bars, trades, { buy: palette.gain, sell: palette.loss }))

    for (const ind of indicators) {
      const s = chart.addSeries(
        LineSeries,
        {
          color: colorOf.get(ind.name),
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: '',
        },
        ind.pane === 'lower' ? 1 : 0,
      )
      s.setData(toLine(ind.points))
    }
    if (hasLower) {
      const panes = chart.panes()
      panes[0]?.setStretchFactor(0.7)
      panes[1]?.setStretchFactor(0.3)
    }
    chart.timeScale().fitContent()

    const onMove = (param: MouseEventParams) => {
      const d = param.time !== undefined ? param.seriesData.get(candlesSeries) : undefined
      setHover(d && 'open' in d ? (bars.find((b) => b.time === param.time) ?? null) : null)
    }
    chart.subscribeCrosshairMove(onMove)
    chartRef.current = chart
    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
    }
  }, [palette, bars, indicators, trades, colorOf, hasLower])

  const shown = hover ?? bars.at(-1)
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {shown ? (
          <span className="tabular-nums text-muted-foreground">
            {fmtShortDateTime(shown.at)} · O {fmtNum(shown.open, 2)} H {fmtNum(shown.high, 2)} L {fmtNum(shown.low, 2)}{' '}
            <span className="font-semibold text-foreground">C {fmtNum(shown.close, 2)}</span>
          </span>
        ) : null}
        <span className="ml-auto inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
          {indicators.map((ind) => (
            <span key={ind.name} className="inline-flex items-center gap-1">
              <span aria-hidden className="h-0.5 w-3 rounded-full" style={{ background: colorOf.get(ind.name) }} />
              {ind.name}
              {ind.pane === 'lower' ? <span className="text-[10px]">(lower pane)</span> : null}
            </span>
          ))}
          {trades.length ? (
            <span className="inline-flex items-center gap-2">
              <span>
                <span aria-hidden className="text-gain">▲</span> buy
              </span>
              <span>
                <span aria-hidden className="text-loss">▼</span> sell
              </span>
            </span>
          ) : null}
        </span>
      </div>
      <div ref={container} role="img" aria-label={label} style={{ height: hasLower ? height + 120 : height }} className="w-full" />
    </div>
  )
}
