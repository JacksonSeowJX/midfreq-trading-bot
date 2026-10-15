import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { accounts, accountView } from '@/test/fixtures'
import { server } from '@/test/handlers'
import { renderWithProviders } from '@/test/render'
import { AccountPanel } from './AccountPanel'
import { defaultAccount, snapshotAge } from './logic'

describe('AccountPanel (broker account)', () => {
  it('is labelled as the broker’s view', async () => {
    renderWithProviders(<AccountPanel />)
    const panel = screen.getByRole('region', { name: 'Paper account' })
    expect(panel).toHaveAttribute('data-source', 'broker')
    expect(within(panel).getByText('Broker account')).toBeInTheDocument()
    await screen.findByRole('radiogroup', { name: 'Broker account' })
  })

  it('lists every account and greys out unavailable ones with their reason', async () => {
    renderWithProviders(<AccountPanel />)
    const hk = await screen.findByRole('radio', { name: /Moomoo paper HK \(unavailable/ })
    expect(hk).toHaveAttribute('data-available', 'false')
    expect(hk).toHaveClass('opacity-60')
    expect(hk).toHaveTextContent('OpenD is not running on 127.0.0.1:11111')
    expect(screen.getByRole('radio', { name: 'Moomoo paper US' })).toHaveAttribute('data-available', 'true')
  })

  it('opens the account matching the market and shows its snapshot, positions and orders', async () => {
    renderWithProviders(<AccountPanel />, { market: 'US' })
    expect(await screen.findByText('USD 1,001,234.56')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Moomoo paper US' })).toHaveAttribute('data-state', 'on')
    expect(within(screen.getByRole('group', { name: 'Open positions' })).getByTestId('kpi-value')).toHaveTextContent('2')

    const positions = screen.getByRole('table', { name: 'Open positions' })
    const msft = within(positions).getByRole('cell', { name: 'US.MSFT' }).closest('tr')!
    // no cost basis: a dash, never 0
    expect(within(msft).getAllByRole('cell')[2]).toHaveTextContent('—')
    expect(within(screen.getByRole('table', { name: 'Recent orders' })).getByText('FILLED_ALL')).toBeInTheDocument()
  })

  it('shows why an unavailable account cannot be read, without calling it', async () => {
    let called = false
    server.use(
      http.get('*/api/accounts/moomoo_hk', () => {
        called = true
        return HttpResponse.json({}, { status: 500 })
      }),
    )
    renderWithProviders(<AccountPanel />)
    await userEvent.click(await screen.findByRole('radio', { name: /Moomoo paper HK/ }))
    expect(await screen.findByText('Moomoo paper HK is unavailable')).toBeInTheDocument()
    expect(screen.getAllByText('OpenD is not running on 127.0.0.1:11111').length).toBeGreaterThan(0)
    expect(called).toBe(false)
  })

  it('treats a 503 from the account as unavailable, with the API’s reason', async () => {
    renderWithProviders(<AccountPanel />)
    await userEvent.click(await screen.findByRole('radio', { name: 'Binance spot' }))
    expect(await screen.findByText('Binance spot is unavailable')).toBeInTheDocument()
    expect(screen.getByText('Binance API timed out after 5 s')).toBeInTheDocument()
  })

  it('says how to add an account when none is configured', async () => {
    server.use(http.get('*/api/accounts', () => HttpResponse.json([])))
    renderWithProviders(<AccountPanel />)
    expect(await screen.findByText('No broker accounts configured')).toBeInTheDocument()
  })
})

describe('defaultAccount', () => {
  it('prefers an available account named for the market, then any available one', () => {
    expect(defaultAccount(accounts, 'US')?.id).toBe('moomoo_us')
    expect(defaultAccount(accounts, 'HK')?.id).toBe('moomoo_us') // the HK one is unavailable
    const down = accounts.map((a) => ({ ...a, available: false }))
    expect(defaultAccount(down, 'HK')?.id).toBe('moomoo_hk') // its reason is what to show
    expect(defaultAccount(down, 'US')?.id).toBe('moomoo_us')
    expect(defaultAccount([], 'US')).toBeUndefined()
  })
})

describe('accounts published by the VM (snapshots)', () => {
  const snapshot = accounts.map((a) => (a.id === 'moomoo_us' ? { ...a, kind: 'snapshot' as const } : a))

  function serve(asOf: string) {
    server.use(
      http.get('*/api/accounts', () => HttpResponse.json(snapshot)),
      http.get('*/api/accounts/moomoo_us', () => HttpResponse.json({ ...accountView, as_of: asOf })),
    )
  }

  afterEach(() => {
    vi.useRealTimers()
  })

  it('says it is a snapshot and how old it is', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-16T14:08:00Z'))
    serve('2026-10-16T14:05:00Z')
    renderWithProviders(<AccountPanel />)
    expect(await screen.findByText('Snapshot from the VM')).toBeInTheDocument()
    expect(screen.getByTestId('snapshot-age')).toHaveTextContent('as of 14:05, 3 min ago')
    expect(screen.queryByText(/the VM may be offline/)).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Moomoo paper US' })).toHaveTextContent('moomoo · VM snapshot')
  })

  it('warns when the snapshot is older than 15 minutes', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-16T14:08:00Z'))
    serve('2026-10-16T13:30:00Z')
    renderWithProviders(<AccountPanel />)
    expect(await screen.findByTestId('snapshot-age')).toHaveTextContent('as of 13:30, 38 min ago')
    expect(screen.getByText(/more than 15 minutes old: the VM may be offline or outside its schedule/)).toBeInTheDocument()
  })

  it('shows no snapshot badge for an account read live', async () => {
    renderWithProviders(<AccountPanel />)
    expect(await screen.findByText('USD 1,001,234.56')).toBeInTheDocument()
    expect(screen.queryByText('Snapshot from the VM')).not.toBeInTheDocument()
  })

  it('snapshotAge: stale only past 15 minutes', () => {
    const at = Date.parse('2026-10-16T14:00:00Z')
    expect(snapshotAge('2026-10-16T14:00:00Z', at + 15 * 60_000)).toEqual({ ageMs: 15 * 60_000, stale: false })
    expect(snapshotAge('2026-10-16T14:00:00Z', at + 15 * 60_000 + 1)?.stale).toBe(true)
    expect(snapshotAge('not a time', at)).toBeNull()
  })
})
