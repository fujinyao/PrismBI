'use client'

import { cn } from '@/lib/utils'

interface FormProps extends React.FormHTMLAttributes<HTMLFormElement> {
  onSubmit: (e: React.FormEvent) => void
  loading?: boolean
}

export function Form({ onSubmit, loading, children, className, ...props }: FormProps) {
  return (
    <form
      onSubmit={(e) => {
        if (loading) return
        onSubmit(e)
      }}
      className={cn('space-y-4', className)}
      {...props}
    >
      <fieldset disabled={loading} className="space-y-4">
        {children}
      </fieldset>
    </form>
  )
}

export function FormSection({ title, description, children }: { title?: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      {title && (
        <div>
          <h3 className="text-base font-medium text-gray-900 dark:text-gray-100">{title}</h3>
          {description && (
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{description}</p>
          )}
        </div>
      )}
      {children}
    </div>
  )
}

export function FormActions({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('flex items-center justify-end gap-3 pt-4', className)}>
      {children}
    </div>
  )
}
