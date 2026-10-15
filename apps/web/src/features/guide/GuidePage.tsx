import guideSource from '@docs/DASHBOARD_GUIDE.md?raw'
import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { GuideMarkdown } from './GuideMarkdown'
import { guideHeadings, tocEntries, type GuideHeading } from './logic'
import { useJumpToHeading } from './useJumpToHeading'

/** The heading nearest the top of the view, for the table of contents (none in jsdom). */
function useActiveHeading(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(null)
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return
    const els = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => el !== null)
    if (!els.length) return
    const inBand = new Map<string, boolean>()
    // a heading counts once it is in the top third of the view
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) inBand.set(e.target.id, e.isIntersecting)
        const first = els.find((el) => inBand.get(el.id))
        if (first) setActive(first.id)
      },
      { rootMargin: '0px 0px -66% 0px' },
    )
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [ids])
  return active
}

function TableOfContents({
  entries,
  active,
  onJump,
}: {
  entries: GuideHeading[]
  active: string | null
  onJump: ReturnType<typeof useJumpToHeading>['jump']
}) {
  return (
    <nav aria-label="On this page" className="max-h-[calc(100vh-8rem)] overflow-y-auto pb-4">
      <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">On this page</p>
      <ul className="border-l text-[12px] leading-snug">
        {entries.map((h) => (
          <li key={h.id}>
            <a
              href={`#${h.id}`}
              onClick={(e) => onJump(e, h.id)}
              aria-current={active === h.id ? 'location' : undefined}
              className={cn(
                '-ml-px block border-l border-transparent py-1 pr-2 text-muted-foreground transition-colors hover:text-foreground',
                h.depth === 3 ? 'pl-6' : 'pl-3',
                active === h.id && 'border-brand font-medium text-foreground',
              )}
            >
              {h.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}

/**
 * The user guide: docs/DASHBOARD_GUIDE.md, the same file GitHub shows, rendered in the app's
 * typography. Links to app pages are router links (the guide's "Try it" links open a run form
 * prefilled); #section links scroll within the page. On wide screens a table of contents
 * follows along.
 */
export default function GuidePage() {
  const toc = useMemo(() => tocEntries(guideHeadings(guideSource)), [])
  const ids = useMemo(() => toc.map((h) => h.id), [toc])
  const active = useActiveHeading(ids)
  const { jump, ownHash } = useJumpToHeading()
  const { hash } = useLocation()

  // arriving at /guide#section (a link from elsewhere, or a reload): scroll to it once rendered
  useEffect(() => {
    if (!hash || hash === ownHash.current) return
    const el = document.getElementById(decodeURIComponent(hash.slice(1)))
    el?.scrollIntoView({ block: 'start' })
  }, [hash, ownHash])

  return (
    <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_15rem]">
      <Card className="min-w-0 px-5 py-6 sm:px-8 lg:px-10">
        <article aria-label="Dashboard guide" className="mx-auto max-w-[46rem] text-[14px]">
          <GuideMarkdown source={guideSource} onJump={jump} />
        </article>
      </Card>
      <aside className="sticky top-4 hidden xl:block">
        <TableOfContents entries={toc} active={active} onJump={jump} />
      </aside>
    </div>
  )
}
