import type { ResearchFile } from '@/api/types'

export interface ListedFile {
  file: ResearchFile
  title: string
}

/**
 * The study list in reading order: the newest file of each study (and kind),
 * the corrected headline study first, then current studies, then the rest, with superseded
 * and failed studies last. With `showAll`, every raw file by name, newest first.
 */
export function listStudies(files: ResearchFile[], showAll: boolean): ListedFile[] {
  const newestFirst = [...files].sort((a, b) => b.modified.localeCompare(a.modified))
  if (showAll) return newestFirst.map((f) => ({ file: f, title: f.name }))
  const seen = new Map<string, ResearchFile>()
  // one entry per study and kind: a run often writes a CSV and a JSON under the same study
  for (const f of newestFirst) {
    const key = `${f.study}|${f.kind}`
    if (!seen.has(key)) seen.set(key, f)
  }
  const rank = (title: string) => {
    const t = title.toLowerCase()
    return [
      t.includes('superseded') || t.includes('failed') ? 1 : 0,
      t.includes('headline') ? 0 : 1,
      t.includes('(current)') ? 0 : 1,
    ]
  }
  return [...seen.values()]
    .map((f) => ({ file: f, title: f.label || f.name }))
    .sort((a, b) => {
      const ra = rank(a.title)
      const rb = rank(b.title)
      for (let i = 0; i < ra.length; i++) {
        const d = (ra[i] ?? 0) - (rb[i] ?? 0)
        if (d) return d
      }
      return a.title.localeCompare(b.title)
    })
}

export interface QuickChartSpec {
  x: string
  y: string
}

/**
 * The quick chart drawn when it is obvious: the first return-like numeric
 * column against the first symbol / strategy / assigned column.
 */
export function quickChartSpec(columns: string[], rows: Record<string, unknown>[]): QuickChartSpec | null {
  const y = columns.find(
    (c) =>
      /oos|return/i.test(c) &&
      rows.some((r) => typeof r[c] === 'number') &&
      rows.every((r) => r[c] === null || r[c] === undefined || typeof r[c] === 'number'),
  )
  const x = columns.find((c) => ['symbol', 'strategy', 'assigned'].includes(c.toLowerCase()))
  return x && y ? { x, y } : null
}

/** A table cell: integers with separators, other numbers to 4 places, booleans as words. */
export function cellText(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return String(v)
    return Number.isInteger(v)
      ? v.toLocaleString('en-US')
      : v.toLocaleString('en-US', { maximumFractionDigits: 4, minimumFractionDigits: 0 })
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
