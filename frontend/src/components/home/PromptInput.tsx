'use client'

import { useState, useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'

interface PromptInputProps {
  onSubmit: (question: string) => void
  state?: 'idle' | 'submitting' | 'disabled' | 'error'
  placeholder?: string
  errorMessage?: string
  className?: string
}

export function PromptInput({
  onSubmit,
  state = 'idle',
  placeholder,
  errorMessage,
  className,
}: PromptInputProps) {
  const t = useI18nStore((s) => s.t)
  const [value, setValue] = useState('')
  const resolvedPlaceholder = placeholder || t('home.askPlaceholder', 'Ask a question about your data...')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const composing = useRef(false)

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim()
    if (!trimmed || state === 'submitting' || state === 'disabled') return
    onSubmit(trimmed)
  }, [value, state, onSubmit])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !(e.nativeEvent as KeyboardEvent).isComposing && !composing.current) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const disabled = state === 'submitting' || state === 'disabled'

  return (
    <div className={cn('space-y-2', className)}>
      <div
        className={cn(
          'flex items-end gap-2 rounded-lg border bg-white p-3 transition-colors dark:bg-gray-800',
          state === 'error'
            ? 'border-error'
            : 'border-gray-300 focus-within:border-primary dark:border-gray-600 dark:focus-within:border-primary',
          disabled && 'cursor-not-allowed opacity-60',
        )}
      >
        <textarea
          ref={textareaRef}
          onInput={(e) => { if (!composing.current && !(e.nativeEvent as InputEvent).isComposing) setValue((e.target as HTMLTextAreaElement).value) }}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={(e) => { composing.current = false; setValue((e.target as HTMLTextAreaElement).value) }}
          onKeyDown={handleKeyDown}
          placeholder={state === 'disabled' ? t('common.loading', 'Loading...') : resolvedPlaceholder}
          disabled={disabled}
          rows={2}
          className={cn(
            'flex-1 resize-none bg-transparent text-sm text-gray-900 placeholder-gray-400 focus:outline-none dark:text-gray-100 dark:placeholder-gray-500',
          )}
        />
        <Button
          variant="primary"
          size="md"
          onClick={handleSubmit}
          loading={state === 'submitting'}
          disabled={disabled || !value.trim()}
          className="shrink-0"
        >
          {state === 'submitting' ? '' : t('chat.send', 'Send')}
        </Button>
      </div>
      {state === 'error' && errorMessage && (
        <p className="text-sm text-error">{errorMessage}</p>
      )}
    </div>
  )
}
