import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { apiDown, server } from '@/test/handlers'
import { renderApp } from '@/test/render'

describe('routing', () => {
  it('opens on the Live view', async () => {
    const { router } = renderApp('/')
    await waitFor(() => expect(router.state.location.pathname).toBe('/live'))
    expect(await screen.findByRole('heading', { level: 1, name: /Live trading/ })).toBeInTheDocument()
  })

  it('navigates from the left nav, loading the research pages on demand', async () => {
    const { router } = renderApp('/live')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    await userEvent.click(within(nav).getByRole('link', { name: 'Overview' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'All results overview' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/overview')
    expect(within(nav).getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')

    await userEvent.click(within(nav).getByRole('link', { name: 'Research files' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Research results' })).toBeInTheDocument()
  })

  it.each([
    ['/backtest', 'Backtest'],
    ['/compare', 'Compare'],
    ['/optimize', 'Optimize'],
  ])('%s says it still runs in Streamlit', async (path, title) => {
    renderApp(path)
    expect(await screen.findByText(`${title} still runs in the Streamlit app`)).toBeInTheDocument()
    expect(screen.getByText('uv run streamlit run apps/dashboard/app.py')).toBeInTheDocument()
  })

  it('shows a not-found page for unknown paths', async () => {
    renderApp('/nowhere')
    expect(await screen.findByText('No such page')).toBeInTheDocument()
  })
})

describe('top bar', () => {
  it('reports the API version and data freshness', async () => {
    renderApp('/live')
    expect(await screen.findByText('API v0.1.0')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('freshness')).toHaveTextContent(/^As of \d\d:\d\d:\d\d/))
  })

  it('switches theme and remembers it', async () => {
    renderApp('/live')
    await userEvent.click(screen.getByRole('radio', { name: 'Dark theme' }))
    expect(document.documentElement).toHaveClass('dark')
    expect(window.localStorage.getItem('midfreq.theme')).toBe('dark')
    await userEvent.click(screen.getByRole('radio', { name: 'Light theme' }))
    expect(document.documentElement).not.toHaveClass('dark')
  })
})

describe('when the API is down or failing', () => {
  it('says the API is unreachable and how to start it', async () => {
    server.use(apiDown)
    renderApp('/live')
    expect(await screen.findByText('API offline')).toBeInTheDocument()
    expect((await screen.findAllByText('API unreachable')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('uv run uvicorn midfreq.api.main:app --reload').length).toBeGreaterThan(0)
  })

  it('shows an endpoint the API does not implement yet as such', async () => {
    server.use(http.get('*/api/results/overview', () => HttpResponse.json({ detail: 'not implemented yet' }, { status: 501 })))
    renderApp('/overview')
    expect(await screen.findByText('Not available yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })
})
