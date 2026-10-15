import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { renderApp } from '@/test/render'
import { WELCOME_DISMISSED_KEY } from './logic'

const welcome = () => screen.queryByRole('region', { name: 'Welcome to the midfreq trading dashboard' })

describe('the welcome card on Live', () => {
  it('introduces the dashboard on a first visit, with the guide and a backtest to try', async () => {
    renderApp('/live')
    const card = await screen.findByRole('region', { name: 'Welcome to the midfreq trading dashboard' })
    expect(within(card).getByRole('link', { name: 'Open the guide' })).toHaveAttribute('href', '/guide')
    expect(within(card).getByRole('link', { name: 'Try a backtest' })).toHaveAttribute('href', '/backtest?preset=forward-test')
    expect(card.querySelector('p')?.textContent?.split(/(?<=\.)\s/)).toHaveLength(2)
  })

  it('stays dismissed once closed, across visits', async () => {
    const first = renderApp('/live')
    await userEvent.click(await screen.findByRole('button', { name: 'Dismiss the welcome' }))
    expect(welcome()).not.toBeInTheDocument()
    expect(window.localStorage.getItem(WELCOME_DISMISSED_KEY)).toBe('1')
    first.unmount()

    renderApp('/live')
    expect(await screen.findByRole('heading', { level: 1, name: /Live trading/ })).toBeInTheDocument()
    expect(welcome()).not.toBeInTheDocument()
  })

  it('counts as seen once either button is used', async () => {
    const { router, unmount } = renderApp('/live')
    await userEvent.click(await screen.findByRole('link', { name: 'Try a backtest' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/backtest'))
    expect(await screen.findByLabelText('Lookback (candles)')).toHaveValue(30)
    unmount()

    renderApp('/live')
    await screen.findByRole('heading', { level: 1, name: /Live trading/ })
    expect(welcome()).not.toBeInTheDocument()
  })

  it('still works when storage is blocked: shown, and closed for this visit', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    renderApp('/live')
    await userEvent.click(await screen.findByRole('button', { name: 'Dismiss the welcome' }))
    expect(welcome()).not.toBeInTheDocument()
  })
})
