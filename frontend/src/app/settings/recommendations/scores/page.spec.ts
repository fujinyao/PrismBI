import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hoisted = vi.hoisted(() => ({
  lastErrorToastProps: null as null | {
    message?: string
    onRetry?: () => void
    onClose?: () => void
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: vi.fn(),
}))

vi.mock('@/stores/projectStore', () => ({
  useProjectStore: (selector: (state: { currentProject: { id: number } | null }) => unknown) =>
    selector({ currentProject: { id: 1 } }),
}))

vi.mock('@/stores/i18nStore', () => {
  const dict: Record<string, string> = {
    'recommendation.failedToLoadScores': 'Failed to load score history',
    'recommendation.failedToLoadRouteSignals': 'Failed to load route signals',
    'recommendation.routeSignals.title': 'Route Signals',
    'recommendation.routeSignals.routeKind': 'Route Kind',
    'recommendation.routeSignals.events': 'Events',
    'recommendation.routeSignals.share': 'Share',
    'recommendation.routeSignals.routeKind.single_duckdb': 'Single DuckDB',
    'recommendation.routeSignals.routeKind.cross_source': 'Cross Source',
  }

  return {
    useI18nStore: (selector: (state: { t: (key: string, fallback?: string) => string; locale: string }) => unknown) =>
      selector({
        t: (key: string, fallback?: string) => dict[key] ?? fallback ?? key,
        locale: 'en',
      }),
    formatNumber: (value: number, locale?: string, options?: Intl.NumberFormatOptions) =>
      new Intl.NumberFormat(locale || 'en', options).format(value),
  }
})

vi.mock('@/components/recommendation/ScoreHistory', () => ({
  ScoreHistory: () => React.createElement('div', null, 'score-history'),
}))

vi.mock('@/components/ui/Skeleton', () => ({
  Skeleton: () => React.createElement('div', null, 'skeleton'),
}))

vi.mock('@/components/ui/EmptyState', () => ({
  EmptyState: () => React.createElement('div', null, 'empty-state'),
}))

vi.mock('@/components/ui/ErrorToast', () => ({
  ErrorToast: (props: { message?: string; onRetry?: () => void; onClose?: () => void }) => {
    hoisted.lastErrorToastProps = props
    return React.createElement('div', null, 'error-toast')
  },
}))

import { useQuery } from '@tanstack/react-query'
import RecommendationScoresPage from './page'

describe('RecommendationScoresPage route signal breakdown', () => {
  const useQueryMock = vi.mocked(useQuery)

  let ratingsPayload: unknown
  let statisticsPayload: unknown
  let ratingsIsError: boolean
  let statisticsIsError: boolean
  let statisticsIsLoading: boolean
  let ratingsRefetchMock: ReturnType<typeof vi.fn>
  let statisticsRefetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    hoisted.lastErrorToastProps = null
    ratingsPayload = {
      ratings: [{ id: 1, score: 4, source: 'schema', reason: 'baseline' }],
    }
    statisticsPayload = {
      route_signals: {
        available: true,
        project_id: 1,
        dominant_route_kind: 'single_duckdb',
        mixed_ratio: 0.5,
        avg_metadata_clause_count: 1.25,
        sql_success_rate: 0.8,
        route_kind_counts: {
          single_duckdb: 3,
          cross_source: 1,
        },
      },
    }
    ratingsIsError = false
    statisticsIsError = false
    statisticsIsLoading = false
    ratingsRefetchMock = vi.fn()
    statisticsRefetchMock = vi.fn()

    useQueryMock.mockImplementation((args: { queryKey?: readonly unknown[] }) => {
      const queryName = Array.isArray(args?.queryKey) ? String(args.queryKey[0]) : ''
      if (queryName === 'recommendation-ratings') {
        return {
          data: ratingsPayload,
          isLoading: false,
          isError: ratingsIsError,
          refetch: ratingsRefetchMock,
        } as never
      }
      if (queryName === 'recommendation-statistics') {
        return {
          data: statisticsPayload,
          isLoading: statisticsIsLoading,
          isError: statisticsIsError,
          refetch: statisticsRefetchMock,
        } as never
      }
      return {
        data: undefined,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      } as never
    })
  })

  it('renders route kind rows with sorted event shares', () => {
    const html = renderToStaticMarkup(React.createElement(RecommendationScoresPage))

    expect(html).toContain('Route Kind')
    expect(html).toContain('Events')
    expect(html).toContain('Share')
    expect(html).toContain('Single DuckDB')
    expect(html).toContain('Cross Source')
    expect(html).toContain('75%')
    expect(html).toContain('25%')

    expect(html.indexOf('Single DuckDB')).toBeLessThan(html.indexOf('Cross Source'))
  })

  it('hides breakdown table when route kind counts are empty', () => {
    statisticsPayload = {
      route_signals: {
        available: true,
        project_id: 1,
        dominant_route_kind: 'single_duckdb',
        mixed_ratio: 0.5,
        avg_metadata_clause_count: 1.25,
        sql_success_rate: 0.8,
        route_kind_counts: {},
      },
    }

    const html = renderToStaticMarkup(React.createElement(RecommendationScoresPage))

    expect(html).not.toContain('Route Kind')
    expect(html).not.toContain('Share')
  })

  it('shows route signal error message when statistics query fails', () => {
    statisticsPayload = undefined
    statisticsIsError = true

    const html = renderToStaticMarkup(React.createElement(RecommendationScoresPage))

    expect(html).toContain('Failed to load route signals')
    expect(html).not.toContain('Route Kind')
    expect(html).not.toContain('Share')
  })

  it('shows loading marker while statistics query is fetching', () => {
    statisticsPayload = undefined
    statisticsIsLoading = true

    const html = renderToStaticMarkup(React.createElement(RecommendationScoresPage))

    expect(html).toContain('Loading...')
    expect(html).not.toContain('Failed to load route signals')
  })

  it('retries ratings and statistics queries from score error toast', () => {
    ratingsPayload = undefined
    ratingsIsError = true

    renderToStaticMarkup(React.createElement(RecommendationScoresPage))

    expect(hoisted.lastErrorToastProps?.message).toBe('Failed to load score history')
    expect(typeof hoisted.lastErrorToastProps?.onRetry).toBe('function')

    hoisted.lastErrorToastProps?.onRetry?.()

    expect(ratingsRefetchMock).toHaveBeenCalledTimes(1)
    expect(statisticsRefetchMock).toHaveBeenCalledTimes(1)
  })
})
