'use client'

import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface ErrorToastProps {
  message: string
  onDismiss?: () => void
  onClose?: () => void
  onRetry?: () => void
  type?: 'error' | 'warning' | 'info'
}

const typeStyles = {
  error:
    'bg-error-50 border-error-200 text-error-800 dark:bg-error-900/40 dark:border-error-700 dark:text-error-300',
  warning:
    'bg-warning-50 border-warning-200 text-warning-800 dark:bg-warning-900/40 dark:border-warning-700 dark:text-warning-300',
  info:
    'bg-primary-50 border-primary-200 text-primary-800 dark:bg-primary-900/40 dark:border-primary-700 dark:text-primary-300',
}

const typeIcons = {
  error: '\u2717',
  warning: '\u26A0',
  info: '\u2139',
}

export function ErrorToast({ message, onClose, onRetry, type = 'error' }: ErrorToastProps) {
  const t = useI18nStore((s) => s.t)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    setVisible(true)
    const timer =
      type === 'error'
        ? undefined
        : setTimeout(() => {
            setVisible(false)
            onClose?.()
          }, type === 'warning' ? 5000 : 3000)
    return () => timer && clearTimeout(timer)
  }, [message, type, onClose])

  if (!visible) return null

  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-3 rounded-lg border px-4 py-3 shadow-lg',
        'animate-in slide-in-from-right-full fade-in duration-300',
        typeStyles[type],
      )}
    >
      <span className="mt-0.5 text-lg font-bold">{typeIcons[type]}</span>
      <p className="flex-1 text-sm">{message}</p>
      <div className="flex items-center gap-2">
        {onRetry && (
          <button
            onClick={() => { setVisible(false); onRetry() }}
            className="text-xs font-medium underline hover:no-underline"
          >
            {t('common.retry', 'Retry')}
          </button>
        )}
        {onClose && (
          <button
            onClick={() => { setVisible(false); onClose() }}
            className="text-current opacity-60 hover:opacity-100 transition-opacity"
            aria-label={t('common.dismiss', 'Dismiss')}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>
    </div>
  )
}
