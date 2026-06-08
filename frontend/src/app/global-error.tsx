'use client'

import { useEffect } from 'react'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  const t = useI18nStore((s) => s.t)

  useEffect(() => {
    console.error('Global error:', error)
  }, [error])

  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 px-6 text-center dark:bg-gray-900">
          <svg
            className="mb-4 h-16 w-16 text-error"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
            />
          </svg>
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
            {t('error.critical', 'Critical Error')}
          </h1>
          <p className="mt-2 max-w-md text-sm text-gray-500 dark:text-gray-400">
            {t('error.criticalDesc', 'The application encountered a critical error. Please refresh the page.')}
          </p>
          <div className="mt-6 flex gap-3">
            <Button variant="primary" size="md" onClick={reset}>
              {t('common.tryAgain', 'Try again')}
            </Button>
            <Button variant="secondary" size="md" onClick={() => window.location.href = '/'}>
              {t('error.goHome', 'Go to Home')}
            </Button>
          </div>
        </div>
      </body>
    </html>
  )
}