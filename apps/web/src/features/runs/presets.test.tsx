import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { StrategyInfo } from '@/api/types'
import { capabilities, strategies } from '@/test/fixtures'
import { server } from '@/test/handlers'
import { renderApp } from '@/test/render'
import { DEFAULT_RISK, defaultForm, formKey } from './form'
import { findPreset, PRESETS, presetPath } from './presets'

/** The fixture registry plus the two oscillators the compare preset asks for. */
const withOscillators: StrategyInfo[] = [
  ...strategies,
  {
    name: 'RSI',
    params: [
      { name: 'rsi_period', label: 'RSI Period', default: 14, min: 2, max: 50, step: 1 },
      { name: 'oversold', label: 'Oversold Threshold', default: 30, min: 10, max: 40, step: 1 },
      { name: 'overbought', label: 'Overbought Threshold', default: 70, min: 60, max: 95, step: 1 },
    ],
  },
  {
    name: 'MACD',
    params: [
      { name: 'fast_ema', label: 'Fast EMA Period', default: 12, min: 2, max: 50, step: 1 },
      { name: 'slow_ema', label: 'Slow EMA Period', default: 26, min: 10, max: 100, step: 1 },
      { name: 'signal_period', label: 'Signal Line Period', default: 9, min: 2, max: 30, step: 1 },
    ],
  },
]

/** Records what each run kind posts; no run is started unless Run is pressed. */
function capturePosts() {
  const posted: { kind: string; body: Record<string, unknown> }[] = []
  server.use(
    http.post('*/api/jobs/:kind', async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>
      posted.push({ kind: String(params.kind), body })
      return HttpResponse.json(
        { id: 'job-new', kind: params.kind, status: 'queued', progress: 0, message: '', created: '2026-10-16T01:00:00Z', request: body },
        { status: 202 },
      )
    }),
  )
  return posted
}

const DEFAULT_RISK_BODY = {
  stop_loss_pct: null,
  trailing_stop_pct: null,
  take_profit_pct: null,
  max_drawdown_pct: 0.1,
  sizing: 'fixed_quantity',
  qty: 100,
}

describe('presets', () => {
  it('each preset has a page and a unique id', () => {
    expect(new Set(PRESETS.map((p) => p.id)).size).toBe(PRESETS.length)
    expect(PRESETS.map(presetPath)).toEqual([
      '/backtest?preset=forward-test',
      '/backtest?preset=forward-test-hk-fees',
      '/compare?preset=oscillators',
      '/optimize?preset=walk-forward',
    ])
    expect(findPreset('backtest', 'forward-test')?.title).toMatch(/live forward test/)
    // a preset belongs to one kind of run
    expect(findPreset('compare', 'forward-test')).toBeUndefined()
    expect(findPreset('backtest', 'nope')).toBeUndefined()
    expect(findPreset('backtest', null)).toBeUndefined()
  })

  it('start from the defaults, with only their own settings changed', () => {
    const today = new Date(2026, 9, 16)
    const form = findPreset('backtest', 'forward-test')!.form(today)
    expect(form).toEqual({
      ...defaultForm(today),
      strategy: 'Cross-Sectional Reversal',
      params: { 'Cross-Sectional Reversal': { lookback: '30', top_n: '3', rebalance_every: '19' } },
      markets: { US: { symbol: 'US.AAPL', pairSymbol: '', universe: 'sp100', commissionPct: '0.005' } },
      start: '2025-09-25',
      end: '2026-09-25',
    })
    expect(form.risk).toEqual(DEFAULT_RISK)
  })
})

