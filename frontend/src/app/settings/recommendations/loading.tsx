'use client'

import { useI18nStore } from '@/stores/i18nStore'
import { SkeletonTable } from '@/components/ui/Skeleton'

export default function RecommendationsLoading() {
  const t = useI18nStore((s) => s.t)

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-primary" />
        <p className="text-sm text-gray-500 dark:text-gray-400">{t('common.loading', 'Loading...')}</p>
      </div>
      <div className="w-full max-w-4xl">
        <SkeletonTable rows={5} cols={4} />
      </div>
    </div>
  )
}