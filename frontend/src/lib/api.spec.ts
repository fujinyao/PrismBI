import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, recommendationsApi, settingsApi, type RecommendationBootstrapStatus, type RouterRuntimeSnapshot } from '@/lib/api'

describe('settingsApi', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls router runtime reload endpoint', async () => {
    const runtime: RouterRuntimeSnapshot = {
      max_sql_rows: 1000,
      sql_route_v2_enabled: true,
    }
    const postSpy = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ success: true, runtime })

    const result = await settingsApi.routerRuntimeReload()

    expect(postSpy).toHaveBeenCalledWith('/settings/router/reload', {})
    expect(result).toEqual({ success: true, runtime })
  })

  it('calls settings audit summary endpoint', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue({
        scanned_events: 3,
        matched_events: 2,
        scope: 'general',
        latest_offset: 5,
        latest_limit: 10,
        by_scope: {},
        latest: [],
      })

    const result = await settingsApi.auditSummary({ scope: 'general', latest_offset: 5, latest_limit: 10, max_events: 500 })

    expect(getSpy).toHaveBeenCalledWith('/settings/audit-summary', { scope: 'general', latest_offset: 5, latest_limit: 10, max_events: 500 })
    expect(result.scanned_events).toBe(3)
    expect(result.matched_events).toBe(2)
  })
})

describe('recommendationsApi', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls bootstrap status endpoint', async () => {
    const payload: RecommendationBootstrapStatus = {
      project_id: 9,
      status: 'running',
      is_bootstrapping: true,
      ready: false,
      recommendation_count: 0,
      active_recommendations: 0,
      error: null,
      started_at: null,
      finished_at: null,
      updated_at: null,
    }
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue(payload)

    const result = await recommendationsApi.bootstrapStatus(9)

    expect(getSpy).toHaveBeenCalledWith('/recommendations/9/bootstrap-status')
    expect(result).toEqual(payload)
  })
})
