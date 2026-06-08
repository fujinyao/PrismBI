'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { adminAuditLogsApi } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'
import { useI18nStore } from '@/stores/i18nStore'
import { AuditLogTable } from '@/components/admin/AuditLogTable'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { RequirePermission } from '@/components/providers/RequirePermission'

export default function AdminAuditPage() {
  return (
    <RequirePermission resource="audit_logs" action="read">
      <AdminAuditContent />
    </RequirePermission>
  )
}

function AdminAuditContent() {
  const t = useI18nStore((s) => s.t)
  const [eventType, setEventType] = useState<string | undefined>()
  const [userId, setUserId] = useState<number | undefined>()
  const [from, setFrom] = useState<string | undefined>()
  const [to, setTo] = useState<string | undefined>()
  const [error, setError] = useState<string | null>(null)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canExport = hasPermission('audit_logs', 'export')

  const {
    data: logs,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['audit-logs', eventType, userId, from, to],
    queryFn: () => adminAuditLogsApi.list({ event_type: eventType, user_id: userId, from, to }),
  })

  const handleExport = async (format: 'csv' | 'json') => {
    try {
      await adminAuditLogsApi.export(format)
    } catch {
      setError(t('admin.audit.failedToExport', 'Failed to export audit logs'))
    }
  }

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3">
          <Skeleton className="mb-2 h-10 w-full" />
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('admin.audit.failedToLoad', 'Failed to load audit logs')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-4 flex items-center justify-end">
        {canExport && (
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => handleExport('csv')}>
              {t('admin.audit.exportCsv', 'Export CSV')}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => handleExport('json')}>
              {t('admin.audit.exportJson', 'Export JSON')}
            </Button>
          </div>
        )}
      </div>

      <div className="mb-4 flex flex-wrap gap-4">
        <select
          value={eventType ?? ''}
          onChange={(e) => setEventType(e.target.value || undefined)}
          className="rounded border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="">{t('admin.audit.allEvents', 'All events')}</option>
          <option value="LOGIN">{t('admin.audit.eventLogin', 'Login')}</option>
          <option value="QUERY">{t('admin.audit.eventQuery', 'Query')}</option>
          <option value="EXPORT">{t('admin.audit.eventExport', 'Export')}</option>
          <option value="PERM_CHANGE">{t('admin.audit.eventPermChange', 'Permission Change')}</option>
        </select>
        <input
          type="date"
          value={from ?? ''}
          onChange={(e) => setFrom(e.target.value || undefined)}
          className="rounded border border-gray-300 px-3 py-2 text-sm"
        />
        <input
          type="date"
          value={to ?? ''}
          onChange={(e) => setTo(e.target.value || undefined)}
          className="rounded border border-gray-300 px-3 py-2 text-sm"
        />
      </div>

      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <AuditLogTable logs={logs?.items ?? []} loading={false} onExport={handleExport} canExport={canExport} />
    </div>
  )
}
