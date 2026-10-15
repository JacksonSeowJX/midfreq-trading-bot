import { useTheme, type ResolvedTheme } from '@/lib/preferences'

/** Chart colours, mirroring the tokens in index.css (canvas charts cannot read CSS classes). */
export interface ChartPalette {
  surface: string
  text: string
  muted: string
  grid: string
  axis: string
  neutral: string
  gain: string
  loss: string
  broker: string
  bot: string
}

export const CHART_PALETTE: Record<ResolvedTheme, ChartPalette> = {
  light: {
    surface: '#ffffff',
    text: '#1f2328',
    muted: '#667085',
    grid: '#ece8e0',
    axis: '#d9d4ca',
    neutral: '#b8b3aa',
    gain: '#2e7d32',
    loss: '#c62828',
    broker: '#2a78d6',
    bot: '#eb6834',
  },
  dark: {
    surface: '#1b1a17',
    text: '#ece8e1',
    muted: '#9c968c',
    grid: '#2a2823',
    axis: '#3a3731',
    neutral: '#5e5a53',
    gain: '#5cb860',
    loss: '#ef5f5c',
    broker: '#5d9de8',
    bot: '#f07a4a',
  },
}

export function useChartPalette(): ChartPalette {
  return CHART_PALETTE[useTheme().resolved]
}

/** "#rrggbb" + alpha -> "rgba(...)". */
export function alpha(hex: string, a: number): string {
  const n = Number.parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}

/** Axis, grid and tooltip styling shared by every research chart. */
export function baseOption(p: ChartPalette) {
  const axis = {
    axisLine: { lineStyle: { color: p.axis } },
    axisTick: { show: false },
    axisLabel: { color: p.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: p.grid, type: 'solid' as const, width: 1 } },
    nameTextStyle: { color: p.muted, fontSize: 11 },
  }
  return {
    textStyle: { fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif', color: p.text },
    animationDuration: 300,
    tooltip: {
      backgroundColor: p.surface,
      borderColor: p.axis,
      textStyle: { color: p.text, fontSize: 12 },
      extraCssText: 'box-shadow: 0 4px 12px rgb(0 0 0 / 0.12); border-radius: 6px;',
    },
    legend: { textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 10, itemHeight: 10, icon: 'roundRect' },
    axis,
  }
}

/**
 * Series colours in a fixed order (never cycled): the dataviz reference palette, stepped for
 * each theme and checked for colour-blind separation on this dashboard's surfaces. Eight at
 * most; a series keeps its slot whatever else is shown.
 */
export const SERIES_COLORS: Record<ResolvedTheme, string[]> = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
}

export function useSeriesColors(): string[] {
  return SERIES_COLORS[useTheme().resolved]
}

/** One-hue sequential ramp (light to dark blue) and the diverging pair, for heatmaps. */
export const HEAT = {
  light: { seq: ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'], neg: '#c62828', mid: '#f0efec', pos: '#1c5cab' },
  dark: { seq: ['#0d366b', '#1c5cab', '#3987e5', '#86b6ef', '#cde2fb'], neg: '#ef5f5c', mid: '#383835', pos: '#6da7ec' },
} as const
