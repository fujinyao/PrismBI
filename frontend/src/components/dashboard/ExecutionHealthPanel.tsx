'use client'

import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { queryApi } from '@/lib/api'
import { getDatasourceConfig } from '@/lib/datasourceConfig'
import {
  aggregateQueryMetrics,
  evaluateRouteObservabilityAlerts,
  isProjectScopedMetricsEnabled,
  normalizeQueryMetricsRows,
  queryMetricsQueryKey,
} from '@/lib/queryMetrics'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { SkeletonRow } from '@/components/ui/Skeleton'
import { formatNumber as formatLocaleNumber, useI18nStore } from '@/stores/i18nStore'

interface ExecutionHealthPanelProps {
  projectId?: number
  className?: string
}

function formatCount(value: number, locale: string): string {
  return formatLocaleNumber(value, locale, { maximumFractionDigits: 0 })
}

function formatDecimal(value: number, locale: string, digits = 2): string {
  return formatLocaleNumber(value, locale, { maximumFractionDigits: digits })
}

function formatRatioPercent(value: number, locale: string, digits = 1): string {
  return `${formatLocaleNumber(value * 100, locale, { maximumFractionDigits: digits })}%`
}

function toSafeCount(value: unknown): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  return Math.max(0, Math.trunc(parsed))
}

function toSafeRate(value: unknown): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  if (parsed <= 0) return 0
  if (parsed >= 1) return 1
  return parsed
}

function sumCounterValues(counter: Record<string, number> | undefined): number {
  return Object.values(counter ?? {}).reduce((total, value) => {
    return total + toSafeCount(value)
  }, 0)
}

function resolveFallbackTotal(total: unknown, rate: unknown, denominator: number): number {
  const safeTotal = toSafeCount(total)
  if (safeTotal > 0) return safeTotal
  if (denominator <= 0) return 0
  return Math.round(toSafeRate(rate) * denominator)
}

function resolveFallbackRate(total: number, rate: unknown, denominator: number): number {
  if (denominator > 0 && total > 0) {
    return toSafeRate(total / denominator)
  }
  return toSafeRate(rate)
}

function successRateTone(rate: number): string {
  if (rate >= 95) return 'text-emerald-600 dark:text-emerald-400'
  if (rate >= 80) return 'text-amber-600 dark:text-amber-400'
  return 'text-error dark:text-error-400'
}

function topCounterEntries(counter: Record<string, number> | undefined, limit = 3): Array<[string, number]> {
  return Object.entries(counter ?? {})
    .map(([key, value]) => [String(key), Number.isFinite(Number(value)) ? Number(value) : 0] as [string, number])
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
}

function prettifyCounterKey(value: string): string {
  return value.replace(/_/g, ' ')
}

function alertTone(level: 'warning' | 'critical'): string {
  if (level === 'critical') {
    return 'border-error/30 bg-error/10 text-error dark:border-error-400/40 dark:bg-error-400/10 dark:text-error-200'
  }
  return 'border-amber-300/50 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200'
}

function fallbackRateTone(rate: number, warningRate: number, criticalRate: number): string {
  if (rate >= criticalRate) return 'text-error dark:text-error-300'
  if (rate >= warningRate) return 'text-amber-600 dark:text-amber-300'
  return 'text-emerald-600 dark:text-emerald-300'
}

