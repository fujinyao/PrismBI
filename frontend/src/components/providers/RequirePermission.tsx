'use client'

import { EmptyState } from '@/components/ui/EmptyState'
import { useAuthStore } from '@/stores/authStore'
import { useI18nStore } from '@/stores/i18nStore'

interface RequirePermissionProps {
  resource: string
  action: string
  children: React.ReactNode
}

export function RequirePermission({ resource, action, children }: RequirePermissionProps) {
  const t = useI18nStore((s) => s.t)
  const hasPermission = useAuthStore((s) => s.hasPermission)

  if (!hasPermission(resource, action)) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <EmptyState
          title={t('auth.permissionDenied', 'Permission denied')}
          description={t('auth.permissionDeniedDesc', 'You do not have permission to view this page.')}
        />
      </div>
    )
  }

  return <>{children}</>
}
