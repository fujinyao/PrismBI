import { describe, expect, it } from 'vitest'
import type { QueryRouteDimensions, QueryStrategyTrendPoint } from '@/lib/api'
import {
  aggregateQueryMetrics,
  appendStrategyTrendPoint,
  buildStrategyTrendPoint,
  evaluateRouteObservabilityAlerts,
  isProjectScopedMetricsEnabled,
  normalizeStrategyTrendHistory,
  normalizeQueryMetricsRows,
  queryMetricsQueryKey,
  summarizeStrategyTrend,
  summarizeStrategyObservability,
} from '@/lib/queryMetrics'

function buildRouteDimensions(overrides: Partial<QueryRouteDimensions> = {}): QueryRouteDimensions {
  return {
    events_total: 0,
    route_kind: {},
    generation_engine: {},
    strategy_selected_engine: {},
    strategy_mode: {},
    strategy_policy: {},
    strategy_risk_level: {},
    strategy_risk_score_total: 0,
    strategy_risk_score_avg: 0,
    strategy_risk_score_max: 0,
    strict_json_mode: {},
    generation_decision_total: 0,
    fallback_count_total: 0,
    fallback_count_avg: 0,
    fallback_count_max: 0,
    repair_used: 0,
    generation_retry_reason: {},
    validation_issue_bucket: {},
    llm_empty_response_retry: 0,
    repair_guard_blocked: 0,
    repair_short_circuit: 0,
    repair_short_circuit_reason: {},
    schema_link_fallback_total: 0,
    schema_link_fallback_reason: {},
    schema_link_fallback_rate: 0,
    sql_generation_fallback_total: 0,
    sql_generation_fallback_reason: {},
    sql_generation_fallback_rate: 0,
    final_answer_fallback_total: 0,
    final_answer_fallback_reason: {},
    final_answer_fallback_rate: 0,
    window_seconds: 1800,
    last_updated: 1,
    ...overrides,
  }
}

