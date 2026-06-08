'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface CompactPromptBarProps {
  onSubmit: (question: string) => void | Promise<void>
  placeholder?: string
  disabled?: boolean
  className?: string
}

export function CompactPromptBar({
  onSubmit,
  placeholder,
  disabled,
  className,
}: CompactPromptBarProps) {
  const t = useI18nStore((s) => s.t)
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = useCallback(async () => {
    const trimmed = value.trim()
    if (!trimmed || submitting || disabled) return
    setSubmitting(true)
    try {
      await onSubmit(trimmed)
      setValue('')
    } finally {
      setSubmitting(false)
    }
  }, [value, submitting, disabled, onSubmit])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className={cn('border-t border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900', className)}>
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? t('common.loading', 'Loading...') : (placeholder || t('home.askPlaceholder', 'Ask a question...'))}
          disabled={disabled || submitting}
          className={cn(
            'min-w-0 flex-1 rounded-full border border-gray-300 bg-gray-50 px-3 py-2 text-sm',
            'text-gray-900 placeholder:text-gray-400',
            'focus:border-primary-400 focus:outline-none focus:ring-1 focus:ring-primary-300',
            'dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder:text-gray-500',
            (disabled || submitting) && 'cursor-not-allowed opacity-50',
          )}
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || submitting || !value.trim()}
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-full',
            'bg-primary-500 text-white transition-colors',
            'hover:bg-primary-600 active:bg-primary-700',
            'disabled:cursor-not-allowed disabled:opacity-40',
            'dark:bg-primary-600 dark:hover:bg-primary-500',
          )}
          aria-label={t('chat.send', 'Send')}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
          </svg>
        </button>
      </div>
    </div>
  )
}