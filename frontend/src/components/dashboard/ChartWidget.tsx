'use client'

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { ChartContainer } from '@/components/chart/ChartContainer'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface ChartWidgetProps {
  title: string
  chartType: string
  spec: any
  data: any[]
  loading?: boolean
  error?: string
  onRefresh?: () => void
}

export function ChartWidget({
  title,
  chartType,
  spec,
  data,
  loading = false,
  error,
  onRefresh,
}: ChartWidgetProps) {
  const t = useI18nStore((s) => s.t)
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <SkeletonCard />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="relative h-full">
      <CardHeader>
        <div className="flex items-center gap-2 min-w-0">
          <div className="resize-handle cursor-se-resize text-gray-400">
            <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M22 22H20V20H22V22ZM22 18H18V22H22V18ZM18 18H14V22H18V18ZM14 14H10V18H14V14Z" />
            </svg>
          </div>
          <CardTitle className="truncate">{title}</CardTitle>
          <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium text-gray-500 dark:bg-gray-700 dark:text-gray-400">
            {chartType}
          </span>
        </div>
        {onRefresh && (
          <Button variant="ghost" size="sm" onClick={onRefresh} aria-label={t('chartWidget.refresh', 'Refresh chart')}>
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
          </Button>
        )}
      </CardHeader>
      <CardContent className={cn('h-[300px]', error && 'flex items-center justify-center')}>
        {error ? (
          <div className="flex flex-col items-center gap-2 text-center">
            <svg className="h-8 w-8 text-error" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"
              />
            </svg>
            <p className="text-sm text-error-600 dark:text-error-400">{error}</p>
          </div>
        ) : (
          <ChartContainer spec={spec} data={data} />
        )}
      </CardContent>
    </Card>
  )
}
