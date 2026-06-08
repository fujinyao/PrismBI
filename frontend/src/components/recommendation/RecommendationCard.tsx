'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Tag } from '@/components/ui/Tag'
import { StarRating } from '@/components/home/StarRating'
import { useI18nStore } from '@/stores/i18nStore'

interface Recommendation {
  id: string
  question: string
  confidence: number
  source: 'catalog' | 'memory' | 'hot'
  type: 'trending' | 'explore'
}

interface RecommendationCardProps {
  recommendation: Recommendation
  onRate: (id: string, rating: number) => void
  onSelect: (id: string) => void
  className?: string
}

const sourceVariants: Record<string, 'info' | 'default' | 'warning'> = {
  catalog: 'info',
  memory: 'default',
  hot: 'warning',
}

const typeVariants: Record<string, 'warning' | 'info'> = {
  trending: 'warning',
  explore: 'info',
}

export function RecommendationCard({
  recommendation,
  onRate,
  onSelect,
  className,
}: RecommendationCardProps) {
  const t = useI18nStore((s) => s.t)
  const [showRating, setShowRating] = useState(false)
  const { id, question, confidence, source, type } = recommendation
  const sourceLabels: Record<string, string> = {
    catalog: t('recommendationCard.sourceCatalog', 'Catalog'),
    memory: t('recommendationCard.sourceMemory', 'Memory'),
    hot: t('recommendationCard.sourceHot', 'Hot'),
  }

  return (
    <div
      className={cn(
        'group relative rounded-lg border border-gray-200 p-4 transition-all hover:border-primary-300 hover:shadow-sm dark:border-gray-700 dark:hover:border-primary-700',
        className,
      )}
      onMouseEnter={() => setShowRating(true)}
      onMouseLeave={() => setShowRating(false)}
    >
      <div className="flex items-start justify-between gap-2">
        <button
          onClick={() => onSelect(id)}
          className="flex-1 text-left"
        >
          <p className="line-clamp-2 text-sm font-semibold text-gray-900 dark:text-gray-100">
            {question}
          </p>
        </button>

        {showRating && (
          <div className="shrink-0 pt-0.5">
            <StarRating
              value={0}
              onChange={(rating) => onRate(id, rating)}
              size="sm"
            />
          </div>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1.5">
        <Tag variant={sourceVariants[source] ?? 'default'} size="sm">
          {sourceLabels[source] ?? source}
        </Tag>
        <Tag variant={typeVariants[type] ?? 'info'} size="sm">
          {type === 'trending' ? t('recommendationCard.typeTrending', 'trending') : t('recommendationCard.typeExplore', 'explore')}
        </Tag>
      </div>

      <div className="mt-3">
        <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
          <span>{t('recommendationCard.confidence', 'Confidence')}</span>
          <span>{Math.round(confidence * 100)}%</span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.round(confidence * 100)}%` }}
          />
        </div>
      </div>
    </div>
  )
}
