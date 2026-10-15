import { ArrowUpRightIcon, PlayIcon } from 'lucide-react'
import { createContext, useContext, type MouseEvent, type ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import { Link } from 'react-router'
import rehypeSlug from 'rehype-slug'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import { classifyLink } from './logic'

type Jump = (e: MouseEvent<HTMLAnchorElement>, id: string) => void

/** How a #section link is followed, from the page (it owns the scrolling and the URL). */
const JumpContext = createContext<Jump | null>(null)

const link = 'font-medium text-primary underline-offset-2 hover:underline'

function GuideLink({ href = '', children }: { href?: string; children?: ReactNode }) {
  const jump = useContext(JumpContext)
  const target = classifyLink(href)
  if (target.kind === 'hash') {
    return (
      <a href={href} className={link} onClick={jump ? (e) => jump(e, target.id) : undefined}>
        {children}
      </a>
    )
  }
  if (target.kind === 'app') {
    // a page of the app; one that opens a run form prefilled reads as an action
    const tryIt = target.to.includes('preset=')
    return (
      <Link
        to={target.to}
        className={cn(
          link,
          // wraps like text on a narrow screen, each line keeping the chip's border
          tryIt &&
            'rounded-md border bg-card box-decoration-clone px-1.5 py-0.5 text-[13px] no-underline hover:bg-accent hover:no-underline',
        )}
      >
        {tryIt ? <PlayIcon aria-hidden className="mr-1 inline size-3 align-[-1px]" /> : null}
        {children}
      </Link>
    )
  }
  return (
    <a href={target.href} target="_blank" rel="noreferrer" className={cn(link, 'inline-flex items-baseline gap-0.5')}>
      {children}
      <ArrowUpRightIcon aria-hidden className="size-3 shrink-0 self-center" />
    </a>
  )
}

/** A heading with its anchor: a mouse convenience only, so it is hidden from assistive technology. */
function heading(Tag: 'h1' | 'h2' | 'h3' | 'h4', className: string) {
  return function Heading({ id, children }: { id?: string; children?: ReactNode }) {
    const jump = useContext(JumpContext)
    return (
      <Tag id={id} className={cn('group scroll-mt-4', className)}>
        {children}
        {id && Tag !== 'h1' ? (
          <a
            href={`#${id}`}
            aria-hidden
            tabIndex={-1}
            onClick={jump ? (e) => jump(e, id) : undefined}
            className="ml-2 font-normal text-muted-foreground no-underline opacity-0 transition-opacity group-hover:opacity-100"
          >
            #
          </a>
        ) : null}
      </Tag>
    )
  }
}

// Markdown elements in the app's typography: 14px body, the card and muted surfaces, brand rules.
// `node` (passed by react-markdown) is dropped before anything reaches the DOM.
const components: Components = {
  h1: heading('h1', 'text-xl font-semibold tracking-tight'),
  h2: heading('h2', 'mb-3 mt-10 border-b pb-2 text-[17px] font-semibold tracking-tight'),
  h3: heading('h3', 'mb-2 mt-7 text-[15px] font-semibold'),
  h4: heading('h4', 'mb-1.5 mt-5 text-[14px] font-semibold'),
  p: ({ node: _n, ...props }) => <p className="my-3 leading-relaxed" {...props} />,
  a: ({ node: _n, href, children }) => <GuideLink href={href}>{children}</GuideLink>,
  ul: ({ node: _n, ...props }) => (
    <ul className="my-3 list-disc space-y-1 pl-5 marker:text-muted-foreground [&_ol]:my-1.5 [&_ul]:my-1.5" {...props} />
  ),
  ol: ({ node: _n, ...props }) => (
    <ol className="my-3 list-decimal space-y-1.5 pl-5 marker:text-muted-foreground [&_ol]:my-1.5 [&_ul]:my-1.5" {...props} />
  ),
  li: ({ node: _n, ...props }) => <li className="pl-1 leading-relaxed [&>p]:my-1" {...props} />,
  strong: ({ node: _n, ...props }) => <strong className="font-semibold text-foreground" {...props} />,
  blockquote: ({ node: _n, ...props }) => (
    <blockquote
      className="my-4 rounded-r-md border-l-2 border-brand bg-muted/60 px-3.5 py-2 text-[13px] text-muted-foreground [&>p]:my-1.5 [&_strong]:text-foreground"
      {...props}
    />
  ),
  code: ({ node: _n, className, ...props }) => (
    <code className={cn('rounded bg-muted px-1 py-px font-mono text-[0.86em]', className)} {...props} />
  ),
  pre: ({ node: _n, ...props }) => (
    <pre
      className="my-3 overflow-x-auto rounded-md border bg-muted px-3.5 py-2.5 font-mono text-[12px] leading-relaxed [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[12px]"
      {...props}
    />
  ),
  hr: () => <hr className="my-8" />,
  table: ({ node: _n, ...props }) => (
    <div className="my-4 overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-[13px]" {...props} />
    </div>
  ),
  thead: ({ node: _n, ...props }) => <thead className="bg-muted" {...props} />,
  tr: ({ node: _n, ...props }) => <tr className="border-b last:border-0" {...props} />,
  th: ({ node: _n, ...props }) => (
    <th
      className="border-b px-3 py-2 text-left align-bottom text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
      {...props}
    />
  ),
  td: ({ node: _n, ...props }) => <td className="px-3 py-2 align-top leading-relaxed" {...props} />,
}

/** The guide's markdown (GitHub-flavoured: tables, autolinks), its headings carrying GitHub's anchors. */
export function GuideMarkdown({ source, onJump }: { source: string; onJump?: Jump }) {
  return (
    <JumpContext.Provider value={onJump ?? null}>
      <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]} components={components}>
        {source}
      </Markdown>
    </JumpContext.Provider>
  )
}
