'use client'

import { useEffect } from 'react'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  const t = useI18nStore((s) => s.t)

  useEffect(() => {
    console.error('Route error:', error)
  }, [error])

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 py-12 text-center">
      <svg
        className="mb-4 h-12 w-12 text-error"
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
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        {t('error.title', 'Something went wrong')}
      </h2>
      <p className="mt-2 max-w-md text-sm text-gray-500 dark:text-gray-400">
        {error.message || t('error.unexpected', 'An unexpected error occurred.')}
      </p>
      {error.digest && (
        <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
          Digest: {error.digest}
        </p>
      )}
      <div className="mt-6 flex gap-3">
        <Button variant="primary" size="md" onClick={reset}>
          {t('common.tryAgain', 'Try again')}
        </Button>
        <Button variant="secondary" size="md" onClick={() => window.location.href = '/'}>
          {t('error.goHome', 'Go to Home')}
        </Button>
      </div>
    </div>
  )
}