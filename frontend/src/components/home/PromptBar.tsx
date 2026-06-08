'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'

interface PromptBarProps {
  onSubmit: (question: string, previewRowLimit: number) => void | Promise<void>
  placeholder?: string
  disabled?: boolean
  className?: string
  previewRowLimit?: number
}

export function PromptBar({
  onSubmit,
  placeholder,
  disabled,
  className,
  previewRowLimit = 20,
}: PromptBarProps) {
  const t = useI18nStore((s) => s.t)
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const composing = useRef(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const autoResize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [])

  useEffect(() => { autoResize() }, [value, autoResize])

  const handleSubmit = useCallback(async () => {
    const trimmed = value.trim()
    if (!trimmed || submitting || disabled) return
    setSubmitting(true)
    try {
      await onSubmit(trimmed, previewRowLimit)
      setValue('')
      if (textareaRef.current) textareaRef.current.style.height = 'auto'
    } finally {
      setSubmitting(false)
    }
  }, [value, submitting, disabled, onSubmit, previewRowLimit])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !(e.nativeEvent as KeyboardEvent).isComposing && !composing.current) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div
      className={cn(
        'border-t border-gray-200 bg-white px-5 py-4 dark:border-gray-700 dark:bg-gray-900',
        className,
      )}
    >
      <div className="mx-auto w-full max-w-[min(100%,84rem)]">
        <div className="relative rounded-xl border border-gray-300 bg-white focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:focus-within:border-primary-500">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={(e) => { composing.current = false; setValue((e.target as HTMLTextAreaElement).value) }}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? t('common.loading', 'Loading...') : (placeholder || t('home.askPlaceholder', 'What would you like to know?'))}
            disabled={disabled || submitting}
            rows={1}
            className={cn(
              'max-h-40 min-h-[44px] w-full resize-none rounded-xl px-4 py-3 pr-14 text-sm',
              'bg-transparent text-gray-900 dark:text-gray-100',
              'placeholder:text-gray-400 dark:placeholder:text-gray-500',
              'focus:outline-none',
              (disabled || submitting) && 'cursor-not-allowed opacity-50',
            )}
          />
          <Button
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            loading={submitting}
            disabled={disabled || !value.trim()}
            className="absolute bottom-2.5 right-2.5"
          >
            {t('chat.send', 'Send')}
          </Button>
        </div>
      </div>
    </div>
  )
}
