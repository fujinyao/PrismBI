'use client'

import { forwardRef, useId, useLayoutEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'defaultValue'> {
  label?: string
  error?: string
  hint?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, hint, className, id, onChange, value, ...props }, forwardedRef) => {
    const generatedId = useId()
    const inputId = id || generatedId
    const innerRef = useRef<HTMLInputElement>(null)
    const ref = (forwardedRef || innerRef) as React.RefObject<HTMLInputElement>
    const composing = useRef(false)

    // Sync external value changes (e.g. form reset) — but NOT during IME composition
    // Runs before paint so the user never sees an empty input on first render
    useLayoutEffect(() => {
      const el = ref.current
      if (el && !composing.current) {
        const strVal = value == null ? '' : String(value)
        if (el.value !== strVal) {
          el.value = strVal
        }
      }
    })

    const handleInput = (e: React.FormEvent<HTMLInputElement>) => {
      if (composing.current) return
      if ((e.nativeEvent as InputEvent).isComposing) return
      onChange?.(e as unknown as React.ChangeEvent<HTMLInputElement>)
    }

    return (
      <div className="space-y-1">
        {label && (
          <label
            htmlFor={inputId}
            className="block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={cn(
            'block w-full rounded-md border px-3 py-2 text-sm transition-colors',
            'bg-white dark:bg-gray-800',
            'text-gray-900 dark:text-gray-100',
            'placeholder:text-gray-400 dark:placeholder:text-gray-500',
            'focus:outline-none focus:ring-2 focus:ring-primary-300',
            error
              ? 'border-error focus:border-error focus:ring-error-200'
              : 'border-gray-300 dark:border-gray-600',
            props.disabled && 'cursor-not-allowed opacity-50 bg-gray-50 dark:bg-gray-900',
            className,
          )}
          aria-invalid={error ? 'true' : 'false'}
          aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
          {...props}
          onInput={handleInput}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={(e) => {
            composing.current = false
            onChange?.(e as unknown as React.ChangeEvent<HTMLInputElement>)
          }}
        />
        {error && (
          <p id={`${inputId}-error`} className="text-sm text-error" role="alert">
            {error}
          </p>
        )}
        {hint && !error && (
          <p id={`${inputId}-hint`} className="text-sm text-gray-500 dark:text-gray-400">
            {hint}
          </p>
        )}
      </div>
    )
  },
)

Input.displayName = 'Input'
