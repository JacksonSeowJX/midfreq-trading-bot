import { describe, expect, it } from 'vitest'
import {
  fmtAgo,
  fmtCalendarDate,
  fmtClock,
  fmtDateTime,
  fmtShortDateTime,
  fmtDuration,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtShare,
  tone,
} from './format'

describe('number formatting', () => {
  it('groups thousands and fixes decimals', () => {
    expect(fmtNum(1234567.891, 0)).toBe('1,234,568')
    expect(fmtNum(187.5, 2)).toBe('187.50')
  })

  it('signs P&L, but never shows a signed zero', () => {
    expect(fmtNum(1234.4, 0, true)).toBe('+1,234')
    expect(fmtNum(-56.7, 0, true)).toBe('-57')
    expect(fmtNum(0, 0, true)).toBe('0')
    expect(fmtNum(-0.4, 0, true)).toBe('0')
  })

  it('shows a dash for missing values', () => {
    expect(fmtNum(null)).toBe('—')
    expect(fmtNum(undefined)).toBe('—')
    expect(fmtNum(Number.NaN)).toBe('—')
    expect(fmtMoney(null, 'USD')).toBe('—')
  })

  it('prefixes the currency', () => {
    expect(fmtMoney(101234.4, 'USD')).toBe('USD 101,234')
    expect(fmtMoney(-120, 'HKD', 0, true)).toBe('HKD -120')
  })

  it('treats *_pct as percent already and p-values as fractions', () => {
    expect(fmtPct(1.234)).toBe('+1.23%')
    expect(fmtPct(-0.46, 1)).toBe('-0.5%')
    expect(fmtPct(33.3333, 1, false)).toBe('33.3%')
    expect(fmtShare(0.09415)).toBe('9.4%')
  })

  it('judges direction at the precision shown', () => {
    expect(tone(12)).toBe('gain')
    expect(tone(-12)).toBe('loss')
    expect(tone(0.3)).toBe('flat')
    expect(tone(0.003, 2)).toBe('flat')
    expect(tone(null)).toBe('flat')
  })
})

describe('dates', () => {
  it('reads plain calendar dates without shifting the day', () => {
    expect(fmtCalendarDate('20260915')).toBe('15 Sep 2026')
    expect(fmtCalendarDate('2026-10-01')).toBe('01 Oct 2026')
    expect(fmtCalendarDate(null)).toBe('—')
  })

  it('formats instants day first, with the zone', () => {
    expect(fmtDateTime('2026-10-16T07:30:05Z')).toBe('16 Oct 2026, 07:30 UTC')
    expect(fmtShortDateTime('2026-09-16T07:30:05Z')).toBe('16 Sep, 07:30')
    expect(fmtClock('2026-10-16T07:30:05Z')).toBe('07:30:05')
  })

  it('formats spans and ages', () => {
    expect(fmtDuration('2026-10-15T13:30:00Z', '2026-10-15T20:00:00Z')).toBe('6 h 30 min')
    expect(fmtDuration('2026-10-15T13:30:00Z', null)).toBe('—')
    expect(fmtAgo(1_000, 13_000)).toBe('12 s ago')
    expect(fmtAgo(0, 5 * 60_000)).toBe('5 min ago')
  })
})
