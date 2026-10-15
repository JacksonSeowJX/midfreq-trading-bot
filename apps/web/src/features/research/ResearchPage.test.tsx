import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { researchFiles } from '@/test/fixtures'
import { renderApp } from '@/test/render'
import { cellText, listStudies, quickChartSpec } from './logic'

describe('Research', () => {
  it('lists studies headline first, superseded last, and opens the first as a table', async () => {
    renderApp('/research')
    const list = await screen.findByRole('list', { name: 'Research files' })
    const titles = within(list)
      .getAllByRole('button')
      .map((b) => b.textContent ?? '')
    expect(titles[0]).toMatch(/headline/)
    expect(titles.at(-1)).toMatch(/superseded/)

    const table = await screen.findByRole('table', { name: 'cross_sectional_corrected_20260909_050515.csv' })
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(screen.getByRole('img', { name: 'config_a_oos by symbol' })).toBeInTheDocument()
  })

  it('shows a JSON file as a collapsible tree', async () => {
    renderApp('/research')
    await userEvent.click(await screen.findByRole('button', { name: /Pbo Sp100 Reversal Full96/ }))
    expect(await screen.findByText('pbo:')).toBeInTheDocument()
    const params = screen.getByRole('button', { name: /params/ })
    expect(params).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(params)
    expect(params).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('top_n:')).toBeInTheDocument()
  })
})

describe('research helpers', () => {
  it('keeps the newest file per study unless all are asked for', () => {
    const extra = { ...researchFiles[0]!, name: 'cross_sectional_corrected_20250101.csv', modified: '2025-01-01T00:00:00Z' }
    expect(listStudies([...researchFiles, extra], false)).toHaveLength(3)
    expect(listStudies([...researchFiles, extra], true)).toHaveLength(4)
    const json = { ...researchFiles[0]!, name: 'cross_sectional_corrected_detail.json', kind: 'json' as const }
    expect(listStudies([...researchFiles, json], false)).toHaveLength(4) // CSV and JSON of one run both listed
  })

  it('finds the obvious quick chart', () => {
    expect(quickChartSpec(['symbol', 'config_a_oos'], [{ symbol: 'x', config_a_oos: 1 }])).toEqual({
      x: 'symbol',
      y: 'config_a_oos',
    })
    expect(quickChartSpec(['name', 'value'], [{ name: 'x', value: 1 }])).toBeNull()
  })

  it('formats cells', () => {
    expect(cellText(1234567)).toBe('1,234,567')
    expect(cellText(0.123456)).toBe('0.1235')
    expect(cellText(null)).toBe('—')
    expect(cellText(true)).toBe('true')
  })
})
