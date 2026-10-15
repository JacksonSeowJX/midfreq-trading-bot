import { FileJsonIcon, FileSpreadsheetIcon, SearchIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useResearchFiles } from '@/api/queries'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, QueryState, TableSkeleton } from '@/components/states'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { LabeledSwitch } from '@/components/ui/switch'
import { fmtShortDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { FileViewer } from './FileViewer'
import { listStudies, type ListedFile } from './logic'

function FileList({
  items,
  selected,
  onSelect,
}: {
  items: ListedFile[]
  selected: string | undefined
  onSelect: (name: string) => void
}) {
  if (items.length === 0) return <EmptyState title="No files match" className="m-3" />
  return (
    <ul aria-label="Research files" className="divide-y overflow-y-auto" style={{ maxHeight: 'calc(100vh - 260px)' }}>
      {items.map(({ file, title }) => {
        const Icon = file.kind === 'csv' ? FileSpreadsheetIcon : FileJsonIcon
        const active = file.name === selected
        return (
          <li key={file.name}>
            <button
              type="button"
              aria-current={active || undefined}
              onClick={() => onSelect(file.name)}
              className={cn(
                'flex w-full cursor-pointer items-start gap-2 px-3 py-2 text-left hover:bg-accent',
                active && 'bg-accent shadow-[inset_2px_0_0_var(--brand)]',
              )}
            >
              <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block text-[13px] font-medium leading-snug">{title}</span>
                <span className="block truncate text-[11px] text-muted-foreground">
                  {fmtShortDateTime(file.modified)} · {file.study}
                </span>
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export default function ResearchPage() {
  const files = useResearchFiles()
  const [params, setParams] = useSearchParams()
  const [showAll, setShowAll] = useState(false)
  const [query, setQuery] = useState('')
  const listed = useMemo(() => listStudies(files.data ?? [], showAll), [files.data, showAll])
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? listed.filter((l) => `${l.title} ${l.file.name}`.toLowerCase().includes(q)) : listed
  }, [listed, query])
  const wanted = params.get('file')
  const current =
    (files.data ?? []).find((f) => f.name === wanted) ?? visible[0]?.file ?? undefined
  const currentTitle =
    listed.find((l) => l.file.name === current?.name)?.title ?? current?.label ?? current?.name ?? ''

  return (
    <>
      <PageHeader
        title="Research results"
        description="The raw results file of any study in results/: a table for CSV, a tree for JSON."
        actions={
          <LabeledSwitch
            label="Show all raw result files (including early exploration runs)"
            checked={showAll}
            onCheckedChange={setShowAll}
          />
        }
      />
      <QueryState query={files} skeleton={<TableSkeleton rows={12} />}>
        {(all) =>
          all.length === 0 ? (
            <EmptyState title="No research results found in results/">
              Run one of the studies in <code className="font-mono">research/studies/</code> first.
            </EmptyState>
          ) : (
            <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
              <Card className="min-w-0 self-start overflow-hidden">
                <div className="border-b p-2">
                  <div className="relative">
                    <SearchIcon
                      className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground"
                      aria-hidden
                    />
                    <Input
                      aria-label="Filter studies"
                      placeholder={`Filter ${listed.length} ${showAll ? 'files' : 'studies'}`}
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      className="pl-8"
                    />
                  </div>
                </div>
                <FileList
                  items={visible}
                  selected={current?.name}
                  onSelect={(name) => setParams({ file: name }, { replace: true })}
                />
              </Card>
              {current ? (
                <FileViewer key={current.name} file={current} title={currentTitle} />
              ) : (
                <EmptyState title="Select a file" />
              )}
            </div>
          )
        }
      </QueryState>
    </>
  )
}
