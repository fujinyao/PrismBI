'use client'

import { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiHistoryApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { Table, type Column } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'

export default function ApiHistoryPage() {
  const t = useI18nStore((s) => s.t)
  const [search, setSearch] = useState('')
  const searchComposing = useRef(false)
  const [methodFilter, setMethodFilter] = useState<string | undefined>()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const pageSize = 20

  const {
    data: historyData,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['api-history', search, methodFilter, statusFilter, page, pageSize],
    queryFn: () =>
      apiHistoryApi.list({
        search: search || undefined,
        method: methodFilter,
        status_code: statusFilter ? Number(statusFilter) : undefined,
        page,
        page_size: pageSize,
      }),
  })

  const history = (historyData as any)?.items ?? []
  const total = (historyData as any)?.total ?? 0
  const totalPages = Math.ceil(total / pageSize)

  const columns: Column<any>[] = [
    { key: 'created_at', header: t('apiHistory.timestamp', 'Timestamp'), render: (item: any) => (
      <span className="font-mono text-xs">{item.created_at ?? item.createdAt ?? '-'}</span>
    )},
    { key: 'method', header: t('apiHistory.method', 'Method'), render: (item: any) => (
      <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium dark:bg-gray-700">
        {item.method ?? item.api_type ?? '-'}
      </span>
    )},
    { key: 'endpoint', header: t('apiHistory.path', 'Endpoint'), render: (item: any) => (
      <span className="font-mono text-xs">{item.path ?? '-'}</span>
    )},
    { key: 'status_code', header: t('apiHistory.status', 'Status'), render: (item: any) => (
      <span
        className={`rounded px-2 py-0.5 text-xs font-medium ${
          (item.status_code ?? item.statusCode ?? 200) < 400
            ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
            : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
        }`}
      >
        {item.status_code ?? item.statusCode ?? '-'}
      </span>
    )},
    { key: 'duration_ms', header: t('apiHistory.duration', 'Duration'), render: (item: any) => (
      <span className="text-xs">{item.duration_ms ?? item.durationMs ?? '-'}ms</span>
    )},
    { key: 'thread_id', header: t('apiHistory.thread', 'Thread'), render: (item: any) => (
      <span className="text-xs">{item.thread_id ?? '-'}</span>
    )},
  ]

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full max-w-md" />
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
          message={t('apiHistory.failedToLoad', 'Failed to load API history')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <div className="mb-4 flex flex-wrap gap-3">
        <input
          type="text"
          placeholder={t('apiHistory.searchPlaceholder', 'Search by endpoint...')}
          onInput={(e) => { if (!searchComposing.current && !(e.nativeEvent as InputEvent).isComposing) setSearch((e.target as HTMLInputElement).value) }}
          onCompositionStart={() => { searchComposing.current = true }}
          onCompositionEnd={(e) => { searchComposing.current = false; setSearch((e.target as HTMLInputElement).value) }}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
        />
        <select
          value={methodFilter ?? ''}
          onChange={(e) => setMethodFilter(e.target.value || undefined)}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
        >
          <option value="">{t('apiHistory.allMethods', 'All methods')}</option>
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
        </select>
        <select
          value={statusFilter ?? ''}
          onChange={(e) => setStatusFilter(e.target.value || undefined)}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
        >
          <option value="">{t('apiHistory.allStatuses', 'All statuses')}</option>
          <option value="200">{t('apiHistory.status200', '200 Success')}</option>
          <option value="400">{t('apiHistory.status400', '400 Bad Request')}</option>
          <option value="401">{t('apiHistory.status401', '401 Unauthorized')}</option>
          <option value="403">{t('apiHistory.status403', '403 Forbidden')}</option>
          <option value="500">{t('apiHistory.status500', '500 Server Error')}</option>
        </select>
      </div>

      {history.length > 0 ? (
        <div>
          <Table columns={columns} data={history} />
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between">
              <span className="text-xs text-gray-500">
                {t('apiHistory.pageInfo', 'Page {page} of {totalPages} ({total} total)').replace('{page}', String(page)).replace('{totalPages}', String(totalPages)).replace('{total}', String(total))}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  {t('common.previous', 'Previous')}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  {t('common.next', 'Next')}
                </Button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <EmptyState message={t('apiHistory.noRequests', 'No API requests recorded yet.')} />
      )}
    </div>
  )
}
