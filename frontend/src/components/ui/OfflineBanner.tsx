'use client'

import { useOfflineState } from '@/hooks/useOnlineStatus'
import { useI18nStore } from '@/stores/i18nStore'

export function OfflineBanner() {
  const { isOffline } = useOfflineState()
  const t = useI18nStore((s) => s.t)

  if (!isOffline) return null

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="fixed inset-x-0 top-0 z-[100] flex items-center justify-center bg-amber-500 px-4 py-2 text-sm font-medium text-white motion-safe:transition-all motion-safe:duration-300 dark:bg-amber-600"
    >
      <svg className="mr-2 h-4 w-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 5.636a9 9 0 010 12.728m-3.536-3.536a4 4 0 010-5.656m-7.072 9.192a9 9 0 010-12.728m3.536 3.536a4 4 0 010 5.656" />
      </svg>
      <span>{t('error.offline', 'You are offline')}</span>
      <span className="ml-2 text-xs opacity-80">
        {t('error.offlineDesc', 'Some features may not be available. Data will sync when you reconnect.')}
      </span>
    </div>
  )
}