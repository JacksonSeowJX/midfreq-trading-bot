/**
 * Number and time formatting, one place, so every table and card reads the same way.
 *
 * Percent inputs: `fmtPct` takes values already in percent (the API's *_pct, *_oos and
 * consistency fields, e.g. 1.23 for 1.23%); `fmtShare` takes a fraction (p-values, PBO).
 */

export const DASH = '—'

type Num = number | null | undefined

const formatters = new Map<string, Intl.NumberFormat>()

function numberFormat(decimals: number, sign: boolean): Intl.NumberFormat {
  const key = `${decimals}|${sign}`
  let f = formatters.get(key)
  if (!f) {
    f = new Intl.NumberFormat('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
      signDisplay: sign ? 'exceptZero' : 'auto',
    })
    formatters.set(key, f)
  }
  return f
}

function isNum(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** 1234.5 -> "1,235"; with sign -> "+1,235". Missing values -> "—". */
export function fmtNum(v: Num, decimals = 0, sign = false): string {
  if (!isNum(v)) return DASH
  // a value that rounds to zero is shown unsigned ("0", not "-0")
  const out = numberFormat(decimals, sign).format(v)
  return out === '-0' || /^-0\.0+$/.test(out) ? out.slice(1) : out
}

/** "USD 101,234" / "USD +1,234". */
export function fmtMoney(v: Num, currency: string, decimals = 0, sign = false): string {
  if (!isNum(v)) return DASH
  return `${currency} ${fmtNum(v, decimals, sign)}`
}

/** Value already in percent: 1.234 -> "+1.23%". */
export function fmtPct(v: Num, decimals = 2, sign = true): string {
  if (!isNum(v)) return DASH
  return `${fmtNum(v, decimals, sign)}%`
}

/** A fraction shown as a percent: 0.0942 -> "9.4%". */
export function fmtShare(v: Num, decimals = 1): string {
  if (!isNum(v)) return DASH
  return `${fmtNum(v * 100, decimals)}%`
}

export type Tone = 'gain' | 'loss' | 'flat'

/** Direction of a P&L figure, judged at the precision it is shown with. */
export function tone(v: Num, decimals = 0): Tone {
  if (!isNum(v)) return 'flat'
  const eps = 0.5 * 10 ** -decimals
  if (v >= eps) return 'gain'
  if (v <= -eps) return 'loss'
  return 'flat'
}

export const toneClass: Record<Tone, string> = {
  gain: 'text-gain',
  loss: 'text-loss',
  flat: '',
}

export function currencyFor(market: string): string {
  return market.toUpperCase() === 'HK' ? 'HKD' : 'USD'
}

// ── time ────────────────────────────────────────────────────────────────────

// Assembled from en-US parts ("Sep", not en-GB's "Sept"), day first: "16 Oct 2026, 15:30".
function partsFormatter(opts: Intl.DateTimeFormatOptions) {
  const f = new Intl.DateTimeFormat('en-US', { hourCycle: 'h23', ...opts })
  return (d: Date) => {
    const p: Partial<Record<Intl.DateTimeFormatPartTypes, string>> = {}
    for (const part of f.formatToParts(d)) p[part.type] = part.value
    const date = [p.day, p.month, p.year].filter(Boolean).join(' ')
    const time = p.hour !== undefined ? [p.hour, p.minute, p.second].filter(Boolean).join(':') : ''
    const zone = p.timeZoneName ? ` ${p.timeZoneName}` : ''
    return [date, time].filter(Boolean).join(', ') + zone
  }
}

const dateTimeFmt = partsFormatter({
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZoneName: 'short',
})
const shortDateTimeFmt = partsFormatter({ day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
const clockFmt = partsFormatter({ hour: '2-digit', minute: '2-digit', second: '2-digit' })
const calendarFmt = partsFormatter({ day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' })

function toDate(v: string | number | Date | null | undefined): Date | null {
  if (v === null || v === undefined || v === '') return null
  const d = v instanceof Date ? v : new Date(v)
  return Number.isNaN(d.getTime()) ? null : d
}

/** "16 Oct 2026, 15:30 GMT+8", in the viewer's time zone. */
export function fmtDateTime(v: string | number | Date | null | undefined): string {
  const d = toDate(v)
  return d ? dateTimeFmt(d) : DASH
}

/** "16 Oct, 15:30", for dense tables. */
export function fmtShortDateTime(v: string | number | Date | null | undefined): string {
  const d = toDate(v)
  return d ? shortDateTimeFmt(d) : DASH
}

/** "15:30:12". */
export function fmtClock(v: string | number | Date | null | undefined): string {
  const d = toDate(v)
  return d ? clockFmt(d) : DASH
}

/**
 * A calendar date with no time zone ("2026-10-15" or "20261015") -> "15 Oct 2026".
 * Read as a plain date, so it never shifts a day in the viewer's zone.
 */
export function fmtCalendarDate(v: string | null | undefined): string {
  if (!v) return DASH
  const m = /^(\d{4})-?(\d{2})-?(\d{2})$/.exec(v.trim())
  if (!m) return v
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
  return Number.isNaN(d.getTime()) ? v : calendarFmt(d)
}

/** "12 s ago", "4 min ago", "3 h ago". */
export function fmtAgo(then: number, now: number = Date.now()): string {
  const s = Math.max(0, Math.round((now - then) / 1000))
  if (s < 60) return `${s} s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.floor(h / 24)} d ago`
}

/** "1 h 05 min" between two instants. */
export function fmtDuration(from: string | null | undefined, to: string | null | undefined): string {
  const a = toDate(from)
  const b = toDate(to)
  if (!a || !b) return DASH
  const mins = Math.max(0, Math.round((b.getTime() - a.getTime()) / 60000))
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
