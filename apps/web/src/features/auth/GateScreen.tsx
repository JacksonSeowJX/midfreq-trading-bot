import { LoaderCircleIcon, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

/** The page the sign-in gate shows instead of the app: one centred card. */
export function GateScreen({
  title,
  icon: Icon,
  busy = false,
  actions,
  children,
}: {
  title: string
  icon?: LucideIcon
  busy?: boolean
  actions?: ReactNode
  children?: ReactNode
}) {
  return (
    <main className="grid h-full min-h-0 place-items-center overflow-y-auto bg-background p-4">
      <section
        aria-busy={busy || undefined}
        aria-labelledby="gate-title"
        className="w-full max-w-sm rounded-lg border bg-card px-6 py-7 text-center shadow-[0_1px_2px_rgb(16_24_40/0.04)]"
      >
        <span
          aria-hidden
          className="mx-auto mb-4 grid size-9 place-items-center rounded-md bg-brand text-[13px] font-bold tracking-tight text-white"
        >
          MF
        </span>
        {busy ? (
          <LoaderCircleIcon aria-hidden className="mx-auto mb-2 size-5 animate-spin text-muted-foreground" />
        ) : Icon ? (
          <Icon aria-hidden className="mx-auto mb-2 size-5 text-muted-foreground" />
        ) : null}
        <h1 id="gate-title" className="text-base font-semibold">
          {title}
        </h1>
        {children ? <div className="mt-2 text-[13px] leading-relaxed text-muted-foreground">{children}</div> : null}
        {actions ? <div className="mt-5 flex flex-wrap justify-center gap-2">{actions}</div> : null}
      </section>
    </main>
  )
}
