'use client'

import { useI18nStore } from '@/stores/i18nStore'

interface CompactRecommendationProps {
  questions: string[]
  onSelect: (question: string) => void
}

export function CompactRecommendation({ questions, onSelect }: CompactRecommendationProps) {
  const t = useI18nStore((s) => s.t)

  if (!questions.length) return null

  return (
    <div className="space-y-2">
      <h3 className="px-1 text-xs font-medium text-gray-500 dark:text-gray-400">
        {t('recommendation.suggested', 'Suggested')}
      </h3>
      <div className="flex flex-col gap-2">
        {questions.slice(0, 5).map((q, i) => (
          <button
            key={i}
            onClick={() => onSelect(q)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            <span className="line-clamp-2">{q}</span>
          </button>
        ))}
      </div>
    </div>
  )
}