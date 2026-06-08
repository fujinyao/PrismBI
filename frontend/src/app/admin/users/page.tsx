'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminRolesApi, adminUsersApi, type User } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { UserTable } from '@/components/admin/UserTable'
import { UserFormDrawer } from '@/components/admin/UserFormDrawer'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useToast } from '@/components/ui/Toast'
import { RequirePermission } from '@/components/providers/RequirePermission'

export default function AdminUsersPage() {
  return (
    <RequirePermission resource="users" action="read">
      <AdminUsersContent />
    </RequirePermission>
  )
}

function AdminUsersContent() {
  const t = useI18nStore((s) => s.t)
  const [showDrawer, setShowDrawer] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canCreate = hasPermission('users', 'create')
  const canUpdate = hasPermission('users', 'update')
  const canDelete = hasPermission('users', 'delete')
  const canManageRoles = hasPermission('users', 'manage')

  const {
    data: users,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => adminUsersApi.list(),
  })

  const { data: roles } = useQuery({
    queryKey: ['admin-roles'],
    queryFn: () => adminRolesApi.list(),
    enabled: canCreate || canUpdate || canManageRoles,
  })

  const createMutation = useMutation({
    mutationFn: async (data: { username: string; display_name: string; email: string; password?: string; role_id?: number; status: string }) => {
      const created = await adminUsersApi.create({
        username: data.username,
        password: data.password || '',
        display_name: data.display_name,
        email: data.email,
        status: data.status,
      })
      if (data.role_id && canManageRoles) {
        const defaultRoleIds = (created.roles ?? []).filter((role) => role.name === 'viewer').map((role) => role.id)
        if (!defaultRoleIds.includes(data.role_id)) {
          await adminUsersApi.assignRole(created.id, { role_id: data.role_id })
        }
        await Promise.all(defaultRoleIds.filter((roleId) => roleId !== data.role_id).map((roleId) => adminUsersApi.removeRole(created.id, roleId)))
      }
      return created
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast(t('admin.users.created', 'User created'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('admin.users.failedToCreate', 'Failed to create user'), 'error'),
  })

  const updateMutation = useMutation({
    mutationFn: async (data: { username: string; display_name: string; email: string; role_id?: number; status: string }) => {
      if (!editingUser) throw new Error('No user selected')
      const updated = await adminUsersApi.update(editingUser.id, {
        display_name: data.display_name,
        email: data.email,
        status: data.status,
      })
      const currentRoleId = editingUser.role_id ?? editingUser.roles?.find((role) => role.project_id == null)?.id
      if (data.role_id && canManageRoles && currentRoleId !== data.role_id) {
        if (currentRoleId) await adminUsersApi.removeRole(editingUser.id, currentRoleId)
        const alreadyHasRole = editingUser.roles?.some((role) => role.id === data.role_id)
        if (!alreadyHasRole) {
          await adminUsersApi.assignRole(editingUser.id, { role_id: data.role_id })
        }
        await Promise.all(
          (editingUser.roles ?? [])
            .filter((role) => role.name === 'viewer' && role.id !== data.role_id)
            .map((role) => adminUsersApi.removeRole(editingUser.id, role.id)),
        )
      }
      return updated
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast(t('admin.users.updated', 'User updated'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('admin.users.failedToUpdate', 'Failed to update user'), 'error'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => adminUsersApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast(t('admin.users.deleted', 'User deleted'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('admin.users.failedToDelete', 'Failed to delete user'), 'error'),
  })

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full max-w-md" />
          {Array.from({ length: 6 }).map((_, i) => (
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
          message={t('admin.users.failedToLoad', 'Failed to load users')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-4 flex items-center justify-end">
        {canCreate && (
          <Button variant="primary" onClick={() => { setEditingUser(null); setShowDrawer(true) }}>
            {t('admin.users.add', 'Add User')}
          </Button>
        )}
      </div>

      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      {showDrawer && (
        <UserFormDrawer
          open
          user={editingUser ?? undefined}
          roles={roles?.roles ?? []}
          onClose={() => setShowDrawer(false)}
          onSave={(data) => editingUser ? updateMutation.mutateAsync(data) : createMutation.mutateAsync(data)}
        />
      )}

      {users && (users.items?.length ?? 0) > 0 ? (
        <UserTable
          users={users.items}
          loading={false}
          searchQuery=""
          canEdit={canUpdate}
          canDelete={canDelete}
          onEdit={(user) => { if (canUpdate) { setEditingUser(user); setShowDrawer(true) } }}
          onDelete={(id) => { if (canDelete && confirm(t('admin.users.deleteConfirm', 'Delete this user?'))) deleteMutation.mutate(id) }}
        />
      ) : (
        <EmptyState
          title={t('admin.users.noUsers', 'No users yet. Invite your first user.')}
          action={canCreate ? { label: t('admin.users.inviteFirst', 'Invite first user'), onClick: () => { setEditingUser(null); setShowDrawer(true) } } : undefined}
        />
      )}
    </div>
  )
}
