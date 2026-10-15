import type { AccountInfo, SessionSummary } from '@/api/types'
import type { Market } from '@/lib/preferences'

export interface AccountPoint {
  timestamp: string
  value: number
}

/**
 * The account's equity over time, from the broker totals each session recorded at its start and
 * end (not the per-candle equity, which is only that session's own partial view). Sorted, one
 * point per instant.
 */
export function accountEquity(sessions: SessionSummary[]): AccountPoint[] {
  const byTime = new Map<number, AccountPoint>()
  for (const s of sessions) {
    const pairs: [string | null | undefined, number | null | undefined][] = [
      [s.started_at, s.start_total_assets],
      [s.ended_at, s.end_total_assets],
    ]
    for (const [ts, value] of pairs) {
      if (!ts || value === null || value === undefined || !(value > 0)) continue
      const t = Date.parse(ts)
      if (Number.isNaN(t) || byTime.has(t)) continue
      byTime.set(t, { timestamp: ts, value })
    }
  }
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, p]) => p)
}

export type SessionStatus = 'crashed' | 'open' | 'ended'

export function sessionStatus(s: SessionSummary): SessionStatus {
  if (s.crashed) return 'crashed'
  return s.ended_at ? 'ended' : 'open'
}

/**
 * The account to open first: an available one named for the selected market, else any
 * available one, else (all unavailable) the one named for the market, so its reason shows.
 */
export function defaultAccount(accounts: AccountInfo[], market: Market): AccountInfo | undefined {
  const m = market.toLowerCase()
  const names = (a: AccountInfo) => `${a.id} ${a.label}`.toLowerCase().split(/[^a-z0-9]+/)
  const forMarket = (a: AccountInfo) => names(a).includes(m)
  return (
    accounts.find((a) => a.available && forMarket(a)) ??
    accounts.find((a) => a.available) ??
    accounts.find(forMarket) ??
    accounts[0]
  )
}

/** A VM snapshot older than this suggests the VM is offline or outside its schedule. */
export const SNAPSHOT_STALE_MS = 15 * 60_000

/** How old a published snapshot is, and whether that is old enough to warn about. */
export function snapshotAge(asOf: string, now: number): { ageMs: number; stale: boolean } | null {
  const t = Date.parse(asOf)
  if (Number.isNaN(t)) return null
  const ageMs = Math.max(0, now - t)
  return { ageMs, stale: ageMs > SNAPSHOT_STALE_MS }
}
