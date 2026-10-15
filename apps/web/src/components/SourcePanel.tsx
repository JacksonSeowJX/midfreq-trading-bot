import { BotIcon, LandmarkIcon } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

export type Source = 'broker' | 'bot'

const SOURCE_COPY: Record<Source, { label: string; explain: string }> = {
  broker: {
    label: 'Broker account',
    explain:
      "The broker's own view of the account, read through its connector. Independent of the bot, so the two can be reconciled.",
  },
  bot: {
    label: 'Bot record',
    explain:
      "The trading bot's own record, read from its session logs. Independent of the broker, so the two can be reconciled.",
  },
}

export function SourceTag({ source, className }: { source: Source; className?: string }) {
  const Icon = source === 'broker' ? LandmarkIcon : BotIcon
  return (
    <Tooltip content={SOURCE_COPY[source].explain}>
      <Badge variant={source} className={cn('cursor-help uppercase tracking-wide', className)} tabIndex={0}>
        <Icon aria-hidden />
        {SOURCE_COPY[source].label}
      </Badge>
    </Tooltip>
  )
}

/**
 * A section of the Live view, labelled with where its numbers come from. Broker panels carry a
 * blue rule, the bot's record an orange one, so the two never read as one source.
 */
export function SourcePanel({
  source,
  title,
  description,
  aside,
  children,
  className,
}: {
  source: Source
  title: ReactNode
  description?: ReactNode
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  const id = useId()
  return (
    <section
      aria-labelledby={id}
      data-source={source}
      className={cn(
        'min-w-0 rounded-lg border border-t-[3px] bg-card shadow-[0_1px_2px_rgb(16_24_40/0.04)]',
        source === 'broker' ? 'border-t-broker' : 'border-t-bot',
        className,
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <SourceTag source={source} />
            <h2 id={id} className="text-sm font-semibold">
              {title}
            </h2>
          </div>
          {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
        </div>
        {aside ? <div className="text-xs text-muted-foreground">{aside}</div> : null}
      </header>
      <div className="space-y-4 p-4">{children}</div>
    </section>
  )
}
