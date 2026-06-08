import { describe, expect, it } from 'vitest'
import { buildGeneralSettingsPayload } from './GeneralSettings'

describe('buildGeneralSettingsPayload', () => {
  it('builds one combined payload with normalized route observability fields', () => {
    const payload = buildGeneralSettingsPayload({
      language: 'en',
      timezone: 'UTC',
      dateFormat: 'YYYY-MM-DD',
      sessionTimeout: 60,
      requestTimeout: 90000,
      llmReadTimeout: 180,
      dbConnectTimeout: 15,
      routeObservabilityWindowMinutes: 2,
      routeObservabilityPersistEnabled: true,
      routeObservabilityPersistIntervalSeconds: 7200,
      routeObservabilityPersistEventDelta: 0,
      modelRefCaseSensitive: false,
    })

    expect(payload).toEqual({
      language: 'en',
      timezone: 'UTC',
      date_format: 'YYYY-MM-DD',
      session_timeout: 60,
      request_timeout_ms: 90000,
      llm_read_timeout_s: 180,
      db_connect_timeout_s: 15,
      route_observability_window_minutes: 5,
      route_observability_persist_enabled: true,
      route_observability_persist_interval_seconds: 3600,
      route_observability_persist_event_delta: 1,
      model_ref_case_sensitive: false,
    })
  })

  it('falls back to defaults for non-finite route observability inputs', () => {
    const payload = buildGeneralSettingsPayload({
      language: 'zh',
      timezone: 'Asia/Shanghai',
      dateFormat: 'YYYY-MM-DD',
      sessionTimeout: 120,
      requestTimeout: 120000,
      llmReadTimeout: 120,
      dbConnectTimeout: 10,
      routeObservabilityWindowMinutes: Number.NaN,
      routeObservabilityPersistEnabled: false,
      routeObservabilityPersistIntervalSeconds: Number.POSITIVE_INFINITY,
      routeObservabilityPersistEventDelta: Number.NaN,
      modelRefCaseSensitive: true,
    })

    expect(payload.route_observability_window_minutes).toBe(30)
    expect(payload.route_observability_persist_interval_seconds).toBe(30)
    expect(payload.route_observability_persist_event_delta).toBe(20)
    expect(payload.route_observability_persist_enabled).toBe(false)
    expect(payload.model_ref_case_sensitive).toBe(true)
  })
})
