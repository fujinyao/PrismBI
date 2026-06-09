import type {
  QueryExecutionMetric,
  QueryExecutionMetrics,
  QueryLLMHttpCircuitSnapshot,
  QueryRouteDimensions,
  QueryStrategyTrendPoint,
} from '@/lib/api'

export interface QueryMetricsRow {
  datasourceType: string
  total: number
  success: number
  warning: number
  error: number
  timeout: number
  avgMs: number
  p95Ms: number
  avgRows: number
  successRate: number
}

export interface QueryMetricsSummary {
  total: number
  success: number
  warning: number
  error: number
  timeout: number
  successRate: number
  avgMs: number
  p95Ms: number
  avgRows: number
}

export interface StrategyObservabilitySummary {
  decisionTotal: number
  riskScoreTotal: number
  riskScoreAvg: number
  riskScoreMax: number
  selectedEngines: Array<[string, number]>
  modes: Array<[string, number]>
  policies: Array<[string, number]>
  riskLevels: Array<[string, number]>
}

export interface StrategyTrendPoint {
  capturedAtMs: number
  decisionTotal: number
  riskScoreAvg: number
  highRiskRate: number
  decomposePolicyRate: number
  dominantMode: string
  dominantPolicy: string
}

export interface StrategyTrendSummary {
  sampleCount: number
  horizonMinutes: number
  modeSwitches: number
  policySwitches: number
  riskScoreDelta: number
  highRiskRateDelta: number
  decomposePolicyRateDelta: number
  currentDominantMode: string
  currentDominantPolicy: string
  driftLevel: 'stable' | 'warning' | 'critical'
}

export interface RouteObservabilityAlert {
  id:
    | 'duplicate_alias'
    | 'repair_guard_blocked'
    | 'llm_empty_response_retry'
    | 'repair_short_circuit_low'
    | 'llm_http_circuit_open'
    | 'schema_link_fallback_high'
    | 'sql_generation_fallback_high'
    | 'final_answer_fallback_high'
    | 'strategy_high_risk_rate'
    | 'strategy_decompose_policy_high'
  level: 'warning' | 'critical'
  count: number
  threshold: number
}

interface FallbackRateAlertConfig {
  id: 'schema_link_fallback_high' | 'sql_generation_fallback_high' | 'final_answer_fallback_high'
  fallbackTotal: number
  warningRate: number
  criticalRate: number
  minWarningDecisions?: number
  minCriticalDecisions?: number
}

const EMPTY_SUMMARY: QueryMetricsSummary = {
  total: 0,
  success: 0,
  warning: 0,
  error: 0,
  timeout: 0,
  successRate: 0,
  avgMs: 0,
  p95Ms: 0,
  avgRows: 0,
}

function toFiniteNumber(value: unknown): number {
  const num = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(num) ? num : 0
}

function toSafeCount(value: unknown): number {
  return Math.max(0, Math.trunc(toFiniteNumber(value)))
}

function toSafeRate(value: unknown): number {
  const rate = toFiniteNumber(value)
  if (rate <= 0) return 0
  if (rate >= 1) return 1
  return rate
}

function sumCounterValues(counter: Record<string, number> | undefined): number {
  return Object.values(counter ?? {}).reduce((total, value) => {
    return total + toSafeCount(value)
  }, 0)
}

function sortedCounterEntries(counter: Record<string, number> | undefined, limit = 3): Array<[string, number]> {
  return Object.entries(counter ?? {})
    .map(([key, value]) => [String(key), toSafeCount(value)] as [string, number])
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
}

function resolveFallbackTotal(
  fallbackTotal: unknown,
  fallbackRate: unknown,
  generationDecisionTotal: number,
): number {
  const total = toSafeCount(fallbackTotal)
  if (total > 0) return total
  if (generationDecisionTotal <= 0) return 0
  return Math.round(toSafeRate(fallbackRate) * generationDecisionTotal)
}

function round2(value: number): number {
  return Math.round(value * 100) / 100
}

function appendFallbackRateAlert(
  alerts: RouteObservabilityAlert[],
  generationDecisionTotal: number,
  config: FallbackRateAlertConfig,
): void {
  const minWarningDecisions = toSafeCount(config.minWarningDecisions ?? 8)
  const minCriticalDecisions = Math.max(minWarningDecisions, toSafeCount(config.minCriticalDecisions ?? 20))
  if (generationDecisionTotal < minWarningDecisions) return

  const warningThreshold = Math.max(1, Math.ceil(generationDecisionTotal * config.warningRate))
  const criticalThreshold = Math.max(warningThreshold + 1, Math.ceil(generationDecisionTotal * config.criticalRate))

  if (generationDecisionTotal >= minCriticalDecisions && config.fallbackTotal >= criticalThreshold) {
    alerts.push({
      id: config.id,
      level: 'critical',
      count: config.fallbackTotal,
      threshold: criticalThreshold,
    })
    return
  }

  if (config.fallbackTotal >= warningThreshold) {
    alerts.push({
      id: config.id,
      level: 'warning',
      count: config.fallbackTotal,
      threshold: warningThreshold,
    })
  }
}

