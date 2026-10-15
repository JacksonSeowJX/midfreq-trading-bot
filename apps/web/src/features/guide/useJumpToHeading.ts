import { useCallback, useRef, type MouseEvent } from 'react'
import { useNavigate } from 'react-router'

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Following a #section link inside the guide: scroll the heading into view (inside the app's
 * scrolling main area) and put its anchor in the URL, without a new history entry. `ownHash`
 * holds the hash set this way, so the page does not scroll to it a second time.
 */
export function useJumpToHeading() {
  const navigate = useNavigate()
  const ownHash = useRef<string | null>(null)
  const jump = useCallback(
    (e: MouseEvent<HTMLAnchorElement>, id: string) => {
      const el = document.getElementById(id)
      if (!el || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
      e.preventDefault()
      el.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' })
      ownHash.current = `#${id}`
      void navigate({ hash: `#${id}` }, { replace: true, preventScrollReset: true })
    },
    [navigate],
  )
  return { jump, ownHash }
}
