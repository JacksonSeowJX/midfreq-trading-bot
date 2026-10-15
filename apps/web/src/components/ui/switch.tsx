import { Switch as SwitchPrimitive } from 'radix-ui'
import { useId, type ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'peer inline-flex h-4 w-7 shrink-0 cursor-pointer items-center rounded-full border border-transparent bg-border transition-colors data-[state=checked]:bg-brand disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-3 translate-x-0.5 rounded-full bg-card shadow-sm transition-transform data-[state=checked]:translate-x-[13px]" />
    </SwitchPrimitive.Root>
  )
}

/** A switch with its label, the common case. */
export function LabeledSwitch({
  label,
  checked,
  onCheckedChange,
  className,
}: {
  label: string
  checked: boolean
  onCheckedChange: (v: boolean) => void
  className?: string
}) {
  const id = useId()
  return (
    <div className={cn('inline-flex items-center gap-2', className)}>
      <Switch id={id} checked={checked} onCheckedChange={onCheckedChange} />
      <label htmlFor={id} className="cursor-pointer select-none text-xs text-muted-foreground">
        {label}
      </label>
    </div>
  )
}