function normalizeMetric(datasourceType: string, metric: Partial<QueryExecutionMetric>): QueryMetricsRow {
  const total = toSafeCount(metric.total)
  const success = toSafeCount(metric.success)
  const warning = toSafeCount(metric.warning)
  const error = toSafeCount(metric.error)
  const timeout = toSafeCount(metric.timeout)
  const avgMs = round2(Math.max(0, toFiniteNumber(metric.avg_ms)))
  const p95Ms = round2(Math.max(0, toFiniteNumber(metric.p95_ms)))
  const avgRows = round2(Math.max(0, toFiniteNumber(metric.avg_rows)))
  const successRate = total > 0 ? round2((success / total) * 100) : 0

  return {
    datasourceType,
    total,
    success,
    warning,
    error,
    timeout,
    avgMs,
    p95Ms,
    avgRows,
    successRate,
  }
}

export function isProjectScopedMetricsEnabled(projectId?: number): projectId is number {
  return typeof projectId === 'number' && Number.isInteger(projectId) && projectId > 0
}

export function queryMetricsQueryKey(projectId?: number): readonly ['query-metrics', number | null] {
  return ['query-metrics', isProjectScopedMetricsEnabled(projectId) ? projectId : null] as const
}

export function normalizeQueryMetricsRows(metrics: QueryExecutionMetrics | null | undefined): QueryMetricsRow[] {
  const rows = Object.entries(metrics ?? {}).map(([datasourceType, metric]) => {
    const safeMetric = (metric && typeof metric === 'object') ? metric : {}
    return normalizeMetric(datasourceType, safeMetric as Partial<QueryExecutionMetric>)
  })

  return rows.sort((a, b) => {
    if (b.total !== a.total) return b.total - a.total
    return a.datasourceType.localeCompare(b.datasourceType)
  })
}

export function aggregateQueryMetrics(rows: QueryMetricsRow[]): QueryMetricsSummary {
  if (rows.length === 0) return EMPTY_SUMMARY

  const totals = rows.reduce((acc, row) => {
    const total = acc.total + row.total
    return {
      total,
      success: acc.success + row.success,
      warning: acc.warning + row.warning,
      error: acc.error + row.error,
      timeout: acc.timeout + row.timeout,
      weightedMs: acc.weightedMs + row.avgMs * row.total,
      weightedRows: acc.weightedRows + row.avgRows * row.total,
      p95Ms: Math.max(acc.p95Ms, row.p95Ms),
    }
  }, {
    total: 0,
    success: 0,
    warning: 0,
    error: 0,
    timeout: 0,
    weightedMs: 0,
    weightedRows: 0,
    p95Ms: 0,
  })

  const successRate = totals.total > 0 ? round2((totals.success / totals.total) * 100) : 0

  return {
    total: totals.total,
    success: totals.success,
    warning: totals.warning,
    error: totals.error,
    timeout: totals.timeout,
    successRate,
    avgMs: totals.total > 0 ? round2(totals.weightedMs / totals.total) : 0,
    p95Ms: round2(totals.p95Ms),
    avgRows: totals.total > 0 ? round2(totals.weightedRows / totals.total) : 0,
  }
}

