'use client'

import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { queryApi } from '@/lib/api'
import { getDatasourceConfig } from '@/lib/datasourceConfig'
import {
  aggregateQueryMetrics,
  evaluateRouteObservabilityAlerts,
  isProjectScopedMetricsEnabled,
  normalizeStrategyTrendHistory,
  normalizeQueryMetricsRows,
  queryMetricsQueryKey,
  summarizeRoutePathologies,
  summarizeRouteRepairObservability,
  summarizeStrategyTrend,
  summarizeStrategyObservability,
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

function formatSignedDecimal(value: number, locale: string, digits = 2): string {
  const normalized = Number.isFinite(value) ? value : 0
  const prefix = normalized > 0 ? '+' : ''
  return `${prefix}${formatLocaleNumber(normalized, locale, { maximumFractionDigits: digits })}`
}

function formatSignedRatioPercent(value: number, locale: string, digits = 1): string {
  const normalized = Number.isFinite(value) ? value : 0
  const prefix = normalized > 0 ? '+' : ''
  return `${prefix}${formatLocaleNumber(normalized * 100, locale, { maximumFractionDigits: digits })}%`
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

function prettifyTransitionKey(value: string): string {
  const [from, to] = value.split('->')
  if (!from || !to) return prettifyCounterKey(value)
  return `${prettifyCounterKey(from)} -> ${prettifyCounterKey(to)}`
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

function driftTone(level: 'stable' | 'warning' | 'critical'): string {
  if (level === 'critical') return 'text-error dark:text-error-300'
  if (level === 'warning') return 'text-amber-600 dark:text-amber-300'
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
  const strategyTrendHistory = useMemo(
    () => normalizeStrategyTrendHistory(metricsQuery.data?.strategy_trend_history, 24),
    [metricsQuery.data?.strategy_trend_history],
  )

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
  const topDecomposeStageReason = useMemo(
    () => topCounterEntries(routeDimensions?.decompose_stage_reason, 1)[0],
    [routeDimensions?.decompose_stage_reason],
  )
  const generationDecisionTotal = Math.max(
    toSafeCount(routeDimensions?.generation_decision_total),
    sumCounterValues(routeDimensions?.generation_engine),
  )
  const routeRepairSummary = useMemo(
    () => summarizeRouteRepairObservability(routeDimensions),
    [routeDimensions],
  )
  const routePathologySummary = useMemo(
    () => summarizeRoutePathologies(routeDimensions),
    [routeDimensions],
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
  const topValidationIssueTransition = routePathologySummary.validationIssueTransitions[0]
  const topFallbackChainPattern = routePathologySummary.fallbackChainPatterns[0]
  const topFallbackChainStep = routePathologySummary.fallbackChainSteps[0]
  const topRepairIssueBucket = routePathologySummary.repairShortCircuitIssueBuckets[0]
  const topRepairDominantIssueBucket = routePathologySummary.repairShortCircuitDominantIssueBuckets[0]
  const strategySummary = useMemo(
    () => summarizeStrategyObservability(routeDimensions),
    [routeDimensions],
  )
  const topStrategyEngine = strategySummary.selectedEngines[0]
  const topStrategyMode = strategySummary.modes[0]
  const topStrategyPolicy = strategySummary.policies[0]
  const topStrategyRiskLevel = strategySummary.riskLevels[0]
  const hasStrategySignals =
    strategySummary.decisionTotal > 0
    || strategySummary.riskScoreTotal > 0
    || strategySummary.selectedEngines.length > 0
    || strategySummary.modes.length > 0
    || strategySummary.policies.length > 0
    || strategySummary.riskLevels.length > 0
  const strategyTrendSummary = useMemo(
    () => summarizeStrategyTrend(strategyTrendHistory),
    [strategyTrendHistory],
  )
  const hasStrategyTrend = strategyTrendSummary.sampleCount >= 2
  const strategyDriftLabel = useMemo(() => {
    if (strategyTrendSummary.driftLevel === 'critical') {
      return t('dashboard.health.strategyDriftCritical', 'Critical Drift')
    }
    if (strategyTrendSummary.driftLevel === 'warning') {
      return t('dashboard.health.strategyDriftWarning', 'Warning Drift')
    }
    return t('dashboard.health.strategyDriftStable', 'Stable')
  }, [strategyTrendSummary.driftLevel, t])

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
                        let hint: string | null = null
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
                          case 'repair_timeout_short_circuit_high':
                            label = t('dashboard.health.alert.repairTimeoutShortCircuitHigh', 'Repair timeout short-circuit rate is elevated')
                            hint = t(
                              'dashboard.health.alertHint.repairTimeoutShortCircuitHigh',
                              'Triggered when repair timeout short-circuit count reaches at least {threshold} out of {total} repair short-circuit events in this window.',
                              {
                                threshold: formatCount(alert.threshold, locale),
                                total: formatCount(routeRepairSummary.repairShortCircuitTotal, locale),
                              },
                            )
                            break
                          case 'repair_budget_low_short_circuit_high':
                            label = t('dashboard.health.alert.repairBudgetLowShortCircuitHigh', 'Repair budget-low short-circuit rate is elevated')
                            hint = t(
                              'dashboard.health.alertHint.repairBudgetLowShortCircuitHigh',
                              'Triggered when budget-low repair short-circuit count reaches at least {threshold} out of {total} repair short-circuit events in this window.',
                              {
                                threshold: formatCount(alert.threshold, locale),
                                total: formatCount(routeRepairSummary.repairShortCircuitTotal, locale),
                              },
                            )
                            break
                          case 'json_reask_high':
                            label = t('dashboard.health.alert.jsonReaskHigh', 'Strict JSON re-ask rate is elevated')
                            hint = t(
                              'dashboard.health.alertHint.jsonReaskHigh',
                              'Triggered when strict JSON re-ask count reaches at least {threshold} within {decisions} generation decisions in this window.',
                              {
                                threshold: formatCount(alert.threshold, locale),
                                decisions: formatCount(generationDecisionTotal, locale),
                              },
                            )
                            break
                          case 'decompose_cancelled_high':
                            label = t('dashboard.health.alert.decomposeCancelledHigh', 'Decompose cancellation rate is elevated')
                            hint = t(
                              'dashboard.health.alertHint.decomposeCancelledHigh',
                              'Triggered when cancelled decompose stages reach at least {threshold} out of {total} decompose stages in this window.',
                              {
                                threshold: formatCount(alert.threshold, locale),
                                total: formatCount(routeRepairSummary.decomposeStageTotal, locale),
                              },
                            )
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
                          case 'strategy_high_risk_rate':
                            label = t('dashboard.health.alert.strategyHighRiskRate', 'High-risk strategy decisions are elevated')
                            break
                          case 'strategy_decompose_policy_high':
                            label = t('dashboard.health.alert.strategyDecomposePolicyHigh', 'Decompose policy usage is elevated')
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
                              <p className="truncate opacity-80">
                                {levelLabel}
                                {hint && (
                                  <span
                                    className="ml-1 cursor-help underline decoration-dotted underline-offset-2"
                                    title={hint}
                                    aria-label={hint}
                                  >
                                    {t('dashboard.health.alertExplain', 'Explain')}
                                  </span>
                                )}
                              </p>
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
                <div className="grid grid-cols-2 gap-[5px] lg:grid-cols-4">
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
                      {formatCount(routeRepairSummary.repairShortCircuitTotal, locale)}
                    </p>
                    <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                      {topRepairShortCircuitReason
                        ? `${prettifyCounterKey(topRepairShortCircuitReason[0])}: ${formatCount(topRepairShortCircuitReason[1], locale)}`
                        : t('dashboard.health.none', 'None')}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairIssueBucket', 'Repair Issue Bucket')}
                    </p>
                    <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {topRepairIssueBucket
                        ? prettifyCounterKey(topRepairIssueBucket[0])
                        : t('dashboard.health.none', 'None')}
                    </p>
                    <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                      {formatCount(topRepairIssueBucket?.[1] || 0, locale)}
                      {' · '}
                      {t('dashboard.health.dominant', 'dominant')} {topRepairDominantIssueBucket
                        ? `${prettifyCounterKey(topRepairDominantIssueBucket[0])} (${formatCount(topRepairDominantIssueBucket[1], locale)})`
                        : t('dashboard.health.none', 'None')}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairLocalPreflight', 'Repair Local Preflight')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeRepairSummary.repairLocalPreflight, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {formatRatioPercent(routeRepairSummary.repairLocalPreflightRate, locale, 1)}
                      {' · '}
                      {t('dashboard.health.repairShortCircuit', 'Repair Short-Circuit')} {formatCount(routeRepairSummary.repairShortCircuitTotal, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairTimeoutShortCircuit', 'Repair Timeout Short-Circuit')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeRepairSummary.repairTimeoutShortCircuit, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {formatRatioPercent(routeRepairSummary.repairTimeoutShortCircuitRate, locale, 1)}
                      {' · '}
                      {t('dashboard.health.repairShortCircuit', 'Repair Short-Circuit')} {formatCount(routeRepairSummary.repairShortCircuitTotal, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairBudgetLowShortCircuit', 'Repair Budget Low Short-Circuit')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeRepairSummary.repairBudgetLowShortCircuit, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {formatRatioPercent(routeRepairSummary.repairBudgetLowShortCircuitRate, locale, 1)}
                      {' · '}
                      {t('dashboard.health.repairShortCircuit', 'Repair Short-Circuit')} {formatCount(routeRepairSummary.repairShortCircuitTotal, locale)}
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
                      {t('dashboard.health.jsonReask', 'Strict JSON Re-ask')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeRepairSummary.jsonReaskTotal, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {formatRatioPercent(routeRepairSummary.jsonReaskRate, locale, 1)}
                      {' · '}
                      {formatCount(generationDecisionTotal, locale)} {t('dashboard.health.decisions', 'decisions')}
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
                      {t('dashboard.health.validationIssueTransition', 'Validation Bucket Transition')}
                    </p>
                    <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {topValidationIssueTransition
                        ? prettifyTransitionKey(topValidationIssueTransition[0])
                        : t('dashboard.health.none', 'None')}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {formatCount(topValidationIssueTransition?.[1] || 0, locale)}
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
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.decomposeLatency', 'Decompose Stage Latency')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      P50 {formatDecimal(routeRepairSummary.decomposeStageElapsedMsP50, locale)} ms
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      P95 {formatDecimal(routeRepairSummary.decomposeStageElapsedMsP95, locale)} ms
                      {' · '}
                      {t('dashboard.health.total', 'Total')} {formatCount(routeRepairSummary.decomposeStageTotal, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.decomposeBudgetExceeded', 'Decompose Budget Exceeded')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeRepairSummary.decomposeStageBudgetExceeded, locale)}
                    </p>
                    <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                      {formatRatioPercent(routeRepairSummary.decomposeStageBudgetExceededRate, locale, 1)}
                      {' · '}
                      {topDecomposeStageReason
                        ? `${prettifyCounterKey(topDecomposeStageReason[0])}: ${formatCount(topDecomposeStageReason[1], locale)}`
                        : t('dashboard.health.none', 'None')}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.decomposeCancelled', 'Decompose Cancelled')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routeRepairSummary.decomposeStageCancelled, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {formatRatioPercent(routeRepairSummary.decomposeStageCancelledRate, locale, 1)}
                      {' · '}
                      {t('dashboard.health.total', 'Total')} {formatCount(routeRepairSummary.decomposeStageTotal, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.repairIssueBucketStreak', 'Repair Issue Bucket Streak')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatCount(routePathologySummary.repairIssueBucketStreakMax, locale)}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.circuitable', 'circuitable')}
                      {' '}
                      {formatCount(routePathologySummary.repairCircuitableIssueBucketStreakMax, locale)}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.fallbackChainPattern', 'Fallback Chain Pattern')}
                    </p>
                    <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {topFallbackChainPattern
                        ? prettifyCounterKey(topFallbackChainPattern[0])
                        : t('dashboard.health.none', 'None')}
                    </p>
                    <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                      {formatCount(topFallbackChainPattern?.[1] || 0, locale)}
                      {' · '}
                      {t('dashboard.health.step', 'step')} {topFallbackChainStep
                        ? `${prettifyCounterKey(topFallbackChainStep[0])} (${formatCount(topFallbackChainStep[1], locale)})`
                        : t('dashboard.health.none', 'None')}
                    </p>
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.didYouMeanAppliedRate', 'Did-You-Mean Applied')}
                    </p>
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatRatioPercent(routeRepairSummary.didYouMeanFixAppliedRate, locale, 1)}
                    </p>
                    <p className="truncate text-[11px] text-gray-500 dark:text-gray-400">
                      {formatCount(routeRepairSummary.didYouMeanFixApplied, locale)} / {formatCount(routeRepairSummary.didYouMeanFixTotal, locale)}
                      {' · '}
                      {(routeRepairSummary.didYouMeanStatuses[0]
                        ? `${prettifyCounterKey(routeRepairSummary.didYouMeanStatuses[0][0])}: ${formatCount(routeRepairSummary.didYouMeanStatuses[0][1], locale)}`
                        : t('dashboard.health.none', 'None'))}
                    </p>
                  </div>
                </div>
                {hasStrategySignals && (
                  <div className="space-y-2 rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.strategyRoutingTitle', 'Adaptive Strategy Routing')}
                    </p>
                    <div className="grid grid-cols-2 gap-[5px] lg:grid-cols-5">
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyDecisions', 'Strategy Decisions')}
                        </p>
                        <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                          {formatCount(strategySummary.decisionTotal, locale)}
                        </p>
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyRiskAvg', 'Risk Score Avg')}
                        </p>
                        <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                          {formatDecimal(strategySummary.riskScoreAvg, locale, 2)}
                        </p>
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyRiskMax', 'Risk Score Max')}
                        </p>
                        <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                          {formatCount(strategySummary.riskScoreMax, locale)}
                        </p>
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyTopMode', 'Top Mode')}
                        </p>
                        <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100" title={topStrategyMode?.[0] || ''}>
                          {topStrategyMode ? prettifyCounterKey(topStrategyMode[0]) : t('dashboard.health.none', 'None')}
                        </p>
                        <p className="text-[11px] text-gray-500 dark:text-gray-400">
                          {formatCount(topStrategyMode?.[1] || 0, locale)}
                        </p>
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyTopPolicy', 'Top Policy')}
                        </p>
                        <p className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100" title={topStrategyPolicy?.[0] || ''}>
                          {topStrategyPolicy ? prettifyCounterKey(topStrategyPolicy[0]) : t('dashboard.health.none', 'None')}
                        </p>
                        <p className="text-[11px] text-gray-500 dark:text-gray-400">
                          {formatCount(topStrategyPolicy?.[1] || 0, locale)}
                        </p>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-2 lg:grid-cols-4">
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyEngines', 'Selected Engines')}
                        </p>
                        {strategySummary.selectedEngines.length === 0 ? (
                          <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                        ) : (
                          <div className="space-y-1">
                            {strategySummary.selectedEngines.map(([engine, count]) => (
                              <div key={engine} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                                <span className="truncate pr-2">{prettifyCounterKey(engine)}</span>
                                <span className="font-medium">{formatCount(count, locale)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyModes', 'Strategy Modes')}
                        </p>
                        {strategySummary.modes.length === 0 ? (
                          <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                        ) : (
                          <div className="space-y-1">
                            {strategySummary.modes.map(([mode, count]) => (
                              <div key={mode} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                                <span className="truncate pr-2">{prettifyCounterKey(mode)}</span>
                                <span className="font-medium">{formatCount(count, locale)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyPolicies', 'Strategy Policies')}
                        </p>
                        {strategySummary.policies.length === 0 ? (
                          <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                        ) : (
                          <div className="space-y-1">
                            {strategySummary.policies.map(([policy, count]) => (
                              <div key={policy} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                                <span className="truncate pr-2">{prettifyCounterKey(policy)}</span>
                                <span className="font-medium">{formatCount(count, locale)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyRiskLevels', 'Risk Levels')}
                        </p>
                        {strategySummary.riskLevels.length === 0 ? (
                          <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                        ) : (
                          <div className="space-y-1">
                            {strategySummary.riskLevels.map(([level, count]) => (
                              <div key={level} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                                <span className="truncate pr-2">{prettifyCounterKey(level)}</span>
                                <span className="font-medium">{formatCount(count, locale)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.strategyTopEngine', 'Top selected engine')}: {topStrategyEngine ? `${prettifyCounterKey(topStrategyEngine[0])} (${formatCount(topStrategyEngine[1], locale)})` : t('dashboard.health.none', 'None')}
                      {' · '}
                      {t('dashboard.health.strategyTopRiskLevel', 'Top risk level')}: {topStrategyRiskLevel ? `${prettifyCounterKey(topStrategyRiskLevel[0])} (${formatCount(topStrategyRiskLevel[1], locale)})` : t('dashboard.health.none', 'None')}
                    </p>
                    <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyDriftTitle', 'Strategy Drift')}
                        </p>
                        <span className={cn('text-xs font-semibold', driftTone(strategyTrendSummary.driftLevel))}>
                          {strategyDriftLabel}
                        </span>
                      </div>
                      {hasStrategyTrend ? (
                        <div className="mt-2 grid grid-cols-2 gap-[5px] lg:grid-cols-5">
                          <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
                            <p className="text-[11px] text-gray-500 dark:text-gray-400">
                              {t('dashboard.health.strategyDriftHorizon', 'Horizon')}
                            </p>
                            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                              {formatDecimal(strategyTrendSummary.horizonMinutes, locale, 1)}m
                            </p>
                          </div>
                          <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
                            <p className="text-[11px] text-gray-500 dark:text-gray-400">
                              {t('dashboard.health.strategyRiskDelta', 'Risk Avg Delta')}
                            </p>
                            <p className={cn('text-sm font-semibold', driftTone(strategyTrendSummary.riskScoreDelta > 0 ? strategyTrendSummary.driftLevel : 'stable'))}>
                              {formatSignedDecimal(strategyTrendSummary.riskScoreDelta, locale, 2)}
                            </p>
                          </div>
                          <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
                            <p className="text-[11px] text-gray-500 dark:text-gray-400">
                              {t('dashboard.health.strategyHighRiskDelta', 'High-Risk Delta')}
                            </p>
                            <p className={cn('text-sm font-semibold', driftTone(strategyTrendSummary.highRiskRateDelta > 0 ? strategyTrendSummary.driftLevel : 'stable'))}>
                              {formatSignedRatioPercent(strategyTrendSummary.highRiskRateDelta, locale, 1)}
                            </p>
                          </div>
                          <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
                            <p className="text-[11px] text-gray-500 dark:text-gray-400">
                              {t('dashboard.health.strategyDecomposeDelta', 'Decompose Delta')}
                            </p>
                            <p className={cn('text-sm font-semibold', driftTone(strategyTrendSummary.decomposePolicyRateDelta > 0 ? strategyTrendSummary.driftLevel : 'stable'))}>
                              {formatSignedRatioPercent(strategyTrendSummary.decomposePolicyRateDelta, locale, 1)}
                            </p>
                          </div>
                          <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
                            <p className="text-[11px] text-gray-500 dark:text-gray-400">
                              {t('dashboard.health.strategyModeSwitches', 'Mode/Policy Switches')}
                            </p>
                            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                              {formatCount(strategyTrendSummary.modeSwitches, locale)} / {formatCount(strategyTrendSummary.policySwitches, locale)}
                            </p>
                          </div>
                        </div>
                      ) : (
                        <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                          {t('dashboard.health.strategyDriftNeedMoreSamples', 'Collecting trend samples. Refresh cycles will populate drift metrics.')}
                        </p>
                      )}
                    </div>
                  </div>
                )}
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
                <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
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
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.fallbackChainSteps', 'Fallback Chain Steps')}
                    </p>
                    {routePathologySummary.fallbackChainSteps.length === 0 ? (
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                    ) : (
                      <div className="space-y-1">
                        {routePathologySummary.fallbackChainSteps.map(([step, count]) => (
                          <div key={step} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                            <span className="truncate pr-2">{prettifyCounterKey(step)}</span>
                            <span className="font-medium">{formatCount(count, locale)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.validationIssueTransitions', 'Validation Bucket Transitions')}
                    </p>
                    {routePathologySummary.validationIssueTransitions.length === 0 ? (
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                    ) : (
                      <div className="space-y-1">
                        {routePathologySummary.validationIssueTransitions.map(([transition, count]) => (
                          <div key={transition} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                            <span className="truncate pr-2">{prettifyTransitionKey(transition)}</span>
                            <span className="font-medium">{formatCount(count, locale)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {t('dashboard.health.fallbackChainPatterns', 'Fallback Chain Patterns')}
                    </p>
                    {routePathologySummary.fallbackChainPatterns.length === 0 ? (
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('dashboard.health.none', 'None')}</p>
                    ) : (
                      <div className="space-y-1">
                        {routePathologySummary.fallbackChainPatterns.map(([pattern, count]) => (
                          <div key={pattern} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                            <span className="truncate pr-2">{prettifyCounterKey(pattern)}</span>
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
