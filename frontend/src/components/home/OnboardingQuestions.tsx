'use client'

import { cn } from '@/lib/utils'
import { Card, CardTitle, CardContent } from '@/components/ui/Card'
import { Tag } from '@/components/ui/Tag'
import { useI18nStore } from '@/stores/i18nStore'

interface OnboardingItem {
  question: string
  category: string
  model_names?: string[]
  model_name?: string
}

interface OnboardingQuestionsProps {
  questions: OnboardingItem[]
  loading?: boolean
  onSelect: (question: string) => void
  className?: string
}

export function OnboardingQuestions({
  questions,
  loading,
  onSelect,
  className,
}: OnboardingQuestionsProps) {
  const t = useI18nStore((s) => s.t)
  if (loading) {
    return (
      <Card className={cn(className)}>
        <CardTitle className="mb-3">{t('onboarding.title', 'Get Started')}</CardTitle>
        <CardContent className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 animate-pulse rounded-md bg-gray-100 dark:bg-gray-800" />
          ))}
        </CardContent>
      </Card>
    )
  }

  if (questions.length === 0) return null

  return (
    <Card className={cn(className)}>
      <CardTitle className="mb-3">{t('onboarding.subtitle', 'Get Started with Your Data')}</CardTitle>
      <CardContent className="space-y-2">
        {questions.map((q, i) => {
            const displayName = q.model_names?.length ? q.model_names.join(', ') : q.model_name || ''
            return (
              <button
                key={i}
                onClick={() => onSelect(q.question)}
                className="w-full rounded-md px-3 py-2.5 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800"
              >
                <span className="line-clamp-1">{q.question}</span>
                <div className="mt-1 flex items-center gap-1.5">
                  {q.category && <Tag variant="info" size="sm">{q.category}</Tag>}
                  {displayName && <Tag variant="default" size="sm">{displayName}</Tag>}
                </div>
              </button>
            )
          })}
      </CardContent>
    </Card>
  )
}
