import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ForwardTestKpis } from '@/features/live/ForwardTestPanel'
import { forwardTest } from '@/test/fixtures'
import { renderWithProviders } from '@/test/render'
import { KpiCard } from './KpiCard'

function card(label: string) {
  return screen.getByRole('group', { name: label })
}

describe('KpiCard', () => {
  it('shows its label, value and second line', () => {
    renderWithProviders(<KpiCard label="Cash" value="USD 901,235" sub="of the account" />)
    const c = card('Cash')
    expect(within(c).getByTestId('kpi-value')).toHaveTextContent('USD 901,235')
    expect(c).toHaveTextContent('of the account')
  })

  it('colours the value by direction', () => {
    renderWithProviders(
      <>
        <KpiCard label="Up" value="+5" tone="gain" />
        <KpiCard label="Down" value="-5" tone="loss" />
        <KpiCard label="Flat" value="0" />
      </>,
    )
    expect(within(card('Up')).getByTestId('kpi-value')).toHaveClass('text-gain')
    expect(within(card('Down')).getByTestId('kpi-value')).toHaveClass('text-loss')
    expect(within(card('Flat')).getByTestId('kpi-value')).not.toHaveClass('text-gain', 'text-loss')
  })

  it('explains itself on hover or focus', async () => {
    renderWithProviders(<KpiCard label="PBO" value="0.274" hint="Probability of Backtest Overfitting" />)
    await userEvent.hover(screen.getByRole('button', { name: 'About PBO' }))
    expect((await screen.findAllByText('Probability of Backtest Overfitting')).length).toBeGreaterThan(0)
  })
})

describe('forward-test KPI cards', () => {
  it('shows the seven headline numbers with signs and colours', () => {
    renderWithProviders(<ForwardTestKpis data={forwardTest} currency="USD" />)
    const value = (label: string) => within(card(label)).getByTestId('kpi-value')

    expect(value('Value now')).toHaveTextContent('USD 101,234')
    expect(card('Value now')).toHaveTextContent('of USD 100,000 allocated')
    expect(value('Profit / loss')).toHaveTextContent('USD +1,234')
    expect(value('Profit / loss')).toHaveClass('text-gain')
    expect(value('P&L %')).toHaveTextContent('+1.23%')
    expect(value('Sessions')).toHaveTextContent('12')
    expect(value('Rebalances')).toHaveTextContent('4')
    expect(value('Orders filled')).toHaveTextContent('18')
    expect(value('Banked from sales')).toHaveTextContent('USD -57')
    expect(value('Banked from sales')).toHaveClass('text-loss')
  })
})
