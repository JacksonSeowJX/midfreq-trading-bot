import { ChevronRightIcon } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import type { SavedRunDetail } from '@/api/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { JsonTree } from '@/features/research/JsonTree'
import { num, paramsText } from '@/features/runs/logic'
import { fmtDateTime, fmtMoney, fmtNum, fmtPct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { objectCell, periodText, savedRunSummary, SAVED_KIND_LABEL } from './logic'

const SHOWN_SYMBOLS = 8

const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

function symbolsText(v: unknown): string | null {
  if (!Array.isArray(v) || !v.length) return null
  const names = v.filter((s): s is string => typeof s === 'string')
  const head = names.slice(0, SHOWN_SYMBOLS).join(', ')
  return names.length > SHOWN_SYMBOLS ? `${head} and ${fmtNum(names.length - SHOWN_SYMBOLS)} more` : head
}

function riskText(v: unknown): string | null {
  const r = objectCell(v)
  if (!Object.keys(r).length) return null
  const parts: string[] = []
  const sizing = str(r.sizing) ?? str(r.sizer)
  if (sizing) parts.push(sizing.replace(/_/g, ' ') + (sizing === 'fixed_quantity' && num(r.qty) !== null ? ` (${fmtNum(num(r.qty))})` : ''))
  for (const [key, label] of [
    ['stop_loss_pct', 'stop'],
    ['trailing_stop_pct', 'trailing stop'],
    ['take_profit_pct', 'take profit'],
    ['max_drawdown_pct', 'drawdown halt'],
  ] as const) {
    const x = num(r[key])
    if (x !== null) parts.push(`${label} ${fmtPct(x * 100, 1, false)}`)
  }
  return parts.join(' · ') || null
}

/** How a saved run was made: the settings that matter at a glance, and its whole run.json. */
export function RunSettings({ detail }: { detail: SavedRunDetail }) {
  const meta = detail.meta
  const s = detail.summary
  const ccy = savedRunSummary(detail).currency
  const commission = num(meta.commission_rate)
  const params = objectCell(meta.params)
  const best = objectCell(meta.best_params)
  const objective = str(meta.objective_label) ?? str(meta.objective)
  const items: [string, ReactNode][] = [
    ['Kind', SAVED_KIND_LABEL[s.kind]],
    ['Strategy', s.strategy],
    ['Parameters', s.kind === 'backtest' && Object.keys(params).length ? paramsText(params) : null],
    ['Best parameters', Object.keys(best).length ? paramsText(best) : null],
    ['Method', s.kind === 'walk_forward' ? `walk-forward, ${fmtNum(num(meta.n_splits))} windows, ${fmtPct((num(meta.train_pct) ?? NaN) * 100, 0, false)} train` : null],
    ['Objective', s.kind === 'grid_search' || s.kind === 'walk_forward' ? objective : null],
    ['Market', str(meta.market)],
    ['Universe / symbol', s.universe],
    ['Symbols', symbolsText(meta.symbols)],
    ['Timeframe', str(meta.timeframe)],
    ['Period', s.start || s.end ? periodText(s) : null],
    ['Data up to', str(meta.data_cutoff) ? fmtDateTime(str(meta.data_cutoff)) : null],
    ['Initial capital', num(meta.initial_capital) !== null ? fmtMoney(num(meta.initial_capital), ccy) : null],
    ['Commission', commission !== null ? `${fmtPct(commission * 100, 4, false)} per side` : null],
    ['Slippage', num(meta.slippage_bps) !== null ? `${fmtNum(num(meta.slippage_bps), 1)} bps` : null],
    ['Risk & sizing', riskText(meta.risk)],
    ['Saved by', s.source === 'api' ? 'the dashboard (a job)' : s.source || null],
    ['Code', s.git_commit ? <span className="font-mono" title={s.git_commit}>{s.git_commit.slice(0, 10)}</span> : null],
  ]
  const [open, setOpen] = useState(false)
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>How it was run</CardTitle>
          <CardDescription>Enough to run it again; the whole manifest (run.json) is below.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
          {items
            .filter(([, v]) => v !== null && v !== undefined && v !== '')
            .map(([label, value]) => (
              <div key={label} className="min-w-0">
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
        </dl>
        <Collapsible open={open} onOpenChange={setOpen} className="rounded-md border">
          <CollapsibleTrigger className="flex w-full cursor-pointer items-center gap-1.5 px-3 py-2 text-left text-xs font-medium hover:bg-accent/60">
            <ChevronRightIcon aria-hidden className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
            run.json
          </CollapsibleTrigger>
          <CollapsibleContent className="border-t px-1 py-2 font-mono text-[12px]">
            <JsonTree data={meta} />
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}
