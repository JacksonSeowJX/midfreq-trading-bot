import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { overview } from '@/test/fixtures'
import { server } from '@/test/handlers'
import { renderApp } from '@/test/render'
import { familyStats, filterRows, pboOf, toCsv } from './logic'

const kpi = (label: string) => within(screen.getByRole('group', { name: label })).getByTestId('kpi-value')

describe('Overview', () => {
  it('shows the summary banner and highlights the results that passed', async () => {
    renderApp('/overview')
    expect(await screen.findByRole('group', { name: 'Results tested' })).toHaveTextContent('3')
    expect(screen.getByRole('group', { name: 'Passed the standard' })).toHaveTextContent('33.3% of results')
    const table = screen.getByRole('table', { name: 'Results against the validation standard' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Pass')
    expect(rows[0]!.className).toContain('row-highlight')
    expect(rows[1]!.className).not.toContain('row-highlight')
  })

  it('filters by passed-only, family and search', async () => {
    renderApp('/overview')
    const table = await screen.findByRole('table', { name: 'Results against the validation standard' })
    const count = () => within(table).getAllByRole('row').length - 1

    await userEvent.click(screen.getByRole('switch', { name: 'Show only results that passed' }))
    expect(count()).toBe(1)
    await userEvent.click(screen.getByRole('switch', { name: 'Show only results that passed' }))

    await userEvent.click(screen.getByRole('button', { name: 'Single-Stock Strategies' }))
    expect(count()).toBe(1)
    await userEvent.click(screen.getByRole('button', { name: 'All families' }))

    await userEvent.type(screen.getByRole('textbox', { name: 'Search results' }), 'hang seng')
    expect(count()).toBe(1)
  })

  it('reports the Reality Check and PBO, corrected for the search', async () => {
    renderApp('/overview')
    expect(await screen.findByRole('group', { name: 'By luck · 9 windows' })).toHaveTextContent('9.4%')
    expect(kpi('By luck · 15 windows')).toHaveTextContent('11.0%')
    expect(kpi('Verdict')).toHaveTextContent('Does not pass')
    expect(kpi('Verdict')).toHaveClass('text-loss')
    expect(kpi('PBO · S&P 100')).toHaveTextContent('0.274')
    expect(screen.getByText(/Tested alone, as if nothing else had been tried, that figure would be 5.8%/)).toBeInTheDocument()
  })

  it('asks for the study when no Reality Check exists', async () => {
    server.use(http.get('*/api/results/overview', () => HttpResponse.json({ ...overview, search_correction: null })))
    renderApp('/overview')
    expect(await screen.findByText('No Reality Check result found')).toBeInTheDocument()
  })
})

describe('overview helpers', () => {
  it('filters and summarises by family', () => {
    const all = filterRows(overview.rows, { families: new Set(), query: '', passedOnly: false })
    expect(all).toHaveLength(3)
    expect(familyStats(all)).toEqual([
      { family: 'Cross-Sectional Reversal', tested: 2, passed: 1, passRate: 50 },
      { family: 'Single-Stock Strategies', tested: 1, passed: 0, passRate: 0 },
    ])
  })

  it('exports CSV with a header row', () => {
    const csv = toCsv(overview.rows.slice(0, 1)).split('\n')
    expect(csv[0]).toBe(
      'family,strategy,subject,config_a_oos,config_a_consistency,config_b_oos,config_b_consistency,worst_config,robust,study,file',
    )
    expect(csv[1]).toContain('CS-Reversal,US S&P 100,6.15')
  })

  it('reads PBO only when it is a number', () => {
    expect(pboOf(overview.search_correction!)?.pbo).toBeCloseTo(0.2738)
    expect(pboOf({ ...overview.search_correction!, pbo: { note: 'n/a' } })).toBeNull()
  })
})