export function evaluateRouteObservabilityAlerts(
  routeDimensions: QueryRouteDimensions | null | undefined,
  llmHttpCircuit: QueryLLMHttpCircuitSnapshot | null | undefined = undefined,
): RouteObservabilityAlert[] {
  if (!routeDimensions && !llmHttpCircuit) return []

  const duplicateAliasCount = toSafeCount(routeDimensions?.validation_issue_bucket?.duplicate_alias)
  const repairGuardBlocked = toSafeCount(routeDimensions?.repair_guard_blocked)
  const emptyRetryCount = toSafeCount(routeDimensions?.llm_empty_response_retry)
  const repairShortCircuitCount = toSafeCount(routeDimensions?.repair_short_circuit)
  const llmHttpOpenKeys = toSafeCount(llmHttpCircuit?.open_keys)
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
  const strategySummary = summarizeStrategyObservability(routeDimensions)
  const strategyDecisionTotal = strategySummary.decisionTotal
  const strategyHighRiskCount = toSafeCount(routeDimensions?.strategy_risk_level?.high)
  const strategyDecomposePolicyCount = Math.max(
    toSafeCount(routeDimensions?.strategy_policy?.risk_decompose_merge),
    toSafeCount(routeDimensions?.strategy_policy?.decompose_merge),
  )

  const alerts: RouteObservabilityAlert[] = []

  if (duplicateAliasCount >= 5) {
    alerts.push({ id: 'duplicate_alias', level: 'critical', count: duplicateAliasCount, threshold: 5 })
  } else if (duplicateAliasCount >= 2) {
    alerts.push({ id: 'duplicate_alias', level: 'warning', count: duplicateAliasCount, threshold: 2 })
  }

  if (repairGuardBlocked >= 3) {
    alerts.push({ id: 'repair_guard_blocked', level: 'critical', count: repairGuardBlocked, threshold: 3 })
  } else if (repairGuardBlocked >= 1) {
    alerts.push({ id: 'repair_guard_blocked', level: 'warning', count: repairGuardBlocked, threshold: 1 })
  }

  if (emptyRetryCount >= 8) {
    alerts.push({ id: 'llm_empty_response_retry', level: 'critical', count: emptyRetryCount, threshold: 8 })
  } else if (emptyRetryCount >= 3) {
    alerts.push({ id: 'llm_empty_response_retry', level: 'warning', count: emptyRetryCount, threshold: 3 })
  }

  const repairResolutionAttempts = repairGuardBlocked + repairShortCircuitCount
  if (repairResolutionAttempts >= 8) {
    const criticalThreshold = Math.max(1, Math.ceil(repairResolutionAttempts * 0.2))
    if (repairShortCircuitCount < criticalThreshold) {
      alerts.push({
        id: 'repair_short_circuit_low',
        level: 'critical',
        count: repairShortCircuitCount,
        threshold: criticalThreshold,
      })
    } else {
      const warningThreshold = Math.max(1, Math.ceil(repairResolutionAttempts * 0.4))
      if (repairShortCircuitCount < warningThreshold) {
        alerts.push({
          id: 'repair_short_circuit_low',
          level: 'warning',
          count: repairShortCircuitCount,
          threshold: warningThreshold,
        })
      }
    }
  } else if (repairResolutionAttempts >= 4) {
    const warningThreshold = Math.max(1, Math.ceil(repairResolutionAttempts * 0.4))
    if (repairShortCircuitCount < warningThreshold) {
      alerts.push({
        id: 'repair_short_circuit_low',
        level: 'warning',
        count: repairShortCircuitCount,
        threshold: warningThreshold,
      })
    }
  }

  if (llmHttpOpenKeys >= 3) {
    alerts.push({ id: 'llm_http_circuit_open', level: 'critical', count: llmHttpOpenKeys, threshold: 3 })
  } else if (llmHttpOpenKeys >= 1) {
    alerts.push({ id: 'llm_http_circuit_open', level: 'warning', count: llmHttpOpenKeys, threshold: 1 })
  }

  appendFallbackRateAlert(alerts, generationDecisionTotal, {
    id: 'schema_link_fallback_high',
    fallbackTotal: schemaLinkFallbackTotal,
    warningRate: 0.12,
    criticalRate: 0.25,
  })
  appendFallbackRateAlert(alerts, generationDecisionTotal, {
    id: 'sql_generation_fallback_high',
    fallbackTotal: sqlGenerationFallbackTotal,
    warningRate: 0.18,
    criticalRate: 0.35,
  })
  appendFallbackRateAlert(alerts, generationDecisionTotal, {
    id: 'final_answer_fallback_high',
    fallbackTotal: finalAnswerFallbackTotal,
    warningRate: 0.1,
    criticalRate: 0.25,
  })

  if (strategyDecisionTotal >= 12) {
    const highRiskWarningThreshold = Math.max(1, Math.ceil(strategyDecisionTotal * 0.25))
    const highRiskCriticalThreshold = Math.max(
      highRiskWarningThreshold + 1,
      Math.ceil(strategyDecisionTotal * 0.45),
    )
    if (strategyDecisionTotal >= 20 && strategyHighRiskCount >= highRiskCriticalThreshold) {
      alerts.push({
        id: 'strategy_high_risk_rate',
        level: 'critical',
        count: strategyHighRiskCount,
        threshold: highRiskCriticalThreshold,
      })
    } else if (strategyHighRiskCount >= highRiskWarningThreshold) {
      alerts.push({
        id: 'strategy_high_risk_rate',
        level: 'warning',
        count: strategyHighRiskCount,
        threshold: highRiskWarningThreshold,
      })
    }
  }

  if (strategyDecisionTotal >= 10) {
    const decomposeWarningThreshold = Math.max(1, Math.ceil(strategyDecisionTotal * 0.3))
    const decomposeCriticalThreshold = Math.max(
      decomposeWarningThreshold + 1,
      Math.ceil(strategyDecisionTotal * 0.55),
    )
    if (strategyDecisionTotal >= 20 && strategyDecomposePolicyCount >= decomposeCriticalThreshold) {
      alerts.push({
        id: 'strategy_decompose_policy_high',
        level: 'critical',
        count: strategyDecomposePolicyCount,
        threshold: decomposeCriticalThreshold,
      })
    } else if (strategyDecomposePolicyCount >= decomposeWarningThreshold) {
      alerts.push({
        id: 'strategy_decompose_policy_high',
        level: 'warning',
        count: strategyDecomposePolicyCount,
        threshold: decomposeWarningThreshold,
      })
    }
  }

  return alerts.sort((a, b) => {
    if (a.level !== b.level) return a.level === 'critical' ? -1 : 1
    if (b.count !== a.count) return b.count - a.count
    return a.id.localeCompare(b.id)
  })
}

