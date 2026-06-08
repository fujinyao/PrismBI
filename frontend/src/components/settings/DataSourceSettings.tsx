'use client'

import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { SkeletonTable } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { systemDatasourcesApi } from '@/lib/api'
import { DataSourceCard } from './DataSourceCard'
import { useI18nStore } from '@/stores/i18nStore'
import { useToast } from '@/components/ui/Toast'

interface Datasource {
  id: string
  name: string
  type: string
  connectionString: string
  status: 'connected' | 'disconnected' | 'error'
  lastSyncAt: string | null
}

interface DataSourceSettingsProps {
  onAdd?: () => void
  refreshToken?: number
}

export function DataSourceSettings({ onAdd, refreshToken = 0 }: DataSourceSettingsProps) {
  const t = useI18nStore((s) => s.t)
  const { toast } = useToast()
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [formData, setFormData] = useState({ name: '', type: 'postgresql', connectionString: '' })
  const [saving, setSaving] = useState(false)

  const openAddModal = () => {
    setFormData({ name: '', type: 'postgresql', connectionString: '' })
    setEditingId(null)
    if (onAdd) onAdd()
    else setShowAddModal(true)
  }

  useEffect(() => {
    let cancelled = false
    const fetchData = async () => {
      setLoading(true)
      try {
        const data = await systemDatasourcesApi.list()
        if (!cancelled) setDatasources((data as any[]) ?? [])
      } catch {
        if (!cancelled) setDatasources([])
      }
      if (!cancelled) setLoading(false)
    }
    fetchData()
    return () => { cancelled = true }
  }, [refreshToken])

  const handleTest = async (id: string) => {
    try {
      const result = await systemDatasourcesApi.test(Number(id))
      const success = (result as any)?.success ?? false
      setDatasources((prev) =>
        prev.map((ds) => (ds.id === id ? { ...ds, status: success ? 'connected' as const : 'error' as const } : ds)),
      )
    } catch {
      setDatasources((prev) =>
        prev.map((ds) => (ds.id === id ? { ...ds, status: 'error' as const } : ds)),
      )
    }
  }

  const handleSync = async (id: string) => {
    try {
      await systemDatasourcesApi.test(Number(id))
      setDatasources((prev) =>
        prev.map((ds) =>
          ds.id === id ? { ...ds, lastSyncAt: new Date().toISOString() } : ds,
        ),
      )
    } catch {
      // sync failed, no state update needed
    }
  }

  const handleEdit = (ds: Datasource) => {
    setFormData({ name: ds.name, type: ds.type, connectionString: ds.connectionString })
    setEditingId(ds.id)
    setShowAddModal(true)
  }

  const handleDelete = async (id: string) => {
    try {
      await systemDatasourcesApi.delete(Number(id))
      setDatasources((prev) => prev.filter((ds) => ds.id !== id))
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('datasource.deleteFailed', 'Failed to delete datasource')
      toast(msg, 'error')
    }
  }

  const handleAdd = async () => {
    setSaving(true)
    try {
      if (editingId) {
        await systemDatasourcesApi.update(Number(editingId), {
          name: formData.name,
          properties: { connectionString: formData.connectionString },
        })
      } else {
        await systemDatasourcesApi.create({
          name: formData.name,
          type: formData.type,
          properties: { connectionString: formData.connectionString },
        })
      }
      const data = await systemDatasourcesApi.list()
      setDatasources((data as any[]) ?? [])
      setShowAddModal(false)
      setEditingId(null)
      setFormData({ name: '', type: 'postgresql', connectionString: '' })
    } catch {
      // Error is handled by leaving modal open
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div />
        <Button onClick={openAddModal}>
          {t('datasource.add', '+ Add Datasource')}
        </Button>
      </div>

      {loading ? (
        <SkeletonTable rows={3} cols={5} />
      ) : datasources.length === 0 ? (
        <EmptyState
          title={t('datasource.emptyTitle', 'No datasources configured')}
          description={t('datasource.emptyDescription', 'Add your first datasource to start querying data')}
          action={{ label: t('datasource.addAction', 'Add Datasource'), onClick: openAddModal }}
        />
      ) : (
        <div className="grid gap-4">
          {datasources.map((ds) => (
            <DataSourceCard
              key={ds.id}
              datasource={ds}
              onTest={handleTest}
              onSync={handleSync}
              onEdit={handleEdit}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      <Modal open={showAddModal} onClose={() => setShowAddModal(false)} title={t('datasource.addModalTitle', 'Add Datasource')} size="lg">
        <div className="space-y-4">
          <Input
            label={t('datasource.name', 'Name')}
            value={formData.name}
            onChange={(e) => setFormData((f) => ({ ...f, name: e.target.value }))}
            placeholder={t('datasource.namePlaceholder', 'My Datasource')}
          />
          <Select
            label={t('datasource.type', 'Type')}
            value={formData.type}
            options={[
              { label: t('datasource.type.postgresql', 'PostgreSQL'), value: 'postgresql' },
              { label: t('datasource.type.mysql', 'MySQL'), value: 'mysql' },
              { label: t('datasource.type.bigquery', 'BigQuery'), value: 'bigquery' },
              { label: t('datasource.type.snowflake', 'Snowflake'), value: 'snowflake' },
            ]}
            onChange={(v) => setFormData((f) => ({ ...f, type: v }))}
          />
          <Input
            label={t('datasource.connectionString', 'Connection String')}
            value={formData.connectionString}
            onChange={(e) => setFormData((f) => ({ ...f, connectionString: e.target.value }))}
            placeholder={t('datasource.connectionStringPlaceholder', 'postgresql://user:pass@host:5432/db')}
          />
          <div className="flex justify-end gap-3 pt-4">
            <Button variant="secondary" onClick={() => setShowAddModal(false)}>{t('datasource.cancel', 'Cancel')}</Button>
            <Button onClick={handleAdd} loading={saving} disabled={!formData.name || !formData.connectionString}>
              {t('datasource.save', 'Save')}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
