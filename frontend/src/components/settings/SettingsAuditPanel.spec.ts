import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const hoisted = vi.hoisted(() => ({
  buttonProps: [] as Array<{ children?: React.ReactNode; onClick?: () => void; disabled?: boolean }>,
  useStateMock: vi.fn(),
}))

vi.mock('react', async () => {
  const actual = await vi.importActual<typeof import('react')>('react')
  return {
    ...actual,
    useState: <S,>(initial: S | (() => S)) => hoisted.useStateMock(initial, actual.useState),
  }
})

vi.mock('@tanstack/react-query', () => ({
  useQuery: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  settingsApi: {
    auditSummary: vi.fn(),
  },
}))

vi.mock('@/stores/i18nStore', () => {
  const dict: Record<string, string> = {
    'settings.audit.title': 'Recent Settings Changes',
    'settings.audit.description': 'Audit summary of setting updates by scope and changed fields.',
    'settings.audit.scopeFilter': 'Scope',
    'settings.audit.scope.all': 'All',
    'settings.audit.scope.general': 'General',
    'settings.audit.refresh': 'Refresh',
    'settings.audit.loadFailed': 'Failed to load settings audit summary.',
    'settings.audit.empty': 'No matching settings audit events in the selected range.',
    'settings.audit.prev': 'Previous',
    'settings.audit.next': 'Next',
    'settings.audit.noFieldChanges': 'No field changes recorded',
    'common.retry': 'Retry',
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

vi.mock('@/components/ui/Button', () => ({
  Button: (props: { children?: React.ReactNode; onClick?: () => void; disabled?: boolean }) => {
    hoisted.buttonProps.push(props)
    return React.createElement('button', { disabled: props.disabled }, props.children)
  },
}))

import { useQuery } from '@tanstack/react-query'
import { settingsApi, type SettingsAuditSummary } from '@/lib/api'
import { SettingsAuditPanel } from './SettingsAuditPanel'

const useQueryMock = vi.mocked(useQuery)
const auditSummaryMock = vi.mocked(settingsApi.auditSummary)

function summaryPayload(overrides: Partial<SettingsAuditSummary> = {}): SettingsAuditSummary {
  return {
    scanned_events: 12,
    matched_events: 12,
    scope: null,
    latest_offset: 0,
    latest_limit: 8,
    by_scope: {
      general: {
        events: 12,
        last_updated: '2026-01-01T00:00:00Z',
        changed_fields: {
          request_timeout_ms: 9,
        },
        actions: {
          update: 12,
        },
      },
    },
    latest: [
      {
        event_type: 'SETTINGS_GENERAL_UPDATE',
        scope: 'general',
        user_id: 1,
        resource_id: 'general',
        action: 'update',
        created_at: '2026-01-01T00:00:00Z',
        changed_fields: ['request_timeout_ms'],
      },
    ],
    ...overrides,
  }
}

describe('SettingsAuditPanel', () => {
  beforeEach(() => {
    hoisted.buttonProps = []
    hoisted.useStateMock.mockReset()
    hoisted.useStateMock.mockImplementation(<S,>(initial: S | (() => S), fallback: typeof React.useState<S>) => fallback(initial))
    useQueryMock.mockReset()
    auditSummaryMock.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uses scope and latest offset when requesting summary data', () => {
    const setScope = vi.fn()
    const setLatestOffset = vi.fn()
    hoisted.useStateMock
      .mockImplementationOnce(() => ['general', setScope] as never)
      .mockImplementationOnce(() => [8, setLatestOffset] as never)

    let observedQueryKey: readonly unknown[] | undefined
    useQueryMock.mockImplementation((options) => {
      const queryOptions = options as { queryKey?: readonly unknown[]; queryFn?: () => unknown }
      observedQueryKey = queryOptions.queryKey
      void queryOptions.queryFn?.()
      return {
        data: summaryPayload({ scope: 'general', latest_offset: 8 }),
        isLoading: false,
        isError: false,
        isFetching: false,
        refetch: vi.fn(),
      } as never
    })

    auditSummaryMock.mockResolvedValue(summaryPayload())
    renderToStaticMarkup(React.createElement(SettingsAuditPanel))

    expect(observedQueryKey).toEqual(['settings', 'audit-summary', 'general', 8])
    expect(auditSummaryMock).toHaveBeenCalledWith({
      scope: 'general',
      max_events: 3000,
      latest_limit: 8,
      latest_offset: 8,
    })
  })

  it('updates latest offset with previous/next handlers', () => {
    const setScope = vi.fn()
    const setLatestOffset = vi.fn()
    hoisted.useStateMock
      .mockImplementationOnce(() => ['all', setScope] as never)
      .mockImplementationOnce(() => [8, setLatestOffset] as never)

    useQueryMock.mockReturnValue({
      data: summaryPayload({
        matched_events: 30,
        latest: Array.from({ length: 8 }).map((_, index) => ({
          event_type: `SETTINGS_GENERAL_UPDATE_${index}`,
          scope: 'general',
          user_id: 1,
          resource_id: 'general',
          action: 'update',
          created_at: '2026-01-01T00:00:00Z',
          changed_fields: ['request_timeout_ms'],
        })),
      }),
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never)

    renderToStaticMarkup(React.createElement(SettingsAuditPanel))

    const previousButton = hoisted.buttonProps.find((button) => button.children === 'Previous')
    const nextButton = hoisted.buttonProps.find((button) => button.children === 'Next')
    expect(previousButton).toBeDefined()
    expect(nextButton).toBeDefined()
    expect(previousButton?.disabled).toBe(false)
    expect(nextButton?.disabled).toBe(false)

    nextButton?.onClick?.()
    previousButton?.onClick?.()

    expect(setLatestOffset).toHaveBeenCalledTimes(2)
    const nextUpdater = setLatestOffset.mock.calls[0]?.[0] as ((value: number) => number) | undefined
    const previousUpdater = setLatestOffset.mock.calls[1]?.[0] as ((value: number) => number) | undefined
    expect(nextUpdater).toBeTypeOf('function')
    expect(previousUpdater).toBeTypeOf('function')
    expect(nextUpdater?.(8)).toBe(16)
    expect(previousUpdater?.(8)).toBe(0)
  })

  it('shows empty state when no events match current filter', () => {
    useQueryMock.mockReturnValue({
      data: summaryPayload({ matched_events: 0, latest: [] }),
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never)

    const html = renderToStaticMarkup(React.createElement(SettingsAuditPanel))

    expect(html).toContain('No matching settings audit events in the selected range.')
  })

  it('retries loading when summary query errors', () => {
    const refetch = vi.fn()
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      isFetching: false,
      refetch,
    } as never)

    const html = renderToStaticMarkup(React.createElement(SettingsAuditPanel))
    expect(html).toContain('Failed to load settings audit summary.')

    const retryButton = hoisted.buttonProps.find((button) => button.children === 'Retry')
    expect(retryButton).toBeDefined()

    retryButton?.onClick?.()
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
