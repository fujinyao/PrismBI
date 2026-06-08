'use client'

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { settingsApi } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'
import { cn } from '@/lib/utils'
import { formatNumber as formatLocaleNumber, useI18nStore } from '@/stores/i18nStore'

interface SettingsAuditPanelProps {
  className?: string
}

const SETTINGS_AUDIT_SCOPE_OPTIONS = [
  'all',
  'general',
  'timeouts',
  'router',
  'ask',
  'security',
  'llm',
  'llm_advanced',
  'llm_whitelist',
  'theme',
  'branding',
  'recommendations',
] as const

const LATEST_LIMIT = 8

function formatCount(value: number, locale: string): string {
  return formatLocaleNumber(value, locale, { maximumFractionDigits: 0 })
}

function prettifyScope(scope: string): string {
  return scope
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function topChangedField(counter: Record<string, number> | undefined): [string, number] | null {
  const entries = Object.entries(counter ?? {})
    .map(([field, count]) => [String(field), Number.isFinite(Number(count)) ? Number(count) : 0] as [string, number])
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  return entries[0] ?? null
}

function formatAuditTime(raw: string | null | undefined, locale: string): string {
  if (!raw) return '-'
  const parsed = new Date(raw)
  if (Number.isNaN(parsed.getTime())) return String(raw)
  return parsed.toLocaleString(locale)
}

export function SettingsAuditPanel({ className }: SettingsAuditPanelProps) {
  const t = useI18nStore((s) => s.t)
  const locale = useI18nStore((s) => s.locale)
  const [scope, setScope] = useState<(typeof SETTINGS_AUDIT_SCOPE_OPTIONS)[number]>('all')
  const [latestOffset, setLatestOffset] = useState(0)

  const summaryQuery = useQuery({
    queryKey: ['settings', 'audit-summary', scope, latestOffset],
    queryFn: () => settingsApi.auditSummary({
      scope: scope === 'all' ? undefined : scope,
      max_events: 3000,
      latest_limit: LATEST_LIMIT,
      latest_offset: latestOffset,
    }),
    staleTime: 30000,
  })

  const matchedEvents = useMemo(() => {
    const value = Number(summaryQuery.data?.matched_events)
    if (!Number.isFinite(value) || value < 0) return 0
    return Math.trunc(value)
  }, [summaryQuery.data?.matched_events])

  const scannedEvents = useMemo(() => {
    const value = Number(summaryQuery.data?.scanned_events)
    if (!Number.isFinite(value) || value < 0) return 0
    return Math.trunc(value)
  }, [summaryQuery.data?.scanned_events])

  const byScopeRows = useMemo(
    () => Object.entries(summaryQuery.data?.by_scope ?? {})
      .map(([scopeName, payload]) => {
        const events = Number.isFinite(Number(payload?.events)) ? Number(payload?.events) : 0
        const changedField = topChangedField(payload?.changed_fields)
        return {
          scope: scopeName,
          events,
          changedField,
          lastUpdated: payload?.last_updated ?? null,
        }
      })
      .sort((a, b) => b.events - a.events || a.scope.localeCompare(b.scope)),
    [summaryQuery.data?.by_scope],
  )

  const latestRows = summaryQuery.data?.latest ?? []
  const canPrevious = latestOffset > 0
  const canNext = latestOffset + latestRows.length < matchedEvents
  const pageStart = matchedEvents === 0 ? 0 : latestOffset + 1
  const pageEnd = latestOffset + latestRows.length

  return (
    <Card className={cn('rounded-xl', className)}>
      <CardHeader className="mb-3 items-start gap-3">
        <div>
          <CardTitle className="text-base">{t('settings.audit.title', 'Recent Settings Changes')}</CardTitle>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {t('settings.audit.description', 'Audit summary of setting updates by scope and changed fields.')}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400" htmlFor="settings-audit-scope-filter">
            {t('settings.audit.scopeFilter', 'Scope')}
          </label>
          <select
            id="settings-audit-scope-filter"
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as (typeof SETTINGS_AUDIT_SCOPE_OPTIONS)[number])
              setLatestOffset(0)
            }}
            className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
          >
            {SETTINGS_AUDIT_SCOPE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === 'all'
                  ? t('settings.audit.scope.all', 'All')
                  : t(`settings.audit.scope.${option}`, prettifyScope(option))}
              </option>
            ))}
          </select>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              void summaryQuery.refetch()
            }}
            loading={summaryQuery.isFetching}
          >
            {t('settings.audit.refresh', 'Refresh')}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {summaryQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : summaryQuery.isError ? (
          <div className="rounded-md border border-error/30 bg-error/10 px-3 py-2 text-xs text-error dark:border-error-400/40 dark:bg-error-400/10 dark:text-error-200">
            <p>{t('settings.audit.loadFailed', 'Failed to load settings audit summary.')}</p>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                void summaryQuery.refetch()
              }}
              className="mt-2"
            >
              {t('common.retry', 'Retry')}
            </Button>
          </div>
        ) : matchedEvents === 0 ? (
          <p className="rounded-md border border-dashed border-gray-200 px-3 py-4 text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
            {t('settings.audit.empty', 'No matching settings audit events in the selected range.')}
          </p>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
              <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('settings.audit.scannedEvents', 'Scanned Events')}</p>
                <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{formatCount(scannedEvents, locale)}</p>
              </div>
              <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('settings.audit.matchedEvents', 'Matched Events')}</p>
                <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{formatCount(matchedEvents, locale)}</p>
              </div>
              <div className="col-span-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800/60">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('settings.audit.latestWindow', 'Latest Window')}</p>
                <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  {formatCount(pageStart, locale)}-{formatCount(pageEnd, locale)} / {formatCount(matchedEvents, locale)}
                </p>
              </div>
            </div>

            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-md border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900/60">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                  {t('settings.audit.scopeSummary', 'Scope Summary')}
                </p>
                <div className="space-y-2">
                  {byScopeRows.map((item) => (
                    <div key={item.scope} className="rounded-md border border-gray-200 px-3 py-2 text-xs dark:border-gray-700">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-gray-900 dark:text-gray-100">
                          {t(`settings.audit.scope.${item.scope}`, prettifyScope(item.scope))}
                        </span>
                        <span className="text-gray-500 dark:text-gray-400">{formatCount(item.events, locale)}</span>
                      </div>
                      <p className="mt-1 truncate text-gray-500 dark:text-gray-400">
                        {item.changedField
                          ? `${item.changedField[0]} (${formatCount(item.changedField[1], locale)})`
                          : t('settings.audit.noFieldChanges', 'No field changes recorded')}
                      </p>
                      <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
                        {formatAuditTime(item.lastUpdated, locale)}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-md border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900/60">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                  {t('settings.audit.latest', 'Latest Events')}
                </p>
                <div className="space-y-2">
                  {latestRows.map((item, index) => (
                    <div key={`${item.event_type}-${item.created_at || index}-${index}`} className="rounded-md border border-gray-200 px-3 py-2 text-xs dark:border-gray-700">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-gray-900 dark:text-gray-100">
                          {t(`settings.audit.scope.${item.scope}`, prettifyScope(item.scope))}
                        </span>
                        <span className="text-gray-500 dark:text-gray-400">{item.action || '-'}</span>
                      </div>
                      <p className="mt-1 truncate text-gray-500 dark:text-gray-400">
                        {(item.changed_fields && item.changed_fields.length > 0)
                          ? item.changed_fields.join(', ')
                          : t('settings.audit.noFieldChanges', 'No field changes recorded')}
                      </p>
                      <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
                        {formatAuditTime(item.created_at, locale)}
                      </p>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-end gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={!canPrevious}
                    onClick={() => setLatestOffset((prev) => Math.max(0, prev - LATEST_LIMIT))}
                  >
                    {t('settings.audit.prev', 'Previous')}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={!canNext}
                    onClick={() => setLatestOffset((prev) => prev + LATEST_LIMIT)}
                  >
                    {t('settings.audit.next', 'Next')}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