export function ExecutionHealthPanel({ projectId, className }: ExecutionHealthPanelProps) {
  const t = useI18nStore((s) => s.t)
  const locale = useI18nStore((s) => s.locale)
  const metricsEnabled = isProjectScopedMetricsEnabled(projectId)

  const metricsQuery = useQuery({
    queryKey: queryMetricsQueryKey(projectId),
    queryFn: () => queryApi.metricsWithRouteDimensions(projectId as number),
    enabled: metricsEnabled,
    staleTime: 30000,
    refetchInterval: metricsEnabled ? 60000 : false,
  })

  const rows = useMemo(
    () => normalizeQueryMetricsRows(metricsQuery.data?.by_datasource),
    [metricsQuery.data?.by_datasource],
  )
  const totals = useMemo(() => aggregateQueryMetrics(rows), [rows])
  const routeDimensions = metricsQuery.data?.route_dimensions
  const llmHttpCircuit = metricsQuery.data?.llm_http_circuit
  const llmHttpCircuitOpenKeys = useMemo(() => {
    const value = Number(llmHttpCircuit?.open_keys)
    return Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0
  }, [llmHttpCircuit?.open_keys])
  const llmHttpCircuitTotalKeys = useMemo(() => {
    const value = Number(llmHttpCircuit?.total_keys)
    return Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0
  }, [llmHttpCircuit?.total_keys])
  const llmHttpOpenKeyEntries = useMemo(() => {
    return Object.entries(llmHttpCircuit?.keys ?? {})
      .filter(([, value]) => {
        const state = String(value?.state || '').toLowerCase()
        const remaining = Number(value?.remaining_open_seconds)
        return state === 'open' || (Number.isFinite(remaining) && remaining > 0)
      })
      .map(([key, value]) => {
        const remaining = Number(value?.remaining_open_seconds)
        const safeRemaining = Number.isFinite(remaining) ? Math.max(0, remaining) : 0
        return [key, safeRemaining] as const
      })
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 3)
  }, [llmHttpCircuit?.keys])
  const retryReasons = useMemo(
    () => topCounterEntries(routeDimensions?.generation_retry_reason, 4),
    [routeDimensions?.generation_retry_reason],
  )
  const validationIssueBuckets = useMemo(
    () => topCounterEntries(routeDimensions?.validation_issue_bucket, 4),
    [routeDimensions?.validation_issue_bucket],
  )
  const routeAlerts = useMemo(
    () => evaluateRouteObservabilityAlerts(routeDimensions, llmHttpCircuit),
    [llmHttpCircuit, routeDimensions],
  )
  const hasRouteSignals = Boolean(routeDimensions || llmHttpCircuit)
  const routeWindowSeconds = useMemo(() => {
    const seconds = Number(routeDimensions?.window_seconds)
    if (!Number.isFinite(seconds) || seconds <= 0) return 1800
    return Math.max(60, Math.round(seconds))
  }, [routeDimensions?.window_seconds])
  const routeWindowMinutes = useMemo(() => {
    return Math.max(1, Math.round(routeWindowSeconds / 60))
  }, [routeWindowSeconds])
  const routeWindowBadgeLabel = useMemo(() => {
    if (routeWindowMinutes >= 60 && routeWindowMinutes % 60 === 0) {
      return `${formatCount(routeWindowMinutes / 60, locale)}h`
    }
    return `${formatCount(routeWindowMinutes, locale)}m`
  }, [locale, routeWindowMinutes])
  const routeWindowHint = useMemo(
    () => t(
      'dashboard.health.windowHint',
      'Rolling window from system settings (last {minutes} minutes / {seconds} seconds).',
      {
        minutes: formatCount(routeWindowMinutes, locale),
        seconds: formatCount(routeWindowSeconds, locale),
      },
    ),
    [locale, routeWindowMinutes, routeWindowSeconds, t],
  )
  const topValidationIssue = validationIssueBuckets[0]
  const topRepairShortCircuitReason = useMemo(
    () => topCounterEntries(routeDimensions?.repair_short_circuit_reason, 1)[0],
    [routeDimensions?.repair_short_circuit_reason],
  )
  const generationDecisionTotal = Math.max(
    toSafeCount(routeDimensions?.generation_decision_total),
    sumCounterValues(routeDimensions?.generation_engine),
  )
  const schemaLinkFallbackTotal = resolveFallbackTotal(
    routeDimensions?.schema_link_fallback_total,
    routeDimensions?.schema_link_fallback_rate,
    generationDecisionTotal,
  )
  const sqlGenerationFallbackTotal = resolveFallbackTotal(
    routeDimensions?.sql_generation_fallback_total,
    routeDimensions?.sql_generation_fallback_rate,
    generationDecisionTotal,
  )
  const finalAnswerFallbackTotal = resolveFallbackTotal(
    routeDimensions?.final_answer_fallback_total,
    routeDimensions?.final_answer_fallback_rate,
    generationDecisionTotal,
  )
  const schemaLinkFallbackRate = resolveFallbackRate(
    schemaLinkFallbackTotal,
    routeDimensions?.schema_link_fallback_rate,
    generationDecisionTotal,
  )
  const sqlGenerationFallbackRate = resolveFallbackRate(
    sqlGenerationFallbackTotal,
    routeDimensions?.sql_generation_fallback_rate,
    generationDecisionTotal,
  )
  const finalAnswerFallbackRate = resolveFallbackRate(
    finalAnswerFallbackTotal,
    routeDimensions?.final_answer_fallback_rate,
    generationDecisionTotal,
  )
  const schemaLinkFallbackReasons = useMemo(
    () => topCounterEntries(routeDimensions?.schema_link_fallback_reason, 3),
    [routeDimensions?.schema_link_fallback_reason],
  )
  const sqlGenerationFallbackReasons = useMemo(
    () => topCounterEntries(routeDimensions?.sql_generation_fallback_reason, 3),
    [routeDimensions?.sql_generation_fallback_reason],
  )
  const finalAnswerFallbackReasons = useMemo(
    () => topCounterEntries(routeDimensions?.final_answer_fallback_reason, 3),
    [routeDimensions?.final_answer_fallback_reason],
  )
  const fallbackCards = [
    {
      key: 'schema-link',
      title: t('dashboard.health.schemaLinkFallback', 'Schema-Link Fallback'),
      total: schemaLinkFallbackTotal,
      rate: schemaLinkFallbackRate,
      warningRate: 0.12,
      criticalRate: 0.25,
      reasons: schemaLinkFallbackReasons,
    },
    {
      key: 'sql-generation',
      title: t('dashboard.health.sqlGenerationFallback', 'SQL Generation Fallback'),
      total: sqlGenerationFallbackTotal,
      rate: sqlGenerationFallbackRate,
      warningRate: 0.18,
      criticalRate: 0.35,
      reasons: sqlGenerationFallbackReasons,
    },
    {
      key: 'final-answer',
      title: t('dashboard.health.finalAnswerFallback', 'Final Answer Fallback'),
      total: finalAnswerFallbackTotal,
      rate: finalAnswerFallbackRate,
      warningRate: 0.1,
      criticalRate: 0.25,
      reasons: finalAnswerFallbackReasons,
    },
  ]

  const resolveDatasourceLabel = (datasourceType: string): string => {
    const key = `datasource.type.${datasourceType.toLowerCase()}`
    const localized = t(key)
    if (localized !== key) return localized
    return getDatasourceConfig(datasourceType.toLowerCase())?.displayName ?? datasourceType
  }

  return (
    <Card className={cn('rounded-xl', className)}>
      <CardHeader className="mb-3 items-start">
        <div>
          <CardTitle className="text-base">{t('dashboard.health.title', 'Execution Health')}</CardTitle>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {t('dashboard.health.description', 'Project-scoped datasource execution reliability and latency.')}
          </p>
        </div>
        {metricsEnabled && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => { void metricsQuery.refetch() }}
            loading={metricsQuery.isFetching}
          >
            {t('dashboard.health.refresh', 'Refresh')}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {!metricsEnabled ? (
          <EmptyState
            title={t('dashboard.health.noProjectTitle', 'No project selected')}
            description={t('dashboard.health.noProjectDesc', 'Select a project to view datasource execution health.')}
            className="rounded-lg border border-dashed border-gray-200 px-4 py-6 dark:border-gray-700"
          />
        ) : metricsQuery.isLoading ? (
          <SkeletonRow count={4} className="px-1" />
        ) : metricsQuery.isError ? (
          <EmptyState
            title={t('dashboard.health.loadFailedTitle', 'Unable to load execution health')}
            description={t('dashboard.health.loadFailedDesc', 'Check your permissions and try again.')}
            action={{
              label: t('common.retry', 'Retry'),
              onClick: () => {
                void metricsQuery.refetch()
              },
            }}
            className="rounded-lg border border-dashed border-gray-200 px-4 py-6 dark:border-gray-700"
          />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t('dashboard.health.emptyTitle', 'No execution metrics yet')}
            description={t('dashboard.health.emptyDesc', 'Run a few queries to populate datasource health metrics.')}
            className="rounded-lg border border-dashed border-gray-200 px-4 py-6 dark:border-gray-700"
          />
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-[5px] lg:grid-cols-4">
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.total', 'Total')}</p>
                <p className="text-lg font-semibold text-gray-900 dark:text-gray-100">{formatCount(totals.total, locale)}</p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.successRate', 'Success Rate')}</p>
                <p className={cn('text-lg font-semibold', successRateTone(totals.successRate))}>{formatDecimal(totals.successRate, locale, 1)}%</p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.avgMs', 'Avg Latency')}</p>
                <p className="text-lg font-semibold text-gray-900 dark:text-gray-100">{formatDecimal(totals.avgMs, locale)} ms</p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.p95Ms', 'P95 Latency')}</p>
                <p className="text-lg font-semibold text-gray-900 dark:text-gray-100">{formatDecimal(totals.p95Ms, locale)} ms</p>
              </div>
            </div>

            {hasRouteSignals && (
              <div className="space-y-2 rounded-lg border border-gray-200 bg-gray-50/70 p-3 dark:border-gray-700 dark:bg-gray-800/40">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {t('dashboard.health.routeSignalsTitle', 'Route Observability')}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t(
                        'dashboard.health.routeSignalsDesc',
                        'Retry and validation signals from SQL generation and repair guard in the last {minutes} minutes.',
                        { minutes: formatCount(routeWindowMinutes, locale) },
                      )}
                    </p>
                  </div>
                  <span
                    className="shrink-0 cursor-help rounded-full border border-gray-300 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-600 dark:border-gray-600 dark:bg-gray-900/70 dark:text-gray-300"
                    title={routeWindowHint}
                    aria-label={routeWindowHint}
                  >
                    {t('dashboard.health.window', 'Window')} {routeWindowBadgeLabel}
                  </span>
                </div>
                {routeAlerts.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.alertsTitle', 'Active Alerts')}
                    </p>
                    <div className="space-y-1">
                      {routeAlerts.map((alert) => {
                        const levelLabel = alert.level === 'critical'
                          ? t('dashboard.health.alertCritical', 'Critical')
                          : t('dashboard.health.alertWarning', 'Warning')
                        let label = t('dashboard.health.alert.llmEmptyRetry', 'LLM returned empty SQL payload repeatedly')
                        switch (alert.id) {
                          case 'duplicate_alias':
                            label = t('dashboard.health.alert.duplicateAlias', 'Duplicate alias validation issues')
                            break
                          case 'repair_guard_blocked':
                            label = t('dashboard.health.alert.repairGuardBlocked', 'Repair guard blocked invalid SQL')
                            break
                          case 'llm_http_circuit_open':
                            label = t('dashboard.health.alert.llmHttpCircuitOpen', 'LLM HTTP circuit has open keys')
                            break
                          case 'repair_short_circuit_low':
                            label = t('dashboard.health.alert.repairShortCircuitLow', 'Repair short-circuit hit rate is below baseline')
                            break
                          case 'schema_link_fallback_high':
                            label = t('dashboard.health.alert.schemaLinkFallbackHigh', 'Schema-link fallback rate is elevated')
                            break
                          case 'sql_generation_fallback_high':
                            label = t('dashboard.health.alert.sqlGenerationFallbackHigh', 'SQL generation fallback rate is elevated')
                            break
                          case 'final_answer_fallback_high':
                            label = t('dashboard.health.alert.finalAnswerFallbackHigh', 'Final answer fallback rate is elevated')
                            break
                          default:
                            break
                        }
                        return (
                          <div
                            key={alert.id}
                            className={cn(
                              'flex items-center justify-between rounded-md border px-3 py-2 text-xs',
                              alertTone(alert.level),
                            )}
                          >
                            <div className="min-w-0 pr-2">
                              <p className="truncate font-semibold">{label}</p>
                              <p className="opacity-80">{levelLabel}</p>
                            </div>
                            <div className="shrink-0 text-right font-semibold">
                              <p>{formatCount(alert.count, locale)}</p>
                              <p className="text-[11px] opacity-80">{t('dashboard.health.threshold', 'threshold')} {formatCount(alert.threshold, locale)}</p>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-[5px] lg:grid-cols-6">
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.llmEmptyRetry', 'LLM Empty Retries')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeDimensions?.llm_empty_response_retry || 0, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairGuardBlocked', 'Repair Guard Blocks')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeDimensions?.repair_guard_blocked || 0, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairShortCircuit', 'Repair Short-Circuit')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeDimensions?.repair_short_circuit || 0, locale)}
                    </p>
                    <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                      {topRepairShortCircuitReason
                        ? `${prettifyCounterKey(topRepairShortCircuitReason[0])}: ${formatCount(topRepairShortCircuitReason[1], locale)}`
                        : t('dashboard.health.none', 'None')}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.generationDecisions', 'Generation Decisions')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(generationDecisionTotal, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.fallbackAvg', 'Fallback Avg')}: {formatDecimal(routeDimensions?.fallback_count_avg || 0, locale, 2)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.topValidationIssue', 'Top Validation Issue')}
                    </p>
                    <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {topValidationIssue ? prettifyCounterKey(topValidationIssue[0]) : t('dashboard.health.none', 'None')}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {formatCount(topValidationIssue?.[1] || 0, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.llmHttpCircuitOpen', 'LLM HTTP Circuit Open')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(llmHttpCircuitOpenKeys, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.llmHttpCircuitTracked', 'tracked')} {formatCount(llmHttpCircuitTotalKeys, locale)}
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
                  {fallbackCards.map((card) => (
                    <div key={card.key} className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                      <p className="text-xs text-gray-500 dark:text-gray-400">{card.title}</p>
                      <p className={cn('text-sm font-semibold', fallbackRateTone(card.rate, card.warningRate, card.criticalRate))}>
                        {formatRatioPercent(card.rate, locale, 1)}
                      </p>
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">
                        {t('dashboard.health.total', 'Total')} {formatCount(card.total, locale)}
                        {generationDecisionTotal > 0
                          ? ` / ${formatCount(generationDecisionTotal, locale)} ${t('dashboard.health.decisions', 'decisions')}`
                          : ''}
                      </p>
                      {card.reasons.length === 0 ? (
                        <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.topReasons', 'Top reasons')}: {t('dashboard.health.none', 'None')}
                        </p>
                      ) : (
                        <div className="mt-1 space-y-0.5">
                          {card.reasons.map(([reason, count]) => (
                            <p key={reason} className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                              {prettifyCounterKey(reason)}: {formatCount(count, locale)}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    {t('dashboard.health.llmHttpCircuitOpenKeys', 'LLM HTTP Open Circuit Keys')}
                  </p>
                  {llmHttpOpenKeyEntries.length === 0 ? (
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.llmHttpCircuitNoOpenKeys', 'No open circuit keys')}
                    </p>
                  ) : (
                    <div className="space-y-1">
                      {llmHttpOpenKeyEntries.map(([key, remaining]) => (
                        <div key={key} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                          <span className="truncate pr-2" title={key}>{key}</span>
                          <span className="font-medium">{formatDecimal(remaining, locale, 1)}s</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.retryReasons', 'Retry Reasons')}
                    </p>
                    {retryReasons.length === 0 ? (
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                    ) : (
                      <div className="space-y-1">
                        {retryReasons.map(([reason, count]) => (
                          <div key={reason} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                            <span className="truncate pr-2">{prettifyCounterKey(reason)}</span>
                            <span className="font-medium">{formatCount(count, locale)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.validationIssues', 'Validation Issues')}
                    </p>
                    {validationIssueBuckets.length === 0 ? (
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                    ) : (
                      <div className="space-y-1">
                        {validationIssueBuckets.map(([issue, count]) => (
                          <div key={issue} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                            <span className="truncate pr-2">{prettifyCounterKey(issue)}</span>
                            <span className="font-medium">{formatCount(count, locale)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-800">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.datasource', 'Datasource')}
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.total', 'Total')}
                    </th>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.status', 'Status')}
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.successRate', 'Success Rate')}
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.avgMs', 'Avg Latency')}
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.p95Ms', 'P95 Latency')}
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.avgRows', 'Avg Rows')}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
                  {rows.map((row) => (
                    <tr key={row.datasourceType}>
                      <td className="whitespace-nowrap px-3 py-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                        {resolveDatasourceLabel(row.datasourceType)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">
                        {formatCount(row.total, locale)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-gray-700 dark:text-gray-300">
                        <span className="text-emerald-600 dark:text-emerald-400">
                          {t('dashboard.health.successShort', 'S')} {formatCount(row.success, locale)}
                        </span>
                        <span className="mx-1 text-gray-300 dark:text-gray-600">/</span>
                        <span className="text-amber-600 dark:text-amber-400">
                          {t('dashboard.health.warningShort', 'W')} {formatCount(row.warning, locale)}
                        </span>
                        <span className="mx-1 text-gray-300 dark:text-gray-600">/</span>
                        <span className="text-error dark:text-error-400">
                          {t('dashboard.health.errorShort', 'E')} {formatCount(row.error, locale)}
                        </span>
                        <span className="mx-1 text-gray-300 dark:text-gray-600">/</span>
                        <span className="text-sky-600 dark:text-sky-400">
                          {t('dashboard.health.timeoutShort', 'T')} {formatCount(row.timeout, locale)}
                        </span>
                      </td>
                      <td className={cn('whitespace-nowrap px-3 py-2 text-right text-sm font-medium', successRateTone(row.successRate))}>
                        {formatDecimal(row.successRate, locale, 1)}%
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">
                        {formatDecimal(row.avgMs, locale)} ms
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">
                        {formatDecimal(row.p95Ms, locale)} ms
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right text-sm text-gray-700 dark:text-gray-300">
                        {formatDecimal(row.avgRows, locale, 1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
