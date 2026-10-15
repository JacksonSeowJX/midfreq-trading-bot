import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'

type SortValue = string | number | boolean | null | undefined

export interface Column<T> {
  id: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** makes the column sortable */
  sortValue?: (row: T) => SortValue
  align?: 'left' | 'right' | 'center'
  className?: string
  headerClassName?: string
}

export interface SortState {
  id: string
  desc: boolean
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  label: string
  initialSort?: SortState
  onRowClick?: (row: T) => void
  rowClassName?: (row: T) => string | undefined
  selectedKey?: string | null
  maxHeight?: number
  empty?: ReactNode
  className?: string
}

function compare(a: SortValue, b: SortValue): number {
  const an = a === null || a === undefined || (typeof a === 'number' && Number.isNaN(a))
  const bn = b === null || b === undefined || (typeof b === 'number' && Number.isNaN(b))
  if (an || bn) return an === bn ? 0 : an ? 1 : -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b)
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
}

const alignClass = { left: 'text-left', right: 'num', center: 'text-center' } as const

/**
 * The dashboard's table: dense rows, sticky header, numbers right-aligned in tabular figures,
 * click a header to sort (again to reverse). Missing values always sort last.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  label,
  initialSort,
  onRowClick,
  rowClassName,
  selectedKey,
  maxHeight = 420,
  empty,
  className,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState | null>(initialSort ?? null)

  const sorted = useMemo(() => {
    const col = sort ? columns.find((c) => c.id === sort.id) : undefined
    if (!sort || !col?.sortValue) return rows
    const get = col.sortValue
    const out = [...rows].sort((x, y) => {
      const a = get(x)
      const b = get(y)
      const missing = (v: SortValue) => v === null || v === undefined
      if (missing(a) || missing(b)) return compare(a, b) // nulls last in both directions
      return sort.desc ? compare(b, a) : compare(a, b)
    })
    return out
  }, [rows, columns, sort])

  function toggle(col: Column<T>) {
    if (!col.sortValue) return
    setSort((s) => (s?.id === col.id ? { id: col.id, desc: !s.desc } : { id: col.id, desc: false }))
  }

  if (rows.length === 0 && empty) return <>{empty}</>

  return (
    <div className={cn('overflow-auto rounded-md border', className)} style={{ maxHeight }}>
      <Table aria-label={label}>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {columns.map((col) => {
              const active = sort?.id === col.id
              const ariaSort = active ? (sort.desc ? 'descending' : 'ascending') : col.sortValue ? 'none' : undefined
              const Icon = active ? (sort.desc ? ArrowDownIcon : ArrowUpIcon) : ArrowUpDownIcon
              return (
                <TableHead
                  key={col.id}
                  aria-sort={ariaSort}
                  className={cn(col.align === 'right' && 'text-right', col.align === 'center' && 'text-center', col.headerClassName)}
                >
                  {col.sortValue ? (
                    <button
                      type="button"
                      onClick={() => toggle(col)}
                      className={cn(
                        'inline-flex cursor-pointer items-center gap-1 uppercase hover:text-foreground',
                        col.align === 'right' && 'flex-row-reverse',
                        active && 'text-foreground',
                      )}
                    >
                      {col.header}
                      <Icon className={cn('size-3', !active && 'opacity-40')} aria-hidden />
                    </button>
                  ) : (
                    col.header
                  )}
                </TableHead>
              )
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((row, i) => {
            const key = rowKey(row, i)
            return (
              <TableRow
                key={key}
                data-selected={selectedKey === key || undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={
                  onRowClick
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onRowClick(row)
                        }
                      }
                    : undefined
                }
                tabIndex={onRowClick ? 0 : undefined}
                className={cn(
                  onRowClick && 'cursor-pointer',
                  'data-[selected]:bg-accent',
                  rowClassName?.(row),
                )}
              >
                {columns.map((col) => (
                  <TableCell key={col.id} className={cn(alignClass[col.align ?? 'left'], col.className)}>
                    {col.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
