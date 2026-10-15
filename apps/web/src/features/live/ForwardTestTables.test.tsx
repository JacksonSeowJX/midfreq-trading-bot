import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { forwardTest } from '@/test/fixtures'
import { HoldingsTable, TradesTable } from './ForwardTestTables'

function bodyRows(name: string) {
  return within(screen.getByRole('table', { name })).getAllByRole('row').slice(1)
}

function cellsOf(row: HTMLElement) {
  return within(row).getAllByRole('cell')
}

describe('TradesTable', () => {
  it('lists trades newest first with the Streamlit view’s columns', () => {
    render(<TradesTable trades={forwardTest.trades} currency="USD" />)
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual([
      'Date (New York)',
      'Decided on candle',
      'Side',
      'Stock',
      'Shares',
      'Price',
      'Value (USD)',
      'Realised P&L (USD)',
      'Realised %',
    ])
    const rows = bodyRows('Forward-test trades')
    expect(rows.map((r) => cellsOf(r)[0]?.textContent)).toEqual(['2026-10-01', '2026-09-22', '2026-09-17'])
  })

  it('formats shares, prices and values, and signs and colours realised P&L', () => {
    render(<TradesTable trades={forwardTest.trades} currency="USD" />)
    const [loss, gain, buy] = bodyRows('Forward-test trades')

    const g = cellsOf(gain!)
    expect(g[4]).toHaveTextContent('1,200')
    expect(g[5]).toHaveTextContent('188.53')
    expect(g[6]).toHaveTextContent('226,236')
    expect(g[7]).toHaveTextContent('+1,236')
    expect(g[7]!.firstElementChild).toHaveClass('text-gain')
    expect(g[8]).toHaveTextContent('+0.5%')

    const l = cellsOf(loss!)
    expect(l[7]).toHaveTextContent('-57')
    expect(l[7]!.firstElementChild).toHaveClass('text-loss')
    expect(l[8]).toHaveTextContent('-0.5%')

    // a purchase realises nothing: blank, not zero
    const b = cellsOf(buy!)
    expect(b[2]).toHaveTextContent('BUY')
    expect(b[7]).toHaveTextContent(/^$/)
    expect(b[8]).toHaveTextContent(/^$/)
  })

  it('numbers are right-aligned', () => {
    render(<TradesTable trades={forwardTest.trades} currency="USD" />)
    const cells = cellsOf(bodyRows('Forward-test trades')[0]!)
    for (const i of [4, 5, 6, 7, 8]) expect(cells[i]).toHaveClass('num')
    expect(cells[3]).not.toHaveClass('num')
  })

  it('says so when there are no trades', () => {
    render(<TradesTable trades={[]} currency="USD" />)
    expect(screen.getByText('No trades recorded yet')).toBeInTheDocument()
  })
})

describe('HoldingsTable', () => {
  it('shows each holding with its unrealised P&L', () => {
    render(<HoldingsTable holdings={forwardTest.holdings} currency="USD" />)
    const cells = cellsOf(bodyRows('Forward-test holdings')[0]!)
    expect(cells.map((c) => c.textContent)).toEqual(['NVDA', '80', '120.50', '125.75', '10,060', '+420', '+4.4%'])
    expect(cells[5]!.firstElementChild).toHaveClass('text-gain')
  })
})
