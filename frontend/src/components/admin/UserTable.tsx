'use client'

import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Tag } from '@/components/ui/Tag'
import { SkeletonTable } from '@/components/ui/Skeleton'
import { useI18nStore } from '@/stores/i18nStore'
import type { User as ApiUser } from '@/lib/api'

type User = ApiUser

interface UserTableProps {
  users: User[]
  loading: boolean
  onEdit: (user: User) => void
  onDelete: (id: number) => void
  searchQuery: string
  canEdit?: boolean
  canDelete?: boolean
  className?: string
}

export function UserTable({
  users,
  loading,
  onEdit,
  onDelete,
  searchQuery,
  canEdit = true,
  canDelete = true,
  className,
}: UserTableProps) {
  const t = useI18nStore((s) => s.t)
  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return users
    const q = searchQuery.toLowerCase()
    return users.filter(
      (u) =>
        (u.display_name ?? u.username).toLowerCase().includes(q) ||
        u.username.toLowerCase().includes(q) ||
        (u.email ?? '').toLowerCase().includes(q) ||
        (u.role ?? '').toLowerCase().includes(q),
    )
  }, [users, searchQuery])

  if (loading) {
    return <SkeletonTable rows={6} cols={6} />
  }

  if (users.length === 0) {
    return (
      <div className={cn('py-12 text-center text-sm text-gray-500 dark:text-gray-400', className)}>
        {t('admin.users.noResults', 'No users found.')}
      </div>
    )
  }

  if (filtered.length === 0) {
    return (
      <div className={cn('py-12 text-center text-sm text-gray-500 dark:text-gray-400', className)}>
        {t('admin.users.noMatch', `No users match "${searchQuery}"`)}
      </div>
    )
  }

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
        <thead className="bg-gray-50 dark:bg-gray-800">
          <tr>
            {['Name', 'Email', 'Role', 'Status', 'Last Login', ''].map((h, i) => {
              const labels: Record<string, string> = {
                Name: t('admin.users.name', 'Name'),
                Email: t('admin.users.email', 'Email'),
                Role: t('admin.users.role', 'Role'),
                Status: t('admin.users.status', 'Status'),
                'Last Login': t('admin.users.lastLogin', 'Last Login'),
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
          {filtered.map((user) => {
            const displayName = user.display_name || user.username
            const roleName = user.role || user.roles?.[0]?.name || '-'
            const normalizedStatus = String(user.status ?? '').toUpperCase()
            return (
            <tr
              key={user.id}
              className="transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
            >
              <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900 dark:text-gray-100">
                {displayName}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
                {user.email ?? '-'}
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <Tag variant="info" size="sm">{roleName}</Tag>
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <Tag
                  variant={normalizedStatus === 'ACTIVE' ? 'success' : 'error'}
                  size="sm"
                >
                  {user.status}
                </Tag>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
                {user.last_login_at ?? '-'}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-right">
                <div className="flex items-center justify-end gap-1">
                  {canEdit && (
                    <Button variant="ghost" size="sm" onClick={() => onEdit(user)}>
                      {t('admin.users.edit', 'Edit')}
                    </Button>
                  )}
                  {canDelete && (
                    <Button variant="ghost" size="sm" onClick={() => onDelete(user.id)}>
                      {t('admin.users.delete', 'Delete')}
                    </Button>
                  )}
                </div>
              </td>
            </tr>
          )})}
        </tbody>
      </table>
    </div>
  )
}