describe('opening a preset link', () => {
  it('forward-test fills in the live settings, without running, and drops itself from the URL', async () => {
    const posted = capturePosts()
    const user = userEvent.setup()
    const { router } = renderApp('/backtest?preset=forward-test')

    expect(await screen.findByLabelText('Strategy')).toHaveValue('Cross-Sectional Reversal')
    expect(screen.getByLabelText('Lookback (candles)')).toHaveValue(30)
    expect(screen.getByLabelText('Basket Size (# laggards held)')).toHaveValue(3)
    expect(screen.getByLabelText('Rebalance Every N Candles')).toHaveValue(19)
    expect(screen.getByLabelText('Universe')).toHaveValue('sp100')
    expect(screen.getByRole('combobox', { name: 'Chart symbol' })).toHaveValue('US.AAPL')
    expect(screen.getByLabelText('Start')).toHaveValue('2025-09-25')
    expect(screen.getByLabelText('End')).toHaveValue('2026-09-25')
    expect(screen.getByLabelText('Starting capital (USD)')).toHaveValue(100000)
    expect(screen.getByLabelText('Commission per side (%)')).toHaveValue(0.005)
    expect(screen.getByLabelText('Slippage (bps)')).toHaveValue(0)
    expect(screen.getByText('Filled in from the guide:').closest('[role="status"]')).toHaveTextContent(
      /the live forward test’s settings.*Nothing has run yet; press Run backtest below/,
    )

    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(posted).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    const { kind, body } = posted[0]!
    expect(kind).toBe('backtest')
    expect(body).toMatchObject({
      market: 'US',
      strategy: 'Cross-Sectional Reversal',
      params: { lookback: 30, top_n: 3, rebalance_every: 19 },
      universe: 'sp100',
      symbol: 'US.AAPL',
      timeframe: '1h',
      start: '2025-09-25',
      end: '2026-09-25',
      initial_capital: 100000,
      slippage_bps: 0,
      risk: DEFAULT_RISK_BODY,
    })
    expect(body.commission_rate).toBeCloseTo(0.00005, 10)
    // the note goes once the run starts
    await waitFor(() => expect(screen.queryByText(/Filled in from the guide/)).not.toBeInTheDocument())
  })

  it('forward-test-hk-fees is the same at the Hong Kong commission, and switches the market to US', async () => {
    const posted = capturePosts()
    const user = userEvent.setup()
    renderApp('/backtest?preset=forward-test-hk-fees', { market: 'HK' })

    expect(await screen.findByLabelText('Commission per side (%)')).toHaveValue(0.16)
    expect(screen.getByRole('radio', { name: 'United States (USD)' })).toHaveAttribute('data-state', 'on')
    expect(screen.getByLabelText('Lookback (candles)')).toHaveValue(30)
    expect(screen.getByRole('button', { name: 'Reset to 0.005%' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Run backtest' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]!.body).toMatchObject({ market: 'US', universe: 'sp100', params: { lookback: 30, top_n: 3, rebalance_every: 19 } })
    expect(posted[0]!.body.commission_rate).toBeCloseTo(0.0016, 10)
  })

  it('oscillators ticks RSI, MACD and Bollinger Bands on US.AAPL', async () => {
    server.use(http.get('*/api/strategies', () => HttpResponse.json(withOscillators)))
    const posted = capturePosts()
    const user = userEvent.setup()
    renderApp('/compare?preset=oscillators')

    const list = await screen.findByRole('list', { name: 'Strategies to compare' })
    const ticked = within(list)
      .getAllByRole('checkbox')
      .filter((c) => (c as HTMLInputElement).checked)
      .map((c) => c.closest('li')?.querySelector('label')?.textContent)
    expect(ticked.sort()).toEqual(['Bollinger Bands', 'MACD', 'RSI'])
    expect(screen.getByRole('combobox', { name: 'Symbol' })).toHaveValue('US.AAPL')
    expect(screen.getByText('3 picked, 2–8')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Compare strategies' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    const body = posted[0]!.body
    expect(body).toMatchObject({ market: 'US', symbol: 'US.AAPL', start: '2025-09-25', end: '2026-09-25', universe: null })
    expect(body.strategies).toEqual(
      expect.arrayContaining([
        { strategy: 'RSI', params: { rsi_period: 14, oversold: 30, overbought: 70 } },
        { strategy: 'MACD', params: { fast_ema: 12, slow_ema: 26, signal_period: 9 } },
        { strategy: 'Bollinger Bands', params: { bb_period: 20, num_std: 2 } },
      ]),
    )
    expect(body.strategies).toHaveLength(3)
  })

  it('walk-forward sets up the US 15 walk-forward', async () => {
    const posted = capturePosts()
    const user = userEvent.setup()
    renderApp('/optimize?preset=walk-forward')

    expect(await screen.findByLabelText('Strategy')).toHaveValue('Cross-Sectional Reversal')
    expect(screen.getByLabelText('Universe')).toHaveValue('us_15')
    expect(screen.getByRole('radio', { name: 'Walk-forward' })).toHaveAttribute('data-state', 'on')
    expect(screen.getByLabelText('Objective')).toHaveValue('sharpe_ratio')
    expect(screen.getByLabelText('Rolling windows')).toHaveValue(3)
    expect(screen.getByLabelText('Train share (%)')).toHaveValue(60)

    await user.click(screen.getByRole('button', { name: 'Run walk-forward' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({
      kind: 'optimize',
      body: { strategy: 'Cross-Sectional Reversal', universe: 'us_15', method: 'walk_forward', objective: 'sharpe_ratio', n_splits: 3, train_pct: 0.6, start: '2025-09-25', end: '2026-09-25' },
    })
  })
})

describe('a preset where its kind is not allowed', () => {
  it('the hosted Optimize page explains the local install, and the preset is dropped', async () => {
    server.use(http.get('*/api/jobs/capabilities', () => HttpResponse.json({ ...capabilities, kinds: ['backtest', 'compare'] })))
    const { router } = renderApp('/optimize?preset=walk-forward')
    expect(await screen.findByText('Optimize is available on a local install')).toBeInTheDocument()
    expect(screen.getByText(/uv run uvicorn midfreq\.api\.main:app/)).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.queryByRole('button', { name: 'Run walk-forward' })).not.toBeInTheDocument()
  })
})

describe('presets and the remembered inputs', () => {
  const remembered = { ...defaultForm(), strategy: 'SMA Crossover', capital: '250000' }

  it('without a preset the form opens as last used', async () => {
    window.localStorage.setItem(formKey('backtest'), JSON.stringify(remembered))
    renderApp('/backtest')
    expect(await screen.findByLabelText('Strategy')).toHaveValue('SMA Crossover')
    expect(screen.getByLabelText('Starting capital (USD)')).toHaveValue(250000)
    expect(screen.queryByText(/Filled in from the guide/)).not.toBeInTheDocument()
  })

  it('a preset replaces them, and is remembered in turn', async () => {
    window.localStorage.setItem(formKey('backtest'), JSON.stringify(remembered))
    renderApp('/backtest?preset=forward-test')
    expect(await screen.findByLabelText('Strategy')).toHaveValue('Cross-Sectional Reversal')
    expect(screen.getByLabelText('Starting capital (USD)')).toHaveValue(100000)
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem(formKey('backtest'))!).strategy).toBe('Cross-Sectional Reversal'))
  })

  it('the note goes at the first edit', async () => {
    const user = userEvent.setup()
    renderApp('/backtest?preset=forward-test')
    const lookback = await screen.findByLabelText('Lookback (candles)')
    expect(screen.getByText(/Filled in from the guide/)).toBeInTheDocument()
    await user.clear(lookback)
    await user.type(lookback, '25')
    expect(screen.queryByText(/Filled in from the guide/)).not.toBeInTheDocument()
  })

  it.each([
    ['an unknown preset', '/backtest?preset=nope'],
    ['another page’s preset', '/backtest?preset=oscillators'],
  ])('%s is ignored and dropped from the URL', async (_, path) => {
    window.localStorage.setItem(formKey('backtest'), JSON.stringify(remembered))
    const { router } = renderApp(path)
    expect(await screen.findByLabelText('Strategy')).toHaveValue('SMA Crossover')
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.queryByText(/Filled in from the guide/)).not.toBeInTheDocument()
  })

  it('a preset opened while the form is on screen fills it in', async () => {
    window.localStorage.setItem(formKey('backtest'), JSON.stringify(remembered))
    const { router } = renderApp('/backtest')
    expect(await screen.findByLabelText('Strategy')).toHaveValue('SMA Crossover')
    await router.navigate('/backtest?preset=forward-test-hk-fees')
    await waitFor(() => expect(screen.getByLabelText('Strategy')).toHaveValue('Cross-Sectional Reversal'))
    expect(screen.getByLabelText('Commission per side (%)')).toHaveValue(0.16)
  })
})
