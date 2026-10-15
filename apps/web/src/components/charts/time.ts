import type { UTCTimestamp } from 'lightweight-charts'

/**
 * lightweight-charts draws its time axis in UTC. An instant shifted by the viewer's UTC offset
 * at that moment, so the axis and crosshair read in local wall-clock time.
 */
export function chartTime(at: number): UTCTimestamp {
  return (Math.floor(at / 1000) - new Date(at).getTimezoneOffset() * 60) as UTCTimestamp
}

/** Points with a parseable time and a finite value, sorted, one per chart time (the last wins). */
export function toLine(points: { t: string; v: number }[]): { time: UTCTimestamp; value: number }[] {
  const byTime = new Map<number, { time: UTCTimestamp; value: number }>()
  for (const p of points) {
    const at = Date.parse(p.t)
    if (Number.isNaN(at) || !Number.isFinite(p.v)) continue
    const time = chartTime(at)
    byTime.set(time, { time, value: p.v })
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time)
}
