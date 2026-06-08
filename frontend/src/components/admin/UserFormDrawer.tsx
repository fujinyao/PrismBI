'use client'

import { useState, useEffect } from 'react'
import { Drawer } from '@/components/ui/Drawer'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import type { Role, User } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'

interface UserFormDrawerProps {
  open: boolean
  user?: User
  roles?: Role[]
  onClose: () => void
  onSave: (data: { username: string; display_name: string; email: string; password?: string; role_id?: number; status: string }) => void
}

export function UserFormDrawer({ open, user, roles = [], onClose, onSave }: UserFormDrawerProps) {
  const t = useI18nStore((s) => s.t)

  const ROLE_OPTIONS = roles.map((role) => ({ label: role.name, value: String(role.id) }))
  const isEdit = !!user
  const [username, setUsername] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [roleId, setRoleId] = useState<string>('')
  const [status, setStatus] = useState('ACTIVE')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setUsername(user?.username ?? '')
      setName(user?.display_name ?? '')
      setEmail(user?.email ?? '')
      setPassword('')
      const currentRoleId = user?.role_id ?? user?.roles?.find((role) => role.project_id == null)?.id ?? user?.roles?.[0]?.id
      setRoleId(currentRoleId ? String(currentRoleId) : String(roles.find((r) => r.name === 'viewer')?.id ?? roles[0]?.id ?? ''))
      setStatus(user?.status ?? 'ACTIVE')
    }
  }, [open, roles, user])

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({
        username,
        display_name: name,
        email,
        ...(isEdit ? {} : { password }),
        role_id: roleId ? Number(roleId) : undefined,
        status,
      })
      onClose()
    } catch (err) {
      console.error('Failed to save user:', err)
    } finally {
      setSaving(false)
    }
  }

  const valid = username.trim() && (isEdit || password.trim())

  return (
    <Drawer open={open} onClose={onClose} title={isEdit ? t('admin.users.editTitle', 'Edit User') : t('admin.users.addTitle', 'Add User')} size="md">
      <div className="space-y-4">
        <Input
          label={t('admin.users.username', 'Username')}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder={t('admin.users.usernamePlaceholder', 'jdoe')}
          disabled={isEdit}
        />
        <Input
          label={t('admin.users.name', 'Name')}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('admin.users.namePlaceholder', 'John Doe')}
        />
        <Input
          label={t('admin.users.email', 'Email')}
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={t('admin.users.emailPlaceholder', 'john@example.com')}
        />
        {!isEdit && (
          <Input
            label={t('admin.users.password', 'Password')}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('admin.users.passwordPlaceholder', 'Enter password')}
          />
        )}
        <Select
          label={t('admin.users.role', 'Role')}
          value={roleId}
          options={ROLE_OPTIONS}
          onChange={(v) => setRoleId(v)}
        />
        <div className="space-y-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            {t('admin.users.status', 'Status')}
          </label>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="radio"
                name="status"
                value="ACTIVE"
                checked={status === 'ACTIVE'}
                onChange={() => setStatus('ACTIVE')}
                className="text-primary focus:ring-primary-300"
              />
              {t('admin.users.active', 'Active')}
            </label>
            <label className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="radio"
                name="status"
                value="INACTIVE"
                checked={status === 'INACTIVE'}
                onChange={() => setStatus('INACTIVE')}
                className="text-primary focus:ring-primary-300"
              />
              {t('admin.users.disabled', 'Disabled')}
            </label>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 pt-4">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button onClick={handleSave} loading={saving} disabled={!valid}>
            {isEdit ? t('admin.users.saveChanges', 'Save Changes') : t('admin.users.addUser', 'Add User')}
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
