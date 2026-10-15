import type { ReactNode } from 'react'

/** A form field's id from the form's own id and the request path it fills ("risk.qty"). */
export function fieldId(formId: string, key: string): string {
  return `${formId}-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`
}

/** What a control's aria-describedby should point at: its error, else its hint. */
export function describedBy(id: string, error?: string, hint?: ReactNode): string | undefined {
  if (error) return `${id}-error`
  return hint ? `${id}-hint` : undefined
}
