'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminRolesApi, adminPermissionsApi, type Role } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { RoleTable } from '@/components/admin/RoleTable'
import { RoleFormDrawer } from '@/components/admin/RoleFormDrawer'
import { PermissionMatrix } from '@/components/admin/PermissionMatrix'
import { Button } from '@/components/ui/Button'
import { Tabs } from '@/components/ui/Tabs'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useToast } from '@/components/ui/Toast'
import { RequirePermission } from '@/components/providers/RequirePermission'

export default function AdminRolesPage() {
  return (
    <RequirePermission resource="roles" action="read">
      <AdminRolesContent />
    </RequirePermission>
  )
}

function AdminRolesContent() {
  const t = useI18nStore((s) => s.t)

  const TABS = [
    { key: 'roles', label: t('admin.roles.title', 'Roles') },
    { key: 'permissions', label: t('admin.roles.permissionMatrix', 'Permission Matrix') },
  ]

  const [activeTab, setActiveTab] = useState('roles')
  const [showDrawer, setShowDrawer] = useState(false)
  const [editingRole, setEditingRole] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canCreate = hasPermission('roles', 'create')
  const canUpdate = hasPermission('roles', 'update')
  const canDelete = hasPermission('roles', 'delete')
  const canManage = hasPermission('roles', 'manage')

  const {
    data: roles,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['admin-roles'],
    queryFn: () => adminRolesApi.list(),
  })

  const {
    data: permissions,
  } = useQuery({
    queryKey: ['admin-permissions'],
    queryFn: () => adminPermissionsApi.list(),
    enabled: canManage || activeTab === 'permissions',
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => adminRolesApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-roles'] })
      toast(t('admin.roles.deleted', 'Role deleted'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('admin.roles.failedToDelete', 'Failed to delete role'), 'error'),
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; scope: string; description: string; permissions: number[] }) =>
      adminRolesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-roles'] })
      toast(t('admin.roles.created', 'Role created'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('admin.roles.failedToCreate', 'Failed to create role'), 'error'),
  })

  const updateMutation = useMutation({
    mutationFn: (data: { name: string; description: string; permissions: number[] }) => {
      if (!editingRole) throw new Error('No role selected')
      return adminRolesApi.update(editingRole.id, {
        name: data.name,
        description: data.description,
        permissions: canManage ? data.permissions : undefined,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-roles'] })
      toast(t('admin.roles.updated', 'Role updated'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('admin.roles.failedToUpdate', 'Failed to update role'), 'error'),
  })

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full max-w-md" />
          {Array.from({ length: 4 }).map((_, i) => (
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
          message={t('admin.roles.failedToLoad', 'Failed to load roles')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <Tabs tabs={TABS} activeKey={activeTab} onChange={setActiveTab} />

      <div className="mt-6">
        {activeTab === 'roles' && (
          <>
            <div className="mb-4 flex justify-end">
              {canCreate && (
                <Button variant="primary" onClick={() => { setEditingRole(null); setShowDrawer(true) }}>
                  {t('admin.roles.create', 'Create Role')}
                </Button>
              )}
            </div>

            {showDrawer && (
                <RoleFormDrawer
                  open
                  role={editingRole ?? undefined}
                  permissions={permissions ?? []}
                  canManagePermissions={canManage}
                  onClose={() => setShowDrawer(false)}
                  onSave={(data) => editingRole ? updateMutation.mutateAsync(data) : createMutation.mutateAsync(data)}
                />
              )}

            {roles && roles.roles.length > 0 ? (
              <RoleTable
                roles={roles.roles}
                loading={false}
                canEdit={canUpdate}
                canDelete={canDelete}
                onEdit={(role) => { if (canUpdate) { setEditingRole(role); setShowDrawer(true) } }}
                onDelete={(id) => { if (canDelete && confirm(t('admin.roles.deleteConfirm', 'Delete this role?'))) deleteMutation.mutate(id) }}
              />
            ) : (
              <EmptyState
                title={t('admin.roles.noRoles', 'No roles defined yet.')}
                action={canCreate ? { label: t('admin.roles.createFirst', 'Create first role'), onClick: () => { setEditingRole(null); setShowDrawer(true) } } : undefined}
              />
            )}
          </>
        )}
        {activeTab === 'permissions' && (
          <PermissionMatrix
            permissions={permissions ?? []}
            readonly
          />
        )}
      </div>
    </div>
  )
}
