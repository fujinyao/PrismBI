'use client'

import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/Skeleton'
import { Tag } from '@/components/ui/Tag'
import { useRecommendations } from '@/hooks/useRecommendations'
import { useI18nStore } from '@/stores/i18nStore'

interface RecommendedQuestionsProps {
  threadId?: string
  onSelect: (q: string) => void
  className?: string
}

const categoryVariants: Record<string, 'info' | 'success' | 'warning' | 'default'> = {
  explore: 'info',
  trending: 'warning',
  drilldown: 'success',
  compare: 'default',
}

export function RecommendedQuestions({ threadId, onSelect, className }: RecommendedQuestionsProps) {
  const t = useI18nStore((s) => s.t)
  const { recommendations, isLoading, isError } = useRecommendations(
    threadId ? { context: threadId } : undefined,
  )

  if (isLoading) {
    return (
      <div className={cn('grid grid-cols-1 gap-3 md:grid-cols-3', className)}>
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="rounded-lg border border-gray-200 p-4 dark:border-gray-700"
          >
            <Skeleton className="mb-2 h-4 w-3/4" />
            <Skeleton className="mb-3 h-3 w-1/3" />
            <Skeleton className="mb-2 h-3 w-1/4" />
            <Skeleton className="h-2 w-full" />
          </div>
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className={cn('rounded-lg border border-error-200 bg-error-50 p-4 text-sm text-error-700 dark:border-error-900/30 dark:bg-error-900/30 dark:text-error-400', className)}>
        {t('recommendedQuestions.loadError', 'Failed to load recommendations.')}
      </div>
    )
  }

  if (recommendations.length === 0) {
    return (
      <div className={cn('py-8 text-center text-sm text-gray-500 dark:text-gray-400', className)}>
        {t('recommendedQuestions.noResults', 'No recommendations available yet.')}
      </div>
    )
  }

  return (
    <div className={cn('grid grid-cols-1 gap-3 md:grid-cols-3', className)}>
      {recommendations.map((rec, idx) => (
        <button
          key={`${rec.id}-${idx}`}
          onClick={() => onSelect(rec.question)}
          className="group rounded-lg border border-gray-200 p-4 text-left transition-all hover:border-primary-300 hover:shadow-sm dark:border-gray-700 dark:hover:border-primary-700"
        >
          <p className="line-clamp-2 text-sm font-medium text-gray-900 dark:text-gray-100">
            {rec.question}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {rec.type && (
              <Tag variant={categoryVariants[rec.type] ?? 'default'} size="sm">
                {rec.type}
              </Tag>
            )}
            {rec.source && (
              <Tag variant="default" size="sm">
                {rec.source}
              </Tag>
            )}
            {rec.model_names?.map((m) => (
              <Tag key={m} variant="default" size="sm">
                {m}
              </Tag>
            ))}
          </div>
          <div className="mt-3">
            <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
              <span>{t('recommendedQuestions.confidence', 'Confidence')}</span>
              <span>{Math.round(rec.score * 100)}%</span>
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.round(rec.score * 100)}%` }}
              />
            </div>
          </div>
        </button>
      ))}
    </div>
  )
}
