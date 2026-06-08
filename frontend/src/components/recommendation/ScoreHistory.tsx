'use client'

import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/Skeleton'
import { Tag } from '@/components/ui/Tag'
import { useI18nStore } from '@/stores/i18nStore'

interface ScoreEntry {
  id: string
  date: string
  score: number
  source: string
  reason: string
}

interface ScoreHistoryProps {
  scores: ScoreEntry[]
  loading: boolean
  className?: string
}

export function ScoreHistory({ scores, loading, className }: ScoreHistoryProps) {
  const t = useI18nStore((s) => s.t)
  const maxScore = useMemo(
    () => Math.max(...scores.map((s) => s.score), 1),
    [scores],
  )

  if (loading) {
    return (
      <div className={cn('space-y-3', className)}>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-3/4" />
      </div>
    )
  }

  if (scores.length === 0) {
    return (
      <div
        className={cn(
          'py-8 text-center text-sm text-gray-500 dark:text-gray-400',
          className,
        )}
      >
        {t('scoreHistory.noResults', 'No score history yet.')}
      </div>
    )
  }

  return (
    <div className={cn('space-y-4', className)}>
      {/* Vega-Lite-style bar chart */}
      <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
          {t('scoreHistory.scoreTrend', 'Score Trend')}
        </p>
        <div className="flex items-end gap-1" style={{ height: 120 }}>
          {scores.slice(-14).map((entry) => {
            const pct = (entry.score / maxScore) * 100
            return (
              <div
                key={entry.id}
                className="group relative flex flex-1 flex-col items-center justify-end"
                style={{ height: '100%' }}
              >
                <div
                  className="w-full rounded-t bg-primary transition-all group-hover:bg-primary-600"
                  style={{ height: `${pct}%`, minHeight: 4 }}
                  title={`${entry.date}: ${entry.score}`}
                />
                <div className="mt-1 hidden text-[10px] text-gray-400 group-hover:block">
                  {entry.score}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Detail table */}
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>
              {['Date', 'Score', 'Source', 'Reason'].map((h, i) => {
              const labels: Record<string, string> = {
                Date: t('scoreHistory.date', 'Date'),
                Score: t('scoreHistory.score', 'Score'),
                Source: t('scoreHistory.source', 'Source'),
                Reason: t('scoreHistory.reason', 'Reason'),
              }
              return (
                <th
                  key={i}
                  className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400"
                >
                  {labels[h] ?? h}
                </th>
              )
            })}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-900">
            {scores.map((entry) => (
              <tr
                key={entry.id}
                className="transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                <td className="whitespace-nowrap px-4 py-2.5 text-sm text-gray-600 dark:text-gray-400">
                  {entry.date}
                </td>
                <td className="whitespace-nowrap px-4 py-2.5">
                  <span className="inline-flex items-center gap-1 text-sm font-medium text-gray-900 dark:text-gray-100">
                    {entry.score}
                    <span className="text-xs text-gray-400">
                      / {maxScore}
                    </span>
                  </span>
                </td>
                <td className="whitespace-nowrap px-4 py-2.5">
                  <Tag variant="info" size="sm">
                    {entry.source}
                  </Tag>
                </td>
                <td className="whitespace-nowrap px-4 py-2.5 text-sm text-gray-600 dark:text-gray-400">
                  {entry.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
