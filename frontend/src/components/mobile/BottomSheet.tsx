'use client'

import { useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface BottomSheetProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
}

export function BottomSheet({ open, onClose, title, children }: BottomSheetProps) {
  const t = useI18nStore((s) => s.t)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.isComposing) return
      if (e.key === 'Escape') onClose()
    },
    [onClose],
  )

  useEffect(() => {
    if (open) {
      document.addEventListener('keydown', handleKeyDown)
      document.body.style.overflow = 'hidden'
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = ''
    }
  }, [open, handleKeyDown])

  return (
    <>
      <div
        className={cn(
          'fixed inset-0 z-40 bg-black transition-opacity duration-300',
          open ? 'opacity-50' : 'pointer-events-none opacity-0',
        )}
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        className={cn(
          'fixed bottom-0 left-0 right-0 z-50 mx-auto max-w-md transition-transform duration-300 ease-out',
          open ? 'translate-y-0' : 'translate-y-full',
        )}
      >
        <div className="rounded-t-2xl bg-white shadow-xl dark:bg-gray-800">
          <div className="flex cursor-grab items-center justify-center py-3 active:cursor-grabbing">
            <div className="h-1.5 w-10 rounded-full bg-gray-300 dark:bg-gray-600" />
          </div>

          {title && (
            <div className="flex items-center justify-between px-4 pb-3">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                {title}
              </h3>
              <button
                onClick={onClose}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                aria-label={t('common.close', 'Close')}
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}

          <div className="max-h-[80vh] overflow-y-auto px-4 pb-6">{children}</div>
        </div>
      </div>
    </>
  )
}
