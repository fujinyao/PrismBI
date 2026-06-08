import { describe, expect, it } from 'vitest'
import type { QueryRouteDimensions } from '@/lib/api'
import {
  aggregateQueryMetrics,
  evaluateRouteObservabilityAlerts,
  isProjectScopedMetricsEnabled,
  normalizeQueryMetricsRows,
  queryMetricsQueryKey,
} from '@/lib/queryMetrics'

function buildRouteDimensions(overrides: Partial<QueryRouteDimensions> = {}): QueryRouteDimensions {
  return {
    events_total: 0,
    route_kind: {},
    generation_engine: {},
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
})
