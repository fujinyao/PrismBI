'use client'

import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'
import type { Permission } from '@/lib/api'

const DEFAULT_ACTIONS = ['create', 'read', 'update', 'delete', 'manage', 'export'] as const

interface PermissionMatrixProps {
  permissions: Permission[]
  selectedPermissionIds?: number[]
  readonly?: boolean
  roleId?: string
  onChange?: (permissionIds: number[]) => void
  className?: string
}

export function PermissionMatrix({ permissions, selectedPermissionIds = [], readonly = true, onChange, className }: PermissionMatrixProps) {
  const t = useI18nStore((s) => s.t)
  const selected = useMemo(() => new Set(selectedPermissionIds), [selectedPermissionIds])
  const resources = useMemo(() => Array.from(new Set(permissions.map((p) => p.resource))).sort(), [permissions])
  const actions = useMemo(() => {
    const available = new Set(permissions.map((p) => p.action))
    return DEFAULT_ACTIONS.filter((action) => available.has(action))
  }, [permissions])

  const getPermission = (resource: string, action: string) =>
    permissions.find((p) => p.resource === resource && p.action === action)

  const toggle = (permissionId?: number) => {
    if (readonly || !permissionId || !onChange) return
    const next = new Set(selected)
    if (next.has(permissionId)) next.delete(permissionId)
    else next.add(permissionId)
    onChange(Array.from(next))
  }

  const toggleAllFor = (resource: string) => {
    if (readonly || !onChange) return
    const ids = permissions.filter((p) => p.resource === resource && p.id).map((p) => p.id as number)
    const allEnabled = ids.every((id) => selected.has(id))
    const next = new Set(selected)
    ids.forEach((id) => {
      if (allEnabled) next.delete(id)
      else next.add(id)
    })
    onChange(Array.from(next))
  }

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
        <thead className="bg-gray-50 dark:bg-gray-800">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
              {t('permissionMatrix.resource', 'Resource')}
            </th>
            {actions.map((action) => {
              const actionLabels: Record<string, string> = {
                create: t('permissionMatrix.create', 'Create'),
                read: t('permissionMatrix.read', 'Read'),
                update: t('permissionMatrix.update', 'Update'),
                delete: t('permissionMatrix.delete', 'Delete'),
                manage: t('permissionMatrix.manage', 'Manage'),
                export: t('permissionMatrix.export', 'Export'),
              }
              return (
                <th
                  key={action}
                  className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400"
                >
                  {actionLabels[action] ?? action}
                </th>
              )
            })}
            <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
              {t('permissionMatrix.all', 'All')}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-900">
          {resources.map((resource) => (
            <tr
              key={resource}
              className="transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
            >
              <td className="whitespace-nowrap px-4 py-3 text-sm font-medium capitalize text-gray-900 dark:text-gray-100">
                {resource === 'models' ? t('permissionMatrix.resourceModels', 'models') :
                 resource === 'projects' ? t('permissionMatrix.resourceProjects', 'projects') :
                 resource === 'dashboards' ? t('permissionMatrix.resourceDashboards', 'dashboards') :
                 resource === 'knowledge' ? t('permissionMatrix.resourceKnowledge', 'knowledge') :
                 resource === 'settings' ? t('permissionMatrix.resourceSettings', 'settings') :
                 resource === 'admin' ? t('permissionMatrix.resourceAdmin', 'admin') :
                 resource === 'users' ? t('permissionMatrix.resourceUsers', 'users') :
                 resource}
              </td>
              {actions.map((action) => {
                const permission = getPermission(resource, action)
                const enabled = Boolean(permission?.id && selected.has(permission.id))
                return (
                  <td key={action} className="px-4 py-3 text-center">
                    {permission ? (
                      <input
                        type="checkbox"
                        checked={readonly ? true : enabled}
                        disabled={readonly}
                        onChange={() => toggle(permission.id)}
                        className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary-300 dark:border-gray-600"
                      />
                    ) : (
                      <span className="text-gray-300">-</span>
                    )}
                  </td>
                )
              })}
              <td className="px-4 py-3 text-center">
                <input
                  type="checkbox"
                  checked={permissions.filter((p) => p.resource === resource && p.id).every((p) => readonly || selected.has(p.id as number))}
                  disabled={readonly}
                  onChange={() => toggleAllFor(resource)}
                  className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary-300 dark:border-gray-600"
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
