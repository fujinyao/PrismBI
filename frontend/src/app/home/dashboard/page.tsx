'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { dashboardApi } from '@/lib/api'
import { useProjectStore } from '@/stores/projectStore'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Input } from '@/components/ui/Input'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { formatDate } from '@/lib/utils'
import { DashboardSidebar } from '@/components/dashboard/DashboardShell'
import { ExecutionHealthPanel } from '@/components/dashboard/ExecutionHealthPanel'

interface Dashboard {
  id: number
  name: string
  display_name?: string
  project_id: number
  item_count?: number
  created_at?: string
}

const DASHBOARD_SHORT_CACHE_MS = 5000

export default function DashboardListPage() {
  const router = useRouter()
  const { toast } = useToast()
  const t = useI18nStore((s) => s.t)
  const queryClient = useQueryClient()
  const currentProject = useProjectStore((s) => s.currentProject)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canReadModelMetrics = hasPermission('models', 'read')

  const [createOpen, setCreateOpen] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)
  const [newName, setNewName] = useState('')

  const { data: dashboards, isLoading, isError, refetch } = useQuery({
    queryKey: ['dashboards', currentProject?.id],
    queryFn: () => {
      const id = currentProject?.id
      if (!id) return []
      return dashboardApi.list({ project_id: id }) as Promise<Dashboard[]>
    },
    enabled: Boolean(currentProject?.id),
    staleTime: DASHBOARD_SHORT_CACHE_MS,
    gcTime: DASHBOARD_SHORT_CACHE_MS * 20,
    refetchOnWindowFocus: false,
  })

  const createMutation = useMutation({
    mutationFn: (name: string) => {
      const id = currentProject?.id
      if (!id) return Promise.reject(new Error('No project selected'))
      return dashboardApi.create({ name, project_id: id })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboards', currentProject?.id] })
      toast(t('dashboard.created', 'Dashboard created'), 'success')
      setCreateOpen(false)
      setNewName('')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.createFailed', 'Failed to create dashboard'), 'error'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => dashboardApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboards', currentProject?.id] })
      toast(t('dashboard.deleted', 'Dashboard deleted'), 'success')
      setDeleteConfirm(null)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.deleteFailed', 'Failed to delete dashboard'), 'error'),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => dashboardApi.update(id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboards', currentProject?.id] })
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.renameFailed', 'Failed to rename dashboard'), 'error'),
  })

  const handleCreate = () => {
    if (!newName.trim()) {
      toast(t('dashboard.enterName', 'Please enter a name'), 'warning')
      return
    }
    if (!currentProject) {
      toast(t('dashboard.noProject', 'No project selected'), 'warning')
      return
    }
    createMutation.mutate(newName.trim())
  }

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-[5px] md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          message={t('dashboard.loadError', 'Failed to load dashboards')}
          description={t('dashboard.loadErrorDesc', 'There was an error loading dashboards. Please try again.')}
          action={{ label: t('common.retry', 'Retry'), onClick: () => refetch() }}
        />
      </div>
    )
  }

  const items = dashboards ?? []

  return (
    <div className="flex min-h-full gap-[5px]">
      <DashboardSidebar
        dashboards={items}
        canCreate={Boolean(currentProject)}
        onCreate={() => setCreateOpen(true)}
        onSelect={(id) => router.push(`/home/dashboard/${id}`)}
        onRename={(id, name) => renameMutation.mutate({ id, name })}
        onDelete={(id) => setDeleteConfirm(id)}
      />
      <section className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-2 flex justify-end">
        <Button disabled={!currentProject} onClick={() => setCreateOpen(true)}>{t('dashboard.create', 'Create Dashboard')}</Button>
      </div>

      {canReadModelMetrics && (
        <ExecutionHealthPanel projectId={currentProject?.id} className="mb-2" />
      )}

      {items.length === 0 ? (
        <EmptyState
          title={currentProject ? t('dashboard.noDashboards', 'No dashboards yet') : t('dashboard.noProject', 'No project selected')}
          description={currentProject ? t('dashboard.noDashboardsDesc', 'Create your first dashboard to start visualizing data.') : t('dashboard.noProjectDesc', 'Select a project before creating dashboards.')}
          action={currentProject ? { label: t('dashboard.create', 'Create Dashboard'), onClick: () => setCreateOpen(true) } : undefined}
        />
      ) : (
        <div className="grid grid-cols-1 gap-[5px] md:grid-cols-2 lg:grid-cols-3">
          {items.map((d) => (
            <Card
              key={d.id}
              className="flex cursor-pointer flex-col rounded-xl transition-shadow hover:shadow-md"
              onClick={() => router.push(`/home/dashboard/${d.id}`)}
            >
              <CardHeader>
                <CardTitle className="truncate">{d.display_name ?? d.name}</CardTitle>
              </CardHeader>
              <CardContent className="flex-1">
                <div className="space-y-2 text-sm text-gray-500 dark:text-gray-400">
                  <div className="flex items-center gap-2">
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                    </svg>
                    <span>{d.item_count ?? 0} {t('dashboard.items', 'items')}</span>
                  </div>
                  {d.created_at && (
                    <div className="flex items-center gap-2">
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                      </svg>
                      <span>{formatDate(d.created_at)}</span>
                    </div>
                  )}
                </div>
              </CardContent>
              <div className="flex items-center justify-end gap-2 border-t border-gray-100 px-4 py-3 dark:border-gray-800">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    router.push(`/home/dashboard/${d.id}`)
                  }}
                >
                  {t('dashboard.open', 'Open')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    setDeleteConfirm(d.id)
                  }}
                >
                  <svg className="h-4 w-4 text-gray-400 hover:text-error" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        onClose={() => {
          setCreateOpen(false)
          setNewName('')
        }}
        title={t('dashboard.create', 'Create Dashboard')}
      >
        <div className="space-y-4">
          <Input
            label={t('dashboard.nameLabel', 'Dashboard Name')}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t('dashboard.namePlaceholder', 'e.g. Sales Overview')}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreate()
            }}
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setCreateOpen(false)
                setNewName('')
              }}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={handleCreate} loading={createMutation.isPending}>
              {t('common.create', 'Create')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={deleteConfirm !== null}
        onClose={() => setDeleteConfirm(null)}
        title={t('dashboard.delete', 'Delete Dashboard')}
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {t('dashboard.deleteConfirm', 'Are you sure you want to delete this dashboard? This action cannot be undone.')}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleteConfirm(null)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (deleteConfirm !== null) deleteMutation.mutate(deleteConfirm)
              }}
              loading={deleteMutation.isPending}
            >
              {t('common.delete', 'Delete')}
            </Button>
          </div>
        </div>
      </Modal>
      </section>
    </div>
  )
}
