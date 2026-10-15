import type { ResultRow, SearchCorrection } from '@/api/types'

export interface ResultFilter {
  families: Set<string> // empty = all
  query: string
  passedOnly: boolean
}

export function filterRows(rows: ResultRow[], f: ResultFilter): ResultRow[] {
  const q = f.query.trim().toLowerCase()
  return rows.filter(
    (r) =>
      (f.families.size === 0 || f.families.has(r.family)) &&
      (!f.passedOnly || r.robust) &&
      (!q || `${r.family} ${r.strategy} ${r.subject} ${r.study} ${r.file}`.toLowerCase().includes(q)),
  )
}

export interface FamilyStat {
  family: string
  tested: number
  passed: number
  passRate: number
}

export function familyStats(rows: ResultRow[]): FamilyStat[] {
  const by = new Map<string, FamilyStat>()
  for (const r of rows) {
    const s = by.get(r.family) ?? { family: r.family, tested: 0, passed: 0, passRate: 0 }
    s.tested += 1
    s.passed += r.robust ? 1 : 0
    by.set(r.family, s)
  }
  return [...by.values()]
    .map((s) => ({ ...s, passRate: s.tested ? (s.passed / s.tested) * 100 : 0 }))
    .sort((a, b) => b.passRate - a.passRate || b.tested - a.tested || a.family.localeCompare(b.family))
}

const CSV_COLUMNS: (keyof ResultRow)[] = [
  'family',
  'strategy',
  'subject',
  'config_a_oos',
  'config_a_consistency',
  'config_b_oos',
  'config_b_consistency',
  'worst_config',
  'robust',
  'study',
  'file',
]

export function toCsv(rows: ResultRow[]): string {
  const esc = (v: unknown) => {
    const s = String(v ?? '')
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [CSV_COLUMNS.join(','), ...rows.map((r) => CSV_COLUMNS.map((c) => esc(r[c])).join(','))].join('\n')
}

/** The PBO figure, if the search-correction block carries one (its `pbo` is a free-form dict). */
export function pboOf(sc: SearchCorrection): { pbo: number; nConfigs?: number; nSubsets?: number } | null {
  const raw = sc.pbo
  if (!raw || typeof raw.pbo !== 'number') return null
  return {
    pbo: raw.pbo,
    nConfigs: typeof raw.n_configs === 'number' ? raw.n_configs : undefined,
    nSubsets: typeof raw.n_subsets === 'number' ? raw.n_subsets : undefined,
  }
}
