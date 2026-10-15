import { CheckIcon, XIcon } from 'lucide-react'
import type { ResultRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { EmptyState } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { fmtNum, fmtPct, tone, toneClass } from '@/lib/format'

function Oos({ v }: { v: number }) {
  return <span className={toneClass[tone(v, 2)]}>{fmtPct(v, 2)}</span>
}

const columns: Column<ResultRow>[] = [
  {
    id: 'robust',
    header: 'Result',
    cell: (r) =>
      r.robust ? (
        <Badge variant="gain">
          <CheckIcon aria-hidden /> Pass
        </Badge>
      ) : (
        <Badge variant="outline">
          <XIcon aria-hidden /> Fail
        </Badge>
      ),
    sortValue: (r) => r.robust,
  },
  { id: 'family', header: 'Family', cell: (r) => r.family, sortValue: (r) => r.family },
  { id: 'strategy', header: 'Strategy', cell: (r) => <span className="font-medium">{r.strategy}</span>, sortValue: (r) => r.strategy },
  { id: 'subject', header: 'Run on', cell: (r) => r.subject, sortValue: (r) => r.subject },
  { id: 'a_oos', header: 'A OOS / window', align: 'right', cell: (r) => <Oos v={r.config_a_oos} />, sortValue: (r) => r.config_a_oos },
  { id: 'a_cons', header: 'A windows +', align: 'right', cell: (r) => `${fmtNum(r.config_a_consistency, 0)}%`, sortValue: (r) => r.config_a_consistency },
  { id: 'b_oos', header: 'B OOS / window', align: 'right', cell: (r) => <Oos v={r.config_b_oos} />, sortValue: (r) => r.config_b_oos },
  { id: 'b_cons', header: 'B windows +', align: 'right', cell: (r) => `${fmtNum(r.config_b_consistency, 0)}%`, sortValue: (r) => r.config_b_consistency },
  { id: 'worst', header: 'Worst config', align: 'right', cell: (r) => <Oos v={r.worst_config} />, sortValue: (r) => r.worst_config },
  { id: 'file', header: 'File', cell: (r) => <span className="font-mono text-[11px] text-muted-foreground">{r.file}</span>, sortValue: (r) => r.file },
]

/** Every result against the standard; the ones that passed are highlighted. */
export function ResultsTable({ rows }: { rows: ResultRow[] }) {
  return (
    <DataTable
      label="Results against the validation standard"
      columns={columns}
      rows={rows}
      rowKey={(r, i) => `${r.file}|${r.strategy}|${r.subject}|${i}`}
      rowClassName={(r) => (r.robust ? 'bg-[var(--row-highlight)] font-medium' : undefined)}
      maxHeight={520}
      empty={<EmptyState title="No results match these filters" />}
    />
  )
}
