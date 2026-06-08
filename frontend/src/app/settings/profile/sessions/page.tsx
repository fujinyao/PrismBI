'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { profileApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { Button } from '@/components/ui/Button'
import { formatDate } from '@/lib/utils'

function sessionValue(session: any, snakeKey: string, camelKey: string) {
  return session?.[snakeKey] ?? session?.[camelKey]
}

export default function SessionsPage() {
  const t = useI18nStore((s) => s.t)
  const [error, setError] = useState<string | null>(null)

  const {
    data: sessions,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => profileApi.sessions.list(),
  })

  const handleRevoke = async (sessionId: string) => {
    try {
      await profileApi.sessions.revoke(sessionId)
      refetch()
    } catch {
      setError(t('session.failedToRevoke', 'Failed to revoke session'))
    }
  }

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="mb-2 h-14" />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('session.failedToLoad', 'Failed to load sessions')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      {sessions && (sessions as any[]).length > 0 ? (
        <div className="space-y-3">
          {sessions.map((session: any) => {
            const userAgent = sessionValue(session, 'user_agent', 'userAgent')
            const ipAddress = sessionValue(session, 'ip_address', 'ipAddress')
            const lastActive = sessionValue(session, 'last_active_at', 'lastActiveAt')
            const createdAt = sessionValue(session, 'issued_at', 'createdAt')
            return (
              <div
                key={session.id}
                className="flex items-center justify-between rounded border border-gray-200 p-4"
              >
                <div>
                  <p className="text-sm font-medium">
                    {session.device || userAgent || t('session.unknownDevice', 'Unknown device')}
                  </p>
                  <p className="text-xs text-gray-500">
                    {t('session.ipAddress', 'IP')}: {ipAddress ?? '-'} &middot; {t('session.lastActive', 'Last active')}: {formatDate(lastActive)}
                  </p>
                  <p className="text-xs text-gray-400">
                    {t('session.created', 'Created')}: {formatDate(createdAt)}
                  </p>
                </div>
                <Button variant="danger" size="sm" onClick={() => handleRevoke(String(session.id))}>
                  {t('session.revoke', 'Revoke')}
                </Button>
              </div>
            )
          })}
        </div>
      ) : (
        <EmptyState message={t('session.noActive', 'No active sessions.')} />
      )}
    </div>
  )
}
