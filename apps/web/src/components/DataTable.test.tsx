import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DataTable, type Column } from './DataTable'

interface Row {
  name: string
  pnl: number | null
}

const rows: Row[] = [
  { name: 'b', pnl: 5 },
  { name: 'a', pnl: null },
  { name: 'c', pnl: -3 },
]

const columns: Column<Row>[] = [
  { id: 'name', header: 'Name', cell: (r) => r.name, sortValue: (r) => r.name },
  { id: 'pnl', header: 'P&L', align: 'right', cell: (r) => String(r.pnl ?? ''), sortValue: (r) => r.pnl },
]

const names = () =>
  screen
    .getAllByRole('row')
    .slice(1)
    .map((tr) => within(tr).getAllByRole('cell')[0]?.textContent)

describe('DataTable', () => {
  it('keeps the given order until a header is clicked, then sorts and reverses', async () => {
    render(<DataTable label="t" columns={columns} rows={rows} rowKey={(r) => r.name} />)
    expect(names()).toEqual(['b', 'a', 'c'])

    await userEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(names()).toEqual(['c', 'b', 'a']) // ascending, missing value last
    expect(screen.getByRole('columnheader', { name: /P&L/ })).toHaveAttribute('aria-sort', 'ascending')

    await userEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(names()).toEqual(['b', 'c', 'a']) // descending, missing value still last
  })

  it('right-aligns numeric columns in tabular figures', () => {
    render(<DataTable label="t" columns={columns} rows={rows} rowKey={(r) => r.name} />)
    const firstRow = screen.getAllByRole('row')[1]!
    expect(within(firstRow).getAllByRole('cell')[1]).toHaveClass('num')
  })

  it('opens a row with click or Enter', async () => {
    const onRowClick = vi.fn()
    render(<DataTable label="t" columns={columns} rows={rows} rowKey={(r) => r.name} onRowClick={onRowClick} />)
    await userEvent.click(screen.getByRole('cell', { name: 'c' }))
    expect(onRowClick).toHaveBeenLastCalledWith(rows[2])
    screen.getAllByRole('row')[1]!.focus()
    await userEvent.keyboard('{Enter}')
    expect(onRowClick).toHaveBeenLastCalledWith(rows[0])
  })

  it('shows the empty state instead of an empty table', () => {
    render(<DataTable label="t" columns={columns} rows={[]} rowKey={(r) => r.name} empty={<p>Nothing yet</p>} />)
    expect(screen.getByText('Nothing yet')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
