import { ChevronRightIcon } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'

const PAGE = 100

function Primitive({ value }: { value: unknown }) {
  if (value === null) return <span className="text-muted-foreground">null</span>
  if (typeof value === 'string') return <span className="break-all text-gain">&quot;{value}&quot;</span>
  if (typeof value === 'number') return <span className="tabular-nums text-broker">{String(value)}</span>
  if (typeof value === 'boolean') return <span className="text-bot">{String(value)}</span>
  return <span>{String(value)}</span>
}

function JsonNode({ name, value, depth }: { name?: string; value: unknown; depth: number }) {
  const isObj = value !== null && typeof value === 'object'
  const [open, setOpen] = useState(depth < 1)
  const [limit, setLimit] = useState(PAGE)
  const label = name !== undefined ? <span className="text-foreground">{name}: </span> : null
  if (!isObj) {
    return (
      <div className="py-px pl-4">
        {label}
        <Primitive value={value} />
      </div>
    )
  }
  const isArr = Array.isArray(value)
  const entries: [string, unknown][] = isArr
    ? (value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>)
  const summary = isArr ? `[${entries.length} items]` : `{${entries.length} keys}`
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex cursor-pointer items-center gap-0.5 rounded-sm py-px text-left hover:bg-accent"
      >
        <ChevronRightIcon aria-hidden className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-90')} />
        {label}
        <span className="text-muted-foreground">{summary}</span>
      </button>
      {open ? (
        <div className="ml-[7px] border-l pl-2">
          {entries.slice(0, limit).map(([k, v]) => (
            <JsonNode key={k} name={k} value={v} depth={depth + 1} />
          ))}
          {entries.length > limit ? (
            <button
              type="button"
              onClick={() => setLimit((l) => l + PAGE * 5)}
              className="cursor-pointer pl-4 text-primary hover:underline"
            >
              Show more ({entries.length - limit} hidden)
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

/** A JSON document, collapsible at every level; the first level starts open. */
export function JsonTree({ data }: { data: unknown }) {
  return (
    <div className="overflow-auto rounded-md border bg-card p-2 font-mono text-xs leading-5" style={{ maxHeight: 560 }}>
      <JsonNode value={data} depth={0} />
    </div>
  )
}