export function summarizeStrategyObservability(
  routeDimensions: QueryRouteDimensions | null | undefined,
): StrategyObservabilitySummary {
  const decisionTotal = Math.max(
    toSafeCount(routeDimensions?.generation_decision_total),
    sumCounterValues(routeDimensions?.generation_engine),
    sumCounterValues(routeDimensions?.strategy_selected_engine),
    sumCounterValues(routeDimensions?.strategy_mode),
    sumCounterValues(routeDimensions?.strategy_policy),
  )
  const riskScoreTotal = toSafeCount(routeDimensions?.strategy_risk_score_total)
  const riskScoreAvgFromPayload = Math.max(0, toFiniteNumber(routeDimensions?.strategy_risk_score_avg))
  const riskScoreAvg = decisionTotal > 0 && riskScoreTotal > 0
    ? round2(riskScoreTotal / decisionTotal)
    : round2(riskScoreAvgFromPayload)

  return {
    decisionTotal,
    riskScoreTotal,
    riskScoreAvg,
    riskScoreMax: toSafeCount(routeDimensions?.strategy_risk_score_max),
    selectedEngines: sortedCounterEntries(routeDimensions?.strategy_selected_engine, 4),
    modes: sortedCounterEntries(routeDimensions?.strategy_mode, 4),
    policies: sortedCounterEntries(routeDimensions?.strategy_policy, 4),
    riskLevels: sortedCounterEntries(routeDimensions?.strategy_risk_level, 4),
  }
}

export function buildStrategyTrendPoint(
  routeDimensions: QueryRouteDimensions | null | undefined,
  capturedAtMs: number = Date.now(),
): StrategyTrendPoint | null {
  const summary = summarizeStrategyObservability(routeDimensions)
  if (summary.decisionTotal <= 0) return null

  const highRiskCount = toSafeCount(routeDimensions?.strategy_risk_level?.high)
  const decomposePolicyCount = Math.max(
    toSafeCount(routeDimensions?.strategy_policy?.risk_decompose_merge),
    toSafeCount(routeDimensions?.strategy_policy?.decompose_merge),
  )

  return {
    capturedAtMs: Math.max(0, Math.round(toFiniteNumber(capturedAtMs))),
    decisionTotal: summary.decisionTotal,
    riskScoreAvg: round2(summary.riskScoreAvg),
    highRiskRate: summary.decisionTotal > 0 ? toSafeRate(highRiskCount / summary.decisionTotal) : 0,
    decomposePolicyRate: summary.decisionTotal > 0 ? toSafeRate(decomposePolicyCount / summary.decisionTotal) : 0,
    dominantMode: summary.modes[0]?.[0] ?? '',
    dominantPolicy: summary.policies[0]?.[0] ?? '',
  }
}