describe('queryMetrics helpers', () => {
  it('uses project-scoped query keys for project switching', () => {
    expect(isProjectScopedMetricsEnabled(undefined)).toBe(false)
    expect(isProjectScopedMetricsEnabled(0)).toBe(false)
    expect(isProjectScopedMetricsEnabled(1)).toBe(true)

    expect(queryMetricsQueryKey(undefined)).toEqual(['query-metrics', null])
    expect(queryMetricsQueryKey(1)).toEqual(['query-metrics', 1])
    expect(queryMetricsQueryKey(2)).toEqual(['query-metrics', 2])
    expect(queryMetricsQueryKey(1)).not.toEqual(queryMetricsQueryKey(2))
  })

  it('normalizes rows and sorts by total desc', () => {
    const rows = normalizeQueryMetricsRows({
      mysql: {
        total: 3,
        success: 2,
        warning: 1,
        error: 0,
        timeout: 0,
        avg_ms: 100.336,
        p95_ms: 190.994,
        avg_rows: 12.34,
        last_updated: 1,
      },
      postgresql: {
        total: 5,
        success: 4,
        warning: 0,
        error: 1,
        timeout: 0,
        avg_ms: 40,
        p95_ms: 70,
        avg_rows: 3.456,
        last_updated: 1,
      },
    })

    expect(rows.map((item) => item.datasourceType)).toEqual(['postgresql', 'mysql'])
    const first = rows[0]
    const second = rows[1]
    if (!first || !second) throw new Error('Expected two rows')

    expect(first).toMatchObject({
      datasourceType: 'postgresql',
      total: 5,
      successRate: 80,
      avgMs: 40,
      p95Ms: 70,
      avgRows: 3.46,
    })
    expect(second).toMatchObject({
      datasourceType: 'mysql',
      total: 3,
      successRate: 66.67,
      avgMs: 100.34,
      p95Ms: 190.99,
      avgRows: 12.34,
    })
  })

  it('aggregates totals and weighted averages', () => {
    const rows = normalizeQueryMetricsRows({
      postgresql: {
        total: 2,
        success: 2,
        warning: 0,
        error: 0,
        timeout: 0,
        avg_ms: 10,
        p95_ms: 20,
        avg_rows: 5,
        last_updated: 1,
      },
      trino: {
        total: 3,
        success: 2,
        warning: 0,
        error: 1,
        timeout: 0,
        avg_ms: 40,
        p95_ms: 90,
        avg_rows: 8,
        last_updated: 1,
      },
    })

    expect(aggregateQueryMetrics(rows)).toEqual({
      total: 5,
      success: 4,
      warning: 0,
      error: 1,
      timeout: 0,
      successRate: 80,
      avgMs: 28,
      p95Ms: 90,
      avgRows: 6.8,
    })
  })

  it('returns no route alerts when counters are below thresholds', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      events_total: 10,
      validation_issue_bucket: { duplicate_alias: 1 },
      llm_empty_response_retry: 2,
    }))

    expect(alerts).toEqual([])
  })

  it('prioritizes critical route alerts before warnings', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      events_total: 20,
      generation_retry_reason: { empty_llm_content: 9 },
      validation_issue_bucket: { duplicate_alias: 6 },
      llm_empty_response_retry: 9,
      repair_guard_blocked: 1,
      repair_short_circuit: 2,
      repair_short_circuit_reason: { column_validation: 2 },
    }))

    expect(alerts).toEqual([
      { id: 'llm_empty_response_retry', level: 'critical', count: 9, threshold: 8 },
      { id: 'duplicate_alias', level: 'critical', count: 6, threshold: 5 },
      { id: 'repair_guard_blocked', level: 'warning', count: 1, threshold: 1 },
    ])
  })

  it('raises warning when repair short-circuit count is below baseline', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      events_total: 12,
      repair_guard_blocked: 4,
      repair_short_circuit: 1,
      repair_short_circuit_reason: { column_validation: 1 },
    }))

    expect(alerts).toEqual([
      { id: 'repair_guard_blocked', level: 'critical', count: 4, threshold: 3 },
      { id: 'repair_short_circuit_low', level: 'warning', count: 1, threshold: 2 },
    ])
  })

  it('raises critical when repair short-circuit count is far below baseline', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      events_total: 20,
      repair_guard_blocked: 8,
      repair_short_circuit: 1,
      repair_short_circuit_reason: { column_validation: 1 },
    }))

    expect(alerts).toEqual([
      { id: 'repair_guard_blocked', level: 'critical', count: 8, threshold: 3 },
      { id: 'repair_short_circuit_low', level: 'critical', count: 1, threshold: 2 },
    ])
  })

  it('raises fallback-rate warnings when decision volume is sufficient', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 20,
      schema_link_fallback_total: 3,
      sql_generation_fallback_total: 4,
      final_answer_fallback_total: 2,
    }))

    expect(alerts).toEqual([
      { id: 'sql_generation_fallback_high', level: 'warning', count: 4, threshold: 4 },
      { id: 'schema_link_fallback_high', level: 'warning', count: 3, threshold: 3 },
      { id: 'final_answer_fallback_high', level: 'warning', count: 2, threshold: 2 },
    ])
  })

  it('raises fallback-rate critical alerts on severe fallback rates', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 40,
      schema_link_fallback_total: 12,
      sql_generation_fallback_total: 15,
      final_answer_fallback_total: 11,
    }))

    expect(alerts).toEqual([
      { id: 'sql_generation_fallback_high', level: 'critical', count: 15, threshold: 14 },
      { id: 'schema_link_fallback_high', level: 'critical', count: 12, threshold: 10 },
      { id: 'final_answer_fallback_high', level: 'critical', count: 11, threshold: 10 },
    ])
  })

  it('does not raise fallback-rate alerts when decision volume is too low', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 7,
      schema_link_fallback_total: 7,
      sql_generation_fallback_total: 7,
      final_answer_fallback_total: 7,
    }))

    expect(alerts).toEqual([])
  })

  it('derives fallback alert counts from rates when totals are missing', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 0,
      generation_engine: { fewshot_cot: 20 },
      schema_link_fallback_total: 0,
      schema_link_fallback_rate: 0.2,
    }))

    expect(alerts).toEqual([
      { id: 'schema_link_fallback_high', level: 'warning', count: 4, threshold: 3 },
    ])
  })

  it('prefers fallback totals over rates when both exist', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 20,
      schema_link_fallback_total: 2,
      schema_link_fallback_rate: 0.9,
    }))

    expect(alerts).toEqual([])
  })

  it('raises strategy warning alerts when risk and decompose share are elevated', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 20,
      strategy_risk_level: { high: 6, medium: 10, low: 4 },
      strategy_policy: { risk_decompose_merge: 7, risk_consensus_fewshot: 8, risk_constrained_direct: 5 },
    }))

    expect(alerts).toEqual([
      { id: 'strategy_decompose_policy_high', level: 'warning', count: 7, threshold: 6 },
      { id: 'strategy_high_risk_rate', level: 'warning', count: 6, threshold: 5 },
    ])
  })

  it('raises strategy critical alerts when risk and decompose share are severe', () => {
    const alerts = evaluateRouteObservabilityAlerts(buildRouteDimensions({
      generation_decision_total: 20,
      strategy_risk_level: { high: 10, medium: 7, low: 3 },
      strategy_policy: { risk_decompose_merge: 12, risk_consensus_fewshot: 5, risk_constrained_direct: 3 },
    }))

    expect(alerts).toEqual([
      { id: 'strategy_decompose_policy_high', level: 'critical', count: 12, threshold: 11 },
      { id: 'strategy_high_risk_rate', level: 'critical', count: 10, threshold: 9 },
    ])
  })

  it('raises warning when llm http circuit has open keys', () => {
    const alerts = evaluateRouteObservabilityAlerts(
      buildRouteDimensions({ events_total: 5 }),
      {
        total_keys: 4,
        open_keys: 2,
        keys: {},
      },
    )

    expect(alerts).toEqual([
      { id: 'llm_http_circuit_open', level: 'warning', count: 2, threshold: 1 },
    ])
  })

  it('supports llm http circuit alert even when route dimensions are missing', () => {
    const alerts = evaluateRouteObservabilityAlerts(
      null,
      {
        total_keys: 3,
        open_keys: 3,
        keys: {
          'openai:https://api.openai.com/v1:gpt-4o': {
            state: 'open',
            remaining_open_seconds: 12,
            consecutive_failures: 0,
          },
        },
      },
    )

    expect(alerts).toEqual([
      { id: 'llm_http_circuit_open', level: 'critical', count: 3, threshold: 3 },
    ])
  })

  it('summarizes adaptive strategy routing counters and risk scores', () => {
    const summary = summarizeStrategyObservability(buildRouteDimensions({
      generation_decision_total: 0,
      generation_engine: { direct_llm: 8 },
      strategy_selected_engine: { fewshot_cot: 5, direct_llm: 3 },
      strategy_mode: { adaptive_risk: 7, legacy_tier: 1 },
      strategy_policy: { risk_consensus_fewshot: 4, risk_constrained_direct: 3, tier_default: 1 },
      strategy_risk_level: { medium: 5, low: 2, high: 1 },
      strategy_risk_score_total: 42,
      strategy_risk_score_max: 9,
    }))

    expect(summary.decisionTotal).toBe(8)
    expect(summary.riskScoreTotal).toBe(42)
    expect(summary.riskScoreAvg).toBe(5.25)
    expect(summary.riskScoreMax).toBe(9)
    expect(summary.selectedEngines[0]).toEqual(['fewshot_cot', 5])
    expect(summary.modes[0]).toEqual(['adaptive_risk', 7])
    expect(summary.policies[0]).toEqual(['risk_consensus_fewshot', 4])
    expect(summary.riskLevels[0]).toEqual(['medium', 5])
  })

  it('uses payload average risk score when totals are missing', () => {
    const summary = summarizeStrategyObservability(buildRouteDimensions({
      strategy_risk_score_total: 0,
      strategy_risk_score_avg: 3.67,
      strategy_risk_score_max: 6,
    }))

    expect(summary.decisionTotal).toBe(0)
    expect(summary.riskScoreAvg).toBe(3.67)
    expect(summary.riskScoreMax).toBe(6)
  })

  it('builds a trend point from route dimensions with decision volume', () => {
    const point = buildStrategyTrendPoint(
      buildRouteDimensions({
        generation_decision_total: 20,
        strategy_risk_level: { high: 6, medium: 10, low: 4 },
        strategy_policy: { risk_decompose_merge: 7, risk_consensus_fewshot: 8, risk_constrained_direct: 5 },
        strategy_mode: { adaptive_risk: 14, legacy_tier: 6 },
      }),
      12345,
    )

    expect(point).toEqual({
      capturedAtMs: 12345,
      decisionTotal: 20,
      riskScoreAvg: 0,
      highRiskRate: 0.3,
      decomposePolicyRate: 0.35,
      dominantMode: 'adaptive_risk',
      dominantPolicy: 'risk_consensus_fewshot',
    })
  })

  it('returns null trend point when there are no strategy decisions', () => {
    const point = buildStrategyTrendPoint(buildRouteDimensions({ generation_decision_total: 0 }), 1000)
    expect(point).toBeNull()
  })

  it('appends trend points with de-duplication and bounded history', () => {
    const first = {
      capturedAtMs: 1000,
      decisionTotal: 10,
      riskScoreAvg: 2,
      highRiskRate: 0.1,
      decomposePolicyRate: 0.2,
      dominantMode: 'adaptive_risk',
      dominantPolicy: 'risk_consensus_fewshot',
    }
    const duplicate = { ...first, capturedAtMs: 2000 }
    const next = {
      capturedAtMs: 3000,
      decisionTotal: 10,
      riskScoreAvg: 2.5,
      highRiskRate: 0.2,
      decomposePolicyRate: 0.3,
      dominantMode: 'adaptive_risk',
      dominantPolicy: 'risk_decompose_merge',
    }

    const withFirst = appendStrategyTrendPoint([], first, 3)
    const deduped = appendStrategyTrendPoint(withFirst, duplicate, 3)
    const withSecond = appendStrategyTrendPoint(deduped, next, 3)
    const bounded = appendStrategyTrendPoint(withSecond, { ...next, capturedAtMs: 4000, riskScoreAvg: 2.8 }, 2)

    expect(withFirst).toHaveLength(1)
    expect(deduped).toHaveLength(1)
    expect(withSecond).toHaveLength(2)
    expect(bounded).toHaveLength(2)
    expect(bounded[0]?.capturedAtMs).toBe(3000)
    expect(bounded[1]?.capturedAtMs).toBe(4000)
  })

  it('normalizes backend strategy trend history payloads', () => {
    const rawHistory: QueryStrategyTrendPoint[] = [
      {
        captured_at_unix: 3,
        decision_total: 12,
        risk_score_avg: 2.81,
        high_risk_rate: 0.23,
        decompose_policy_rate: 0.31,
        dominant_mode: 'Adaptive_Risk',
        dominant_policy: 'Risk_Consensus_Fewshot',
      },
      {
        captured_at_unix: 1,
        decision_total: 10,
        risk_score_avg: 1.5,
        high_risk_rate: 0.1,
        decompose_policy_rate: 0.2,
        dominant_mode: 'legacy_tier',
        dominant_policy: 'tier_default',
      },
      {
        captured_at_unix: 2,
        decision_total: 0,
        risk_score_avg: 9,
        high_risk_rate: 1,
        decompose_policy_rate: 1,
        dominant_mode: 'bad',
        dominant_policy: 'bad',
      },
      {
        captured_at_unix: Number.NaN,
        decision_total: 4,
        risk_score_avg: 1,
        high_risk_rate: 0.2,
        decompose_policy_rate: 0.2,
        dominant_mode: 'bad',
        dominant_policy: 'bad',
      },
    ]

    const normalized = normalizeStrategyTrendHistory(rawHistory, 3)

    expect(normalized).toEqual([
      {
        capturedAtMs: 1000,
        decisionTotal: 10,
        riskScoreAvg: 1.5,
        highRiskRate: 0.1,
        decomposePolicyRate: 0.2,
        dominantMode: 'legacy_tier',
        dominantPolicy: 'tier_default',
      },
      {
        capturedAtMs: 3000,
        decisionTotal: 12,
        riskScoreAvg: 2.81,
        highRiskRate: 0.23,
        decomposePolicyRate: 0.31,
        dominantMode: 'adaptive_risk',
        dominantPolicy: 'risk_consensus_fewshot',
      },
    ])
  })

  it('summarizes trend drift levels from strategy trend history', () => {
    const warningSummary = summarizeStrategyTrend([
      {
        capturedAtMs: 1000,
        decisionTotal: 20,
        riskScoreAvg: 2.0,
        highRiskRate: 0.1,
        decomposePolicyRate: 0.2,
        dominantMode: 'adaptive_risk',
        dominantPolicy: 'risk_consensus_fewshot',
      },
      {
        capturedAtMs: 4000,
        decisionTotal: 20,
        riskScoreAvg: 2.9,
        highRiskRate: 0.19,
        decomposePolicyRate: 0.31,
        dominantMode: 'legacy_tier',
        dominantPolicy: 'risk_decompose_merge',
      },
    ])
    const criticalSummary = summarizeStrategyTrend([
      {
        capturedAtMs: 1000,
        decisionTotal: 20,
        riskScoreAvg: 1.8,
        highRiskRate: 0.1,
        decomposePolicyRate: 0.2,
        dominantMode: 'adaptive_risk',
        dominantPolicy: 'risk_constrained_direct',
      },
      {
        capturedAtMs: 8000,
        decisionTotal: 20,
        riskScoreAvg: 3.7,
        highRiskRate: 0.3,
        decomposePolicyRate: 0.45,
        dominantMode: 'legacy_tier',
        dominantPolicy: 'risk_consensus_fewshot',
      },
      {
        capturedAtMs: 14000,
        decisionTotal: 20,
        riskScoreAvg: 4.4,
        highRiskRate: 0.4,
        decomposePolicyRate: 0.55,
        dominantMode: 'adaptive_risk',
        dominantPolicy: 'risk_decompose_merge',
      },
      {
        capturedAtMs: 20000,
        decisionTotal: 20,
        riskScoreAvg: 4.8,
        highRiskRate: 0.46,
        decomposePolicyRate: 0.62,
        dominantMode: 'legacy_tier',
        dominantPolicy: 'risk_constrained_direct',
      },
      {
        capturedAtMs: 26000,
        decisionTotal: 20,
        riskScoreAvg: 5.1,
        highRiskRate: 0.5,
        decomposePolicyRate: 0.68,
        dominantMode: 'adaptive_risk',
        dominantPolicy: 'risk_decompose_merge',
      },
    ])

    expect(warningSummary.driftLevel).toBe('warning')
    expect(warningSummary.modeSwitches).toBe(1)
    expect(warningSummary.policySwitches).toBe(1)
    expect(warningSummary.riskScoreDelta).toBe(0.9)
    expect(warningSummary.highRiskRateDelta).toBe(0.09)
    expect(criticalSummary.driftLevel).toBe('critical')
    expect(criticalSummary.modeSwitches).toBe(4)
    expect(criticalSummary.policySwitches).toBe(4)
    expect(criticalSummary.riskScoreDelta).toBe(3.3)
    expect(criticalSummary.highRiskRateDelta).toBe(0.4)
  })

  it('returns stable trend summary defaults for empty history', () => {
    const summary = summarizeStrategyTrend([])

    expect(summary).toEqual({
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
    })
  })
})
