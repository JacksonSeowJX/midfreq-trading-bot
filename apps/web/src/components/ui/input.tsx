import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Input({ className, type = 'text', ...props }: ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'h-8 w-full min-w-0 rounded-md border bg-card px-2.5 text-[13px] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:outline-none disabled:opacity-50 aria-[invalid=true]:border-loss',
        className,
      )}
      {...props}
    />
  )
}
