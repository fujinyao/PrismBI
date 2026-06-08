'use client'

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'
import { cn, generateId } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface Toast {
  id: string
  type: ToastType
  message: string
  action?: { label: string; onClick: () => void }
}

interface ToastContextValue {
  toast: (message: string, type?: ToastType, action?: Toast['action']) => void
  dismiss: (id: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

const typeStyles: Record<ToastType, string> = {
  success: 'bg-success-50 border-success text-success-700 dark:bg-success-900/30 dark:border-success-700 dark:text-success-400',
  error: 'bg-error-50 border-error text-error-700 dark:bg-error-900/30 dark:border-error-700 dark:text-error-400',
  warning: 'bg-warning-50 border-warning text-warning-700 dark:bg-warning-900/30 dark:border-warning-700 dark:text-warning-400',
  info: 'bg-primary-50 border-primary text-primary-700 dark:bg-primary-900/30 dark:border-primary-700 dark:text-primary-400',
}

const typeIcons: Record<ToastType, string> = {
  success: '\u2713',
  error: '\u2717',
  warning: '\u26A0',
  info: '\u2139',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const tr = useI18nStore((s) => s.t)
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (message: string, type: ToastType = 'info', action?: Toast['action']) => {
      const id = generateId()
      setToasts((prev) => [...prev, { id, type, message, action }])

      if (type !== 'error') {
        setTimeout(() => dismiss(id), type === 'warning' ? 5000 : type === 'info' ? 3000 : 2000)
      }
    },
    [dismiss],
  )

  return (
    <ToastContext.Provider value={{ toast, dismiss }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2" role="alert" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              'flex items-center gap-3 rounded-md border px-4 py-3 shadow-lg transition-all',
              typeStyles[t.type],
            )}
          >
            <span className="text-lg font-bold">{typeIcons[t.type]}</span>
            <p className="flex-1 text-sm">{t.message}</p>
            {t.action && (
              <button
                onClick={t.action.onClick}
                className="text-sm font-medium underline hover:no-underline"
              >
                {t.action.label}
              </button>
            )}
            <button
              onClick={() => dismiss(t.id)}
              className="ml-2 text-current opacity-60 hover:opacity-100"
              aria-label={tr('common.dismiss', 'Dismiss')}
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
