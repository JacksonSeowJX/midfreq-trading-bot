import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { createElement } from 'react'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'
import { server } from './handlers'

// ── what jsdom lacks ───────────────────────────────────────────────────────

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList
}

// Radix uses pointer capture and scrollIntoView, which jsdom does not implement
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}
Element.prototype.scrollIntoView ??= () => {}

// ── canvas charts cannot draw in jsdom; tests check everything around them ──

vi.mock('@/components/charts/EquityChart', () => ({
  EquityChart: ({ label }: { label: string }) => createElement('div', { role: 'img', 'aria-label': label }),
}))
vi.mock('@/components/charts/EChart', () => ({
  EChart: ({ label }: { label: string }) => createElement('div', { role: 'img', 'aria-label': label }),
}))
vi.mock('@/components/charts/PriceChart', () => ({
  PriceChart: ({ label, trades }: { label: string; trades: unknown[] }) =>
    createElement('div', { role: 'img', 'aria-label': label, 'data-markers': trades.length }),
}))
vi.mock('@/components/charts/MultiLineChart', () => ({
  MultiLineChart: ({ label, lines }: { label: string; lines: { name: string; color: string }[] }) =>
    createElement(
      'div',
      { role: 'img', 'aria-label': label },
      createElement(
        'ul',
        { 'aria-label': 'Legend' },
        lines.map((l) => createElement('li', { key: l.name, 'data-color': l.color }, l.name)),
      ),
    ),
}))

// ── the mock API ────────────────────────────────────────────────────────────

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
  window.localStorage.clear()
})
afterAll(() => server.close())
