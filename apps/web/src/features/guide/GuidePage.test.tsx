import guideSource from '@docs/DASHBOARD_GUIDE.md?raw'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { findPreset } from '@/features/runs/presets'
import type { JobKind } from '@/api/types'
import { renderApp } from '@/test/render'
import { classifyLink, guideHeadings, headingText, presetLinks, tocEntries } from './logic'

const headings = guideHeadings(guideSource)
const toc = tocEntries(headings)

describe('the guide page', () => {
  it('is in the left nav under Help, and renders the guide’s title', async () => {
    const { router } = renderApp('/live')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByText('Help')).toBeInTheDocument()
    await userEvent.click(within(nav).getByRole('link', { name: 'Guide' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard guide' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/guide')
    expect(within(nav).getByRole('link', { name: 'Guide' })).toHaveAttribute('aria-current', 'page')
  })

  it('renders every section of docs/DASHBOARD_GUIDE.md as a heading with GitHub’s anchor', async () => {
    renderApp('/guide')
    const article = await screen.findByRole('article', { name: 'Dashboard guide' })
    for (const name of ['A three-minute tour', 'Live', 'Overview', 'Backtest', 'Glossary', 'Frequently asked questions']) {
      expect(within(article).getByRole('heading', { level: 2, name })).toBeInTheDocument()
    }
    for (const h of headings.filter((x) => x.depth === 2 || x.depth === 3)) {
      const el = within(article).getByRole('heading', { level: h.depth, name: h.text })
      expect(el).toHaveAttribute('id', h.id)
    }
    expect(document.getElementById('glossary')).toHaveTextContent('Glossary')
    // GitHub-flavoured tables
    expect(within(article).getAllByRole('table').length).toBeGreaterThan(3)
    expect(within(article).getByRole('columnheader', { name: 'Term' })).toBeInTheDocument()
  })

  it('has a table of contents whose links point at the headings on the page', async () => {
    renderApp('/guide')
    const contents = await screen.findByRole('navigation', { name: 'On this page' })
    const links = within(contents).getAllByRole('link')
    expect(links.map((a) => a.textContent)).toEqual(toc.map((h) => h.text))
    for (const a of links) {
      const id = a.getAttribute('href')!.slice(1)
      expect(document.getElementById(id), id).not.toBeNull()
    }
    expect(within(contents).getByRole('link', { name: 'Example C: the live settings in a backtest, and the cost of fees' })).toHaveAttribute(
      'href',
      '#example-c-the-live-settings-in-a-backtest-and-the-cost-of-fees',
    )
  })

  it('follows a contents link within the page, keeping the anchor in the URL', async () => {
    const { router } = renderApp('/guide')
    const contents = await screen.findByRole('navigation', { name: 'On this page' })
    await userEvent.click(within(contents).getByRole('link', { name: 'Glossary' }))
    await waitFor(() => expect(router.state.location.hash).toBe('#glossary'))
    expect(router.state.location.pathname).toBe('/guide')
  })

  it('opens app pages as router links: a Try it link prefills the run form', async () => {
    const { router } = renderApp('/guide')
    const article = await screen.findByRole('article', { name: 'Dashboard guide' })
    const tryIt = within(article).getAllByRole('link', { name: "Backtest, with the live forward test's settings" })[0]!
    expect(tryIt).toHaveAttribute('href', '/backtest?preset=forward-test')
    expect(within(article).getByRole('link', { name: 'Overview' })).toHaveAttribute('href', '/overview')
    await userEvent.click(tryIt)
    expect(await screen.findByRole('heading', { level: 1, name: 'Backtest' })).toBeInTheDocument()
    expect(await screen.findByLabelText('Lookback (candles)')).toHaveValue(30)
    expect(screen.getByText(/Filled in from the guide/)).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.search).toBe(''))
  })

  it('opens other sites in a new tab', async () => {
    renderApp('/guide')
    const article = await screen.findByRole('article', { name: 'Dashboard guide' })
    const hosted = within(article).getByRole('link', { name: /run\.app/ })
    expect(hosted).toHaveAttribute('target', '_blank')
    expect(hosted).toHaveAttribute('rel', 'noreferrer')
  })
})

describe('the guide’s links', () => {
  it('every Try it link names a preset of its page', () => {
    const links = presetLinks(guideSource)
    expect(links.map((l) => `${l.path}?preset=${l.preset}`)).toEqual(
      expect.arrayContaining([
        '/backtest?preset=forward-test',
        '/backtest?preset=forward-test-hk-fees',
        '/compare?preset=oscillators',
        '/optimize?preset=walk-forward',
      ]),
    )
    for (const l of links) expect(findPreset(l.path.slice(1) as JobKind, l.preset), `${l.path} ${l.preset}`).toBeDefined()
  })

  it('every #anchor in the guide is a heading of the guide', () => {
    const ids = new Set(headings.map((h) => h.id))
    const anchors = [...guideSource.matchAll(/\]\(#([^)]+)\)/g)].map((m) => m[1]!)
    expect(anchors.length).toBeGreaterThan(5)
    for (const a of anchors) expect(ids.has(a), a).toBe(true)
  })
})

describe('guide helpers', () => {
  it('reads headings as GitHub does: inline markup dropped, code fences skipped, repeats numbered', () => {
    const md = [
      '# Title',
      '## The `Run` button and **bold** [links](/x)',
      '```bash',
      '# not a heading',
      '```',
      '## Notes',
      '### Notes',
      '#### Deep',
      '## Why is it so? ##',
    ].join('\n')
    const hs = guideHeadings(md)
    expect(hs.map((h) => [h.depth, h.text, h.id])).toEqual([
      [1, 'Title', 'title'],
      [2, 'The Run button and bold links', 'the-run-button-and-bold-links'],
      [2, 'Notes', 'notes'],
      [3, 'Notes', 'notes-1'],
      [4, 'Deep', 'deep'],
      [2, 'Why is it so?', 'why-is-it-so'],
    ])
    expect(tocEntries(hs).map((h) => h.id)).toEqual(['the-run-button-and-bold-links', 'notes', 'notes-1', 'why-is-it-so'])
    expect(headingText('*Try it*: `x`')).toBe('Try it: x')
  })

  it('tells section, app and outside links apart', () => {
    expect(classifyLink('#glossary')).toEqual({ kind: 'hash', id: 'glossary' })
    expect(classifyLink('/backtest?preset=forward-test')).toEqual({ kind: 'app', to: '/backtest?preset=forward-test' })
    expect(classifyLink('//example.com')).toEqual({ kind: 'external', href: '//example.com' })
    expect(classifyLink('https://docs.astral.sh/uv/')).toEqual({ kind: 'external', href: 'https://docs.astral.sh/uv/' })
  })
})