export function appendStrategyTrendPoint(
  history: StrategyTrendPoint[],
  point: StrategyTrendPoint,
  maxPoints = 20,
): StrategyTrendPoint[] {
  const normalizedHistory = Array.isArray(history) ? history : []
  const limit = Math.max(2, toSafeCount(maxPoints || 20))
  const last = normalizedHistory[normalizedHistory.length - 1]
  if (last) {
    const unchanged =
      last.decisionTotal === point.decisionTotal
      && Math.abs(last.riskScoreAvg - point.riskScoreAvg) < 0.01
      && Math.abs(last.highRiskRate - point.highRiskRate) < 0.001
      && Math.abs(last.decomposePolicyRate - point.decomposePolicyRate) < 0.001
      && last.dominantMode === point.dominantMode
      && last.dominantPolicy === point.dominantPolicy
    if (unchanged) {
      return normalizedHistory
    }
  }
  const next = [...normalizedHistory, point]
  if (next.length <= limit) return next
  return next.slice(next.length - limit)
}

export function normalizeStrategyTrendHistory(
  history: QueryStrategyTrendPoint[] | null | undefined,
  maxPoints = 24,
): StrategyTrendPoint[] {
  const limit = Math.max(2, toSafeCount(maxPoints || 24))
  const normalized = (Array.isArray(history) ? history : [])
    .map((item) => {
      const capturedAtMs = Math.max(0, Math.round(toFiniteNumber(item?.captured_at_unix) * 1000))
      const decisionTotal = toSafeCount(item?.decision_total)
      return {
        capturedAtMs,
        decisionTotal,
        riskScoreAvg: round2(Math.max(0, toFiniteNumber(item?.risk_score_avg))),
        highRiskRate: toSafeRate(item?.high_risk_rate),
        decomposePolicyRate: toSafeRate(item?.decompose_policy_rate),
        dominantMode: String(item?.dominant_mode ?? '').trim().toLowerCase(),
        dominantPolicy: String(item?.dominant_policy ?? '').trim().toLowerCase(),
      }
    })
    .filter((item) => item.capturedAtMs > 0 && item.decisionTotal > 0)
    .sort((a, b) => a.capturedAtMs - b.capturedAtMs)

  return normalized.reduce<StrategyTrendPoint[]>((acc, point) => {
    return appendStrategyTrendPoint(acc, point, limit)
  }, [])
}

export function summarizeStrategyTrend(history: StrategyTrendPoint[]): StrategyTrendSummary {
  const points = (Array.isArray(history) ? history : []).filter((item) => {
    return item && Number.isFinite(item.capturedAtMs) && item.capturedAtMs > 0
  })
  if (points.length === 0) {
    return {
      sampleCount: 0,
      horizonMinutes: 0,
      modeSwitches: 0,
      policySwitches: 0,
      riskScoreDelta: 0,
      highRiskRateDelta: 0,
      decomposePolicyRateDelta: 0,
      currentDominantMode: '',
      currentDominantPolicy: '',
      driftLevel: 'stable',
    }
  }

  const first = points[0] as StrategyTrendPoint
  const last = points[points.length - 1] as StrategyTrendPoint
  let modeSwitches = 0
  let policySwitches = 0
  for (let index = 1; index < points.length; index += 1) {
    const prev = points[index - 1] as StrategyTrendPoint
    const current = points[index] as StrategyTrendPoint
    if (prev.dominantMode && current.dominantMode && prev.dominantMode !== current.dominantMode) {
      modeSwitches += 1
    }
    if (prev.dominantPolicy && current.dominantPolicy && prev.dominantPolicy !== current.dominantPolicy) {
      policySwitches += 1
    }
  }

  const riskScoreDelta = round2(last.riskScoreAvg - first.riskScoreAvg)
  const highRiskRateDelta = round2(last.highRiskRate - first.highRiskRate)
  const decomposePolicyRateDelta = round2(last.decomposePolicyRate - first.decomposePolicyRate)
  const horizonMinutes = round2(Math.max(0, (last.capturedAtMs - first.capturedAtMs) / 60000))

  let driftLevel: 'stable' | 'warning' | 'critical' = 'stable'
  const critical =
    riskScoreDelta >= 1.5
    || highRiskRateDelta >= 0.15
    || decomposePolicyRateDelta >= 0.2
    || policySwitches >= 3
    || modeSwitches >= 4
  const warning =
    riskScoreDelta >= 0.7
    || highRiskRateDelta >= 0.08
    || decomposePolicyRateDelta >= 0.1
    || policySwitches >= 2
    || modeSwitches >= 2
  if (critical) {
    driftLevel = 'critical'
  } else if (warning) {
    driftLevel = 'warning'
  }

  return {
    sampleCount: points.length,
    horizonMinutes,
    modeSwitches,
    policySwitches,
    riskScoreDelta,
    highRiskRateDelta,
    decomposePolicyRateDelta,
    currentDominantMode: last.dominantMode,
    currentDominantPolicy: last.dominantPolicy,
    driftLevel,
  }
}
