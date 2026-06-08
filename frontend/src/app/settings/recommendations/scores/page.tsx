'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { recommendationsApi } from '@/lib/api'
import { formatNumber as formatLocaleNumber, useI18nStore } from '@/stores/i18nStore'
import { useProjectStore } from '@/stores/projectStore'
import { ScoreHistory } from '@/components/recommendation/ScoreHistory'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'

export default function RecommendationScoresPage() {
  const t = useI18nStore((s) => s.t)
  const locale = useI18nStore((s) => s.locale)
  const currentProject = useProjectStore((s) => s.currentProject)
  const [error, setError] = useState<string | null>(null)
  const projectId = currentProject?.id

  const {
    data: ratings,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['recommendation-ratings', projectId],
    queryFn: () => recommendationsApi.ratings({ project_id: projectId }),
  })

  const {
    data: statistics,
    isLoading: isStatisticsLoading,
    isError: isStatisticsError,
    refetch: refetchStatistics,
  } = useQuery({
    queryKey: ['recommendation-statistics', projectId],
    queryFn: () => recommendationsApi.statistics({ project_id: projectId }),
    enabled: Boolean(projectId),
  })

  const routeSignals = statistics?.route_signals
  const routeKindEntries = Object.entries(routeSignals?.route_kind_counts ?? {}).sort(([, left], [, right]) => right - left)
  const routeKindTotal = routeKindEntries.reduce((sum, [, count]) => sum + count, 0)

  const formatPercent = (value: number): string => `${formatLocaleNumber(value * 100, locale, { maximumFractionDigits: 1 })}%`

  const formatCount = (value: number): string => formatLocaleNumber(value, locale, { maximumFractionDigits: 0 })

  const formatDecimal = (value: number, digits = 2): string => formatLocaleNumber(value, locale, { maximumFractionDigits: digits })

  const dominantRouteLabel = (routeKind: string): string => {
    if (!routeKind) return t('recommendation.routeSignals.notAvailable', 'N/A')
    const key = `recommendation.routeSignals.routeKind.${routeKind}`
    const localized = t(key)
    return localized === key ? routeKind.replaceAll('_', ' ') : localized
  }

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <Skeleton className="mb-4 h-48 w-full" />
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="mb-2 h-12" />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('recommendation.failedToLoadScores', 'Failed to load score history')}
          onRetry={() => {
            void refetch()
            if (projectId) void refetchStatistics()
          }}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      {projectId && (
        <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800/60">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {t('recommendation.routeSignals.title', 'Route Signals')}
            </h3>
            {isStatisticsLoading && (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {t('common.loading', 'Loading...')}
              </span>
            )}
          </div>

          {isStatisticsError ? (
            <p className="text-sm text-error dark:text-error-400">
              {t('recommendation.failedToLoadRouteSignals', 'Failed to load route signals')}
            </p>
          ) : routeSignals?.available ? (
            <div>
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('recommendation.routeSignals.mixedRatio', 'Mixed Ratio')}
                  </p>
                  <p className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {formatPercent(routeSignals.mixed_ratio)}
                  </p>
                </div>

                <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('recommendation.routeSignals.dominantRoute', 'Dominant Route')}
                  </p>
                  <p className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {dominantRouteLabel(routeSignals.dominant_route_kind)}
                  </p>
                </div>

                <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('recommendation.routeSignals.sqlSuccessRate', 'SQL Success Rate')}
                  </p>
                  <p className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {formatPercent(routeSignals.sql_success_rate)}
                  </p>
                </div>

                <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('recommendation.routeSignals.avgMetadataClauses', 'Avg Metadata Clauses')}
                  </p>
                  <p className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {formatDecimal(routeSignals.avg_metadata_clause_count, 2)}
                  </p>
                </div>
              </div>

              {routeKindEntries.length > 0 && (
                <div className="mt-3 overflow-x-auto rounded-md border border-gray-200 dark:border-gray-700">
                  <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                    <thead className="bg-gray-100 dark:bg-gray-800">
                      <tr>
                        <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('recommendation.routeSignals.routeKind', 'Route Kind')}
                        </th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('recommendation.routeSignals.events', 'Events')}
                        </th>
                        <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('recommendation.routeSignals.share', 'Share')}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
                      {routeKindEntries.map(([routeKind, count]) => (
                        <tr key={routeKind}>
                          <td className="whitespace-nowrap px-3 py-2 text-sm text-gray-700 dark:text-gray-300">
                            {dominantRouteLabel(routeKind)}
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">
                            {formatCount(count)}
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">
                            {formatPercent(routeKindTotal > 0 ? count / routeKindTotal : 0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {t('recommendation.routeSignals.empty', 'No routing signal data yet. Ask more questions to populate this panel.')}
            </p>
          )}
        </div>
      )}

      {ratings?.ratings && ratings.ratings.length > 0 ? (
        <ScoreHistory scores={ratings.ratings.map((rating: any) => ({
          id: String(rating.id),
          date: rating.date ?? rating.created_at ?? '',
          score: Number(rating.score ?? rating.rating ?? 0),
          source: rating.source ?? rating.source_layer ?? rating.recommend_type ?? 'recommendation',
          reason: rating.reason ?? rating.source_question ?? rating.session_context ?? '',
        }))} loading={false} />
      ) : (
        <EmptyState message={t('recommendation.noScoreData', 'No score data yet. Ratings will appear here once users start rating recommendations.')} />
      )}
    </div>
  )
}
