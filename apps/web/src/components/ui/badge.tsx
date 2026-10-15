import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export const badgeVariants = cva(
  'inline-flex items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-px text-[11px] font-semibold leading-4 [&_svg]:size-3',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-muted text-foreground',
        outline: 'text-muted-foreground',
        gain: 'border-gain/30 bg-gain/10 text-gain',
        loss: 'border-loss/30 bg-loss/10 text-loss',
        warning: 'border-warning/30 bg-warning/10 text-warning',
        broker: 'border-broker/30 bg-broker/10 text-broker',
        bot: 'border-bot/30 bg-bot/10 text-bot',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export function Badge({ className, variant, ...props }: ComponentProps<'span'> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
}
