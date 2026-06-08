'use client'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Tag } from '@/components/ui/Tag'
import { SkeletonTable } from '@/components/ui/Skeleton'
import { useI18nStore } from '@/stores/i18nStore'
import type { Role } from '@/lib/api'

interface RoleTableProps {
  roles: Role[]
  loading: boolean
  onEdit: (role: Role) => void
  onDelete: (id: number) => void
  canEdit?: boolean
  canDelete?: boolean
  className?: string
}

export function RoleTable({ roles, loading, onEdit, onDelete, canEdit = true, canDelete = true, className }: RoleTableProps) {
  const t = useI18nStore((s) => s.t)
  if (loading) {
    return <SkeletonTable rows={4} cols={5} />
  }

  if (roles.length === 0) {
    return (
      <div className={cn('py-12 text-center text-sm text-gray-500 dark:text-gray-400', className)}>
        {t('admin.roles.noResults', 'No roles found.')}
      </div>
    )
  }

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
        <thead className="bg-gray-50 dark:bg-gray-800">
          <tr>
            {['Name', 'Description', 'Users', 'Permissions', ''].map((h, i) => {
              const labels: Record<string, string> = {
                Name: t('admin.roles.name', 'Name'),
                Description: t('admin.roles.description', 'Description'),
                Users: t('admin.roles.users', 'Users'),
                Permissions: t('admin.roles.permissions', 'Permissions'),
              }
              return (
                <th
                  key={i}
                  className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400"
                >
                  {labels[h] ?? h}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-900">
          {roles.map((role) => (
            <tr
              key={role.id}
              className="transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
            >
              <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900 dark:text-gray-100">
                {role.name}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                {role.description ?? '-'}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700 dark:text-gray-300">
                {role.userCount ?? role.member_count ?? 0}
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <Tag variant="info" size="sm">
                  {role.permissionsCount ?? role.permissions?.length ?? 0}
                </Tag>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-right">
                <div className="flex items-center justify-end gap-1">
                  {canEdit && (
                    <Button variant="ghost" size="sm" onClick={() => onEdit(role)}>
                      {t('admin.roles.edit', 'Edit')}
                    </Button>
                  )}
                  {canDelete && (
                    <Button variant="ghost" size="sm" onClick={() => onDelete(role.id)} disabled={role.is_system}>
                      {t('admin.roles.delete', 'Delete')}
                    </Button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
