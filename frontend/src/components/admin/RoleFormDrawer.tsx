'use client'

import { useState, useEffect } from 'react'
import { Drawer } from '@/components/ui/Drawer'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Select'
import type { Permission, Role } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'

interface RoleFormDrawerProps {
  open: boolean
  role?: Role
  permissions?: Permission[]
  canManagePermissions?: boolean
  onClose: () => void
  onSave: (data: { name: string; scope: string; description: string; permissions: number[] }) => void
}

export function RoleFormDrawer({ open, role, permissions = [], canManagePermissions = true, onClose, onSave }: RoleFormDrawerProps) {
  const t = useI18nStore((s) => s.t)
  const isEdit = !!role
  const [name, setName] = useState('')
  const [scope, setScope] = useState('SYSTEM')
  const [description, setDescription] = useState('')
  const [permissionIds, setPermissionIds] = useState<Set<number>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setName(role?.name ?? '')
      setScope(role?.scope ?? 'SYSTEM')
      setDescription(role?.description ?? '')
      setPermissionIds(new Set((role?.permissions ?? []).map((p) => p.id).filter((id): id is number => typeof id === 'number')))
    }
  }, [open, role])

  const togglePermission = (id: number) => {
    setPermissionIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({ name, scope, description, permissions: Array.from(permissionIds) })
      onClose()
    } catch {
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title={isEdit ? t('admin.roles.editTitle', 'Edit Role') : t('admin.roles.addTitle', 'Add Role')} size="sm">
      <div className="space-y-4">
        <Input
          label={t('admin.roles.name', 'Role Name')}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('admin.roles.namePlaceholder', 'e.g. Data Analyst')}
          disabled={role?.is_system}
        />
        <Select
          label={t('admin.roles.scope', 'Scope')}
          value={scope}
          options={[
            { label: t('admin.roles.scopeSystem', 'System'), value: 'SYSTEM' },
            { label: t('admin.roles.scopeProject', 'Project'), value: 'PROJECT' },
          ]}
          onChange={setScope}
          disabled={role?.is_system}
        />
        <div className="space-y-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            {t('admin.roles.description', 'Description')}
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            placeholder={t('admin.roles.descriptionPlaceholder', 'Describe the responsibilities of this role')}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
          />
        </div>
        {canManagePermissions && (
          <div className="space-y-2">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('admin.roles.permissions', 'Permissions')}
            </label>
            <div className="max-h-64 space-y-1 overflow-y-auto rounded-md border border-gray-200 p-2 dark:border-gray-700">
              {permissions.map((permission) => (
                <label key={permission.id} className="flex items-center gap-2 rounded px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800">
                  <input
                    type="checkbox"
                    checked={permission.id ? permissionIds.has(permission.id) : false}
                    disabled={!permission.id}
                    onChange={() => permission.id && togglePermission(permission.id)}
                    className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary-300"
                  />
                  <span className="font-medium">{permission.resource}:{permission.action}</span>
                  {permission.description && <span className="text-gray-400">{permission.description}</span>}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="flex items-center justify-end gap-2 pt-4">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button onClick={handleSave} loading={saving} disabled={!name.trim()}>
            {isEdit ? t('admin.roles.saveChanges', 'Save Changes') : t('admin.roles.addRole', 'Add Role')}
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
