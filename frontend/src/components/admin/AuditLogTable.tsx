'use client'

import { useState, useRef } from 'react'
import { Button } from '@/components/ui/Button'
import { Tag } from '@/components/ui/Tag'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { useI18nStore } from '@/stores/i18nStore'
import type { AuditLog } from '@/lib/api'

interface AuditLogTableProps {
  logs: AuditLog[]
  loading: boolean
  onExport: (format: 'csv' | 'json') => void
  canExport?: boolean
  eventTypeFilter?: string
  onEventTypeFilterChange?: (type: string) => void
  dateFilter?: { start: string; end: string }
  onDateFilterChange?: (dates: { start: string; end: string }) => void
}

const actionColors: Record<string, 'info' | 'success' | 'warning' | 'error'> = {
  CREATE: 'success',
  UPDATE: 'info',
  DELETE: 'error',
  LOGIN: 'info',
  LOGOUT: 'warning',
  EXPORT: 'info',
}

export function AuditLogTable({
  logs,
  loading,
  onExport,
  canExport = true,
  eventTypeFilter,
  onEventTypeFilterChange,
  dateFilter,
  onDateFilterChange,
}: AuditLogTableProps) {
  const t = useI18nStore((s) => s.t)
  const [searchQuery, setSearchQuery] = useState('')
  const searchComposing = useRef(false)

  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (!logs || logs.length === 0) {
    return (
      <EmptyState
        message={t('auditLog.noResults', 'No audit logs found.')}
        action={{ label: t('auditLog.refresh', 'Refresh'), onClick: () => {} }}
      />
    )
  }

  const filtered = logs.filter((log) =>
    searchQuery
      ? (log.action ?? '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (log.resource_type ?? '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        String(log.user_id ?? '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.event_type.toLowerCase().includes(searchQuery.toLowerCase())
      : true,
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder={t('auditLog.search', 'Search logs...')}
            onInput={(e) => { if (!searchComposing.current && !(e.nativeEvent as InputEvent).isComposing) setSearchQuery((e.target as HTMLInputElement).value) }}
            onCompositionStart={() => { searchComposing.current = true }}
            onCompositionEnd={(e) => { searchComposing.current = false; setSearchQuery((e.target as HTMLInputElement).value) }}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800"
          />
          {onEventTypeFilterChange && (
            <select
              value={eventTypeFilter || ''}
              onChange={(e) => onEventTypeFilterChange(e.target.value)}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800"
            >
              <option value="">{t('auditLog.allEvents', 'All Events')}</option>
              <option value="CREATE">{t('auditLog.eventCreate', 'Create')}</option>
              <option value="UPDATE">{t('auditLog.eventUpdate', 'Update')}</option>
              <option value="DELETE">{t('auditLog.eventDelete', 'Delete')}</option>
              <option value="LOGIN">{t('auditLog.eventLogin', 'Login')}</option>
              <option value="LOGOUT">{t('auditLog.eventLogout', 'Logout')}</option>
            </select>
          )}
          {onDateFilterChange && (
            <input
              type="date"
              value={dateFilter?.start || ''}
              onChange={(e) =>
                onDateFilterChange({
                  start: e.target.value,
                  end: dateFilter?.end || '',
                })
              }
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800"
            />
          )}
        </div>
        {canExport && (
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => onExport('csv')}>
              {t('auditLog.exportCSV', 'Export CSV')}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => onExport('json')}>
              {t('auditLog.exportJSON', 'Export JSON')}
            </Button>
          </div>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('auditLog.time', 'Time')}</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('auditLog.user', 'User')}</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('auditLog.action', 'Action')}</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('auditLog.resource', 'Resource')}</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('auditLog.details', 'Details')}</th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('auditLog.ip', 'IP')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {filtered.map((log) => (
              <tr key={log.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {log.created_at ? new Date(log.created_at).toLocaleString() : '-'}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900 dark:text-gray-100">
                  {log.user_id ?? '-'}
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  <Tag variant={actionColors[log.event_type] || 'info'}>{log.event_type}</Tag>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {log.resource_type ?? '-'}
                  {log.resource_id && <span className="ml-1 text-xs text-gray-400">#{log.resource_id}</span>}
                </td>
                <td className="max-w-xs truncate px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                  {typeof log.detail === 'string' ? log.detail : JSON.stringify(log.detail ?? {})}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500 dark:text-gray-500">
                  {log.ip_address ?? '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-xs text-gray-500">
        {t('auditLog.showing', 'Showing {0} of {1} log entries').replace('{0}', String(filtered.length)).replace('{1}', String(logs.length))}
      </div>
    </div>
  )
}
