'use client'

import { cn } from '@/lib/utils'
import { Tag } from '@/components/ui/Tag'
import { Skeleton } from '@/components/ui/Skeleton'
import { useI18nStore } from '@/stores/i18nStore'

interface Recommendation {
  id: number
  question: string
  type: string
  source: string
  score: number
  llm_explanation?: string
  model_names?: string[]
}

interface RecommendedQuestionsProps {
  recommendations: Recommendation[]
  loading?: boolean
  onSelect: (question: string) => void
  className?: string
}

const TYPE_CONFIG: Record<string, { color: string; icon: string }> = {
  trend:         { color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300', icon: '↗' },
  ranking:       { color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300', icon: '↑↓' },
  comparison:    { color: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300', icon: '⇄' },
  distribution:  { color: 'bg-lime-100 text-lime-700 dark:bg-lime-900/30 dark:text-lime-300', icon: '%' },
  aggregation:   { color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300', icon: 'Σ' },
  anomaly:       { color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300', icon: '⚡' },
  contribution:  { color: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300', icon: '◐' },
  correlation:   { color: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300', icon: '⟷' },
  drilldown:     { color: 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300', icon: '▾' },
  relationship:  { color: 'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300', icon: '⟐' },
  trending:      { color: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300', icon: '🔥' },
  expand:        { color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300', icon: '⊕' },
  compare:       { color: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300', icon: '⇄' },
  follow_up:     { color: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300', icon: '↩' },
  catalog:       { color: 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/30 dark:text-fuchsia-300', icon: '★' },
  llm:           { color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300', icon: '✦' },
}

export function RecommendedQuestions({
  recommendations,
  loading,
  onSelect,
  className,
}: RecommendedQuestionsProps) {
  const t = useI18nStore((s) => s.t)

const sourceLabels: Record<string, string> = {
    schema: t('recommendedQuestions.sourceSchema', 'Schema'),
    session: t('recommendedQuestions.sourceSession', 'Session'),
    project: t('recommendedQuestions.sourceProject', 'Project'),
    global: t('recommendedQuestions.sourceGlobal', 'Global'),
    preference: t('recommendedQuestions.sourcePreference', 'Preference'),
    catalog: t('recommendedQuestions.sourceCatalog', 'Catalog'),
    llm: t('recommendedQuestions.sourceLLM', 'AI'),
  }

  const typeLabels: Record<string, string> = {
    trend: t('recommendedQuestions.typeTrend', 'Trend'),
    ranking: t('recommendedQuestions.typeRanking', 'Ranking'),
    comparison: t('recommendedQuestions.typeCompare', 'Compare'),
    distribution: t('recommendedQuestions.typeDistribution', 'Distribution'),
    aggregation: t('recommendedQuestions.typeAggregation', 'Aggregation'),
    anomaly: t('recommendedQuestions.typeAnomaly', 'Anomaly'),
    contribution: t('recommendedQuestions.typeContribution', 'Contribution'),
    correlation: t('recommendedQuestions.typeCorrelation', 'Correlation'),
    drilldown: t('recommendedQuestions.typeDrilldown', 'Drill Down'),
    relationship: t('recommendedQuestions.typeRelationship', 'Relationship'),
    trending: t('recommendedQuestions.typeTrending', 'Trending'),
    expand: t('recommendedQuestions.typeExpand', 'Explore'),
    follow_up: t('recommendedQuestions.typeFollowUp', 'Follow Up'),
    compare: t('recommendedQuestions.typeCompare', 'Compare'),
    catalog: t('recommendedQuestions.sourceCatalog', 'Catalog'),
    llm: t('recommendedQuestions.typeInsight', 'Insight'),
  }

  if (loading) {
    return (
      <div className={cn('space-y-2', className)}>
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-3/4" />
      </div>
    )
  }

  if (recommendations.length === 0) return null

  return (
    <div className={cn('w-full max-w-xl', className)}>
      <p className="mb-3 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
        {t('recommendedQuestions.title', 'Recommended Questions')}
      </p>
      <div className="grid grid-cols-1 gap-2">
        {recommendations.map((rec, idx) => {
          const typeKey = rec.type || 'aggregation'
          const config = TYPE_CONFIG[typeKey] ?? TYPE_CONFIG.aggregation!
          const label = typeLabels[typeKey] || typeLabels.aggregation || typeKey
          return (
            <button
              key={`${rec.id}-${idx}`}
              onClick={() => onSelect(rec.question)}
              className="group flex w-full items-start gap-2.5 rounded-lg border border-gray-150 bg-white px-3.5 py-2.5 text-left transition-all hover:border-blue-200 hover:bg-blue-50/40 hover:shadow-sm dark:border-gray-700 dark:bg-gray-850 dark:hover:border-blue-800 dark:hover:bg-blue-950/30"
            >
              <span className={cn('mt-0.5 inline-flex h-5 min-w-[20px] items-center justify-center rounded px-1 text-[10px] font-semibold', config.color)}>
                {config.icon}
              </span>
              <div className="min-w-0 flex-1">
                <span className="line-clamp-2 text-sm leading-snug text-gray-800 group-hover:text-blue-700 dark:text-gray-200 dark:group-hover:text-blue-300">
                  {rec.question}
                </span>
                <div className="mt-1 flex items-center gap-1.5">
                  <span className={cn('inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium', config.color)}>
                    {label}
                  </span>
                  <Tag variant="default" size="sm">
                    {sourceLabels[rec.source] || rec.source}
                  </Tag>
                </div>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}