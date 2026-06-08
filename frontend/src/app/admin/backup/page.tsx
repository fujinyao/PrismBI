'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminBackupApi, type BackupEntry } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/Toast'
import { RequirePermission } from '@/components/providers/RequirePermission'

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes <= 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1)
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export default function AdminBackupPage() {
  return (
    <RequirePermission resource="backup" action="read">
      <AdminBackupContent />
    </RequirePermission>
  )
}

function AdminBackupContent() {
  const t = useI18nStore((s) => s.t)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const canCreate = hasPermission('backup', 'create')
  const canRestore = hasPermission('backup', 'restore')
  const canDelete = hasPermission('backup', 'delete')
  const canDownload = hasPermission('backup', 'download')

  const [restoring, setRestoring] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const { data: backups, isLoading, error } = useQuery({
    queryKey: ['admin', 'backups'],
    queryFn: () => adminBackupApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: () => adminBackupApi.create(),
    onSuccess: () => {
      toast(t('admin.backup.created', 'Backup created successfully'), 'success')
      queryClient.invalidateQueries({ queryKey: ['admin', 'backups'] })
    },
    onError: (err: Error) => toast(t('admin.backup.createFailed', 'Failed to create backup') + ': ' + err.message, 'error'),
  })

  const restoreMutation = useMutation({
    mutationFn: (name: string) => adminBackupApi.restore(name),
    onSuccess: (result) => {
      if (result.success) {
        toast(t('admin.backup.restored', 'Backup restored successfully. Please refresh the page.'), 'success')
      } else {
        toast(t('admin.backup.restoreFailed', 'Restore failed') + ': ' + (result.error || 'Unknown error'), 'error')
      }
      setRestoring(null)
    },
    onError: (err: Error) => {
      toast(t('admin.backup.restoreFailed', 'Restore failed') + ': ' + err.message, 'error')
      setRestoring(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (name: string) => adminBackupApi.delete(name),
    onSuccess: () => {
      toast(t('admin.backup.deleted', 'Backup deleted'), 'success')
      queryClient.invalidateQueries({ queryKey: ['admin', 'backups'] })
      setConfirmDelete(null)
    },
    onError: (err: Error) => toast(t('admin.backup.deleteFailed', 'Failed to delete backup') + ': ' + err.message, 'error'),
  })

  const handleRestore = (name: string) => {
    if (confirm(t('admin.backup.restoreConfirm', 'Are you sure? This will replace all current data with the backup data.'))) {
      setRestoring(name)
      restoreMutation.mutate(name)
    }
  }

  if (error) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{t('admin.backup.title', 'Backup & Restore')}</h1>
        <EmptyState title={t('admin.backup.loadFailed', 'Failed to load backups')} description={error.message} />
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{t('admin.backup.title', 'Backup & Restore')}</h1>
        {canCreate && (
          <Button
            variant="primary"
            size="sm"
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {t('admin.backup.createBackup', 'Create Backup')}
          </Button>
        )}
      </div>

      <div className="rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div className="border-b border-gray-200 px-4 py-3 dark:border-gray-700">
          <div className="grid grid-cols-[1fr_120px_100px_80px_120px] gap-4 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
            <span>{t('admin.backup.name', 'Name')}</span>
            <span>{t('admin.backup.createdAt', 'Created')}</span>
            <span>{t('admin.backup.size', 'Size')}</span>
            <span>{t('admin.backup.status', 'Status')}</span>
            <span>{t('admin.backup.actions', 'Actions')}</span>
          </div>
        </div>

        {isLoading ? (
          <div className="p-4 space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : !backups || backups.length === 0 ? (
          <EmptyState title={t('admin.backup.noBackups', 'No backups')} description={t('admin.backup.noBackupsDesc', 'Click "Create Backup" to create your first backup.')} />
        ) : (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {backups.map((b: BackupEntry) => (
              <div
                key={b.name}
                className="grid grid-cols-[1fr_120px_100px_80px_120px] items-center gap-4 px-4 py-3 text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium text-gray-900 dark:text-gray-100">{b.name}</p>
                </div>
                <div className="text-gray-500 dark:text-gray-400">{formatDate(b.created_at)}</div>
                <div className="text-gray-500 dark:text-gray-400">{formatBytes(b.size)}</div>
                <div>
                  {b.valid ? (
                    <span className="inline-flex rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-400">
                      {t('admin.backup.valid', 'Valid')}
                    </span>
                  ) : (
                    <span className="inline-flex rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800 dark:bg-red-900/30 dark:text-red-400">
                      {t('admin.backup.invalid', 'Invalid')}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {canRestore && b.valid && (
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={restoring === b.name}
                      disabled={restoring !== null && restoring !== b.name}
                      onClick={() => handleRestore(b.name)}
                    >
                      {t('admin.backup.restore', 'Restore')}
                    </Button>
                  )}
                  <a
                    href={canDownload ? adminBackupApi.downloadUrl(b.name) : '#'}
                    className={canDownload ? 'text-xs text-primary hover:underline' : 'text-xs text-gray-400 cursor-not-allowed'}
                    download={canDownload || undefined}
                    onClick={(e) => { if (!canDownload) e.preventDefault() }}
                  >
                    {t('admin.backup.download', 'Download')}
                  </a>
                  {canDelete && (
                    confirmDelete === b.name ? (
                      <div className="flex items-center gap-1">
                        <Button variant="danger" size="sm" onClick={() => deleteMutation.mutate(b.name)} loading={deleteMutation.isPending}>
                          {t('common.confirm', 'Confirm')}
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(null)}>
                          {t('common.cancel', 'Cancel')}
                        </Button>
                      </div>
                    ) : (
                      <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(b.name)} className="text-red-600 hover:text-red-800">
                        {t('admin.backup.delete', 'Delete')}
                      </Button>
                    )
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}