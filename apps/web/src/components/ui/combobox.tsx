import { ChevronsUpDownIcon } from 'lucide-react'
import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { cn } from '@/lib/utils'

const SHOWN = 80

/**
 * Pick one value from a long list by typing part of it ("aapl" finds US.AAPL). The ARIA
 * combobox pattern: arrows move, Enter picks, Escape gives up. Text that matches no option
 * when focus leaves is dropped, so the value is always one of the options.
 */
export function Combobox({
  id,
  value,
  options,
  onChange,
  placeholder,
  invalid,
  describedBy,
  emptyText = 'No match',
  className,
}: {
  id?: string
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
  invalid?: boolean
  describedBy?: string
  emptyText?: string
  className?: string
}) {
  const autoId = useId()
  const inputId = id ?? autoId
  const listId = `${inputId}-list`
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  const matches = useMemo(() => {
    const q = text.trim().toLowerCase()
    if (!editing || !q || text === value) return options
    return options.filter((o) => o.toLowerCase().includes(q))
  }, [options, text, editing, value])
  const shown = matches.slice(0, SHOWN)
  const activeIndex = Math.min(active, Math.max(shown.length - 1, 0))

  function choose(v: string) {
    onChange(v)
    setEditing(false)
    setOpen(false)
  }

  function commitTyped() {
    const q = text.trim().toLowerCase()
    const exact = options.find((o) => o.toLowerCase() === q || o.toLowerCase().split('.').slice(1).join('.') === q)
    if (exact && exact !== value) onChange(exact)
    setEditing(false)
    setOpen(false)
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) {
        setOpen(true)
        setEditing(true)
        setText(value)
        return
      }
      const next = e.key === 'ArrowDown' ? Math.min(activeIndex + 1, shown.length - 1) : Math.max(activeIndex - 1, 0)
      setActive(next)
      listRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
    } else if (e.key === 'Enter') {
      if (open && shown[activeIndex] !== undefined) {
        e.preventDefault()
        choose(shown[activeIndex])
      }
    } else if (e.key === 'Escape') {
      if (open) e.preventDefault()
      setEditing(false)
      setOpen(false)
    }
  }

  const optionId = (i: number) => `${listId}-${i}`

  return (
    <div className={cn('relative min-w-0', className)}>
      <input
        id={inputId}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && shown.length ? optionId(activeIndex) : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
        value={editing ? text : value}
        onFocus={(e) => {
          setEditing(true)
          setText(value)
          setActive(Math.max(options.indexOf(value), 0))
          e.currentTarget.select()
        }}
        onClick={() => setOpen(true)}
        onChange={(e) => {
          setEditing(true)
          setText(e.target.value)
          setOpen(true)
          setActive(0)
        }}
        onBlur={commitTyped}
        onKeyDown={onKeyDown}
        className="h-8 w-full min-w-0 rounded-md border bg-card pl-2.5 pr-7 font-mono text-[12.5px] placeholder:font-sans placeholder:text-muted-foreground focus-visible:border-ring focus-visible:outline-none aria-[invalid=true]:border-loss"
      />
      <ChevronsUpDownIcon
        aria-hidden
        className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
      />
      <ul
        ref={listRef}
        id={listId}
        role="listbox"
        hidden={!open}
        className="absolute inset-x-0 top-full z-30 mt-1 max-h-64 overflow-y-auto rounded-md border bg-popover py-1 shadow-md"
      >
        {shown.map((o, i) => (
          <li
            key={o}
            id={optionId(i)}
            role="option"
            aria-selected={o === value}
            data-active={i === activeIndex || undefined}
            // keep focus in the input, so blur does not fire before the pick
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => choose(o)}
            onMouseEnter={() => setActive(i)}
            className="cursor-pointer px-2.5 py-1 font-mono text-[12.5px] data-[active]:bg-accent aria-selected:font-semibold aria-selected:text-brand"
          >
            {o}
          </li>
        ))}
        {matches.length === 0 ? <li className="px-2.5 py-1 text-xs text-muted-foreground">{emptyText}</li> : null}
        {matches.length > SHOWN ? (
          <li className="px-2.5 py-1 text-[11px] text-muted-foreground">
            {matches.length - SHOWN} more: keep typing to narrow the list
          </li>
        ) : null}
      </ul>
    </div>
  )
}
