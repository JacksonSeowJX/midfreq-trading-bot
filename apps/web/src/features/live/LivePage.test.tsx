import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { sessions } from '@/test/fixtures'
import { renderApp } from '@/test/render'
import { accountEquity, sessionStatus } from './logic'

describe('Live view', () => {
  it('shows the broker account and the bot record as separate, labelled sources', async () => {
    renderApp('/live')
    const broker = await screen.findByRole('region', { name: 'Paper account' })
    const bot = screen.getByRole('region', { name: 'S&P 100 forward test' })
    expect(broker).toHaveAttribute('data-source', 'broker')
    expect(bot).toHaveAttribute('data-source', 'bot')
    expect(within(bot).getByText('Bot record')).toBeInTheDocument()
    expect(screen.getByText(/Auto-refresh every 60 s/)).toBeInTheDocument()
  })

  it('shows the forward test: KPIs, the equity chart and the trades', async () => {
    renderApp('/live')
    const bot = await screen.findByRole('region', { name: 'S&P 100 forward test' })
    expect(await within(bot).findByRole('img', { name: /Forward-test value over time/ })).toBeInTheDocument()
    expect(within(bot).getByRole('tab', { name: 'Trades (3)' })).toHaveAttribute('data-state', 'active')
    expect(within(bot).getByRole('table', { name: 'Forward-test trades' })).toBeInTheDocument()
    await userEvent.click(within(bot).getByRole('tab', { name: /Holdings/ }))
    expect(within(bot).getByRole('table', { name: 'Forward-test holdings' })).toBeInTheDocument()
  })

  it('switching to HK, where no forward test exists, says so instead of failing', async () => {
    renderApp('/live')
    await screen.findByRole('region', { name: 'S&P 100 forward test' })
    await userEvent.click(screen.getByRole('radio', { name: 'Hong Kong (HKD)' }))
    expect(await screen.findByText('No HK forward test recorded')).toBeInTheDocument()
    expect(await screen.findByText('No HK live sessions recorded yet')).toBeInTheDocument()
  })

  it('lists sessions with end assets and status, and opens one in a drawer', async () => {
    renderApp('/live')
    const table = await screen.findByRole('table', { name: 'Session history' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('1,001,234')
    expect(rows[1]).toHaveTextContent('Crashed')

    await userEvent.click(within(rows[0]!).getByText('6 h 30 min'))
    const drawer = await screen.findByRole('dialog')
    expect(within(drawer).getByText('CrossSectionalReversal · US')).toBeInTheDocument()
    expect(await within(drawer).findByRole('table', { name: 'Session trades' })).toHaveTextContent('US.NVDA')
    expect(within(drawer).getByText('candle_close')).toBeInTheDocument()
    expect(within(within(drawer).getByRole('group', { name: 'Change' })).getByTestId('kpi-value')).toHaveTextContent(
      'USD +284',
    )
  })

  it('draws account equity from the session totals', async () => {
    renderApp('/live')
    expect(await screen.findByRole('img', { name: /Account equity over time/ })).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Account equity' })).getByTestId('kpi-value')).toHaveTextContent(
      'USD 1,001,234',
    )
  })
})

describe('session helpers', () => {
  it('builds the account curve from start and end totals, in time order', () => {
    expect(accountEquity(sessions).map((p) => p.value)).toEqual([1000000, 1000950, 1001234.4])
  })

  it('reads a session’s status', () => {
    expect(sessions.map(sessionStatus)).toEqual(['ended', 'crashed'])
    expect(sessionStatus({ ...sessions[0]!, ended_at: null })).toBe('open')
  })
})
