'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { systemDatasourcesApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { DataSourceSettings } from '@/components/settings/DataSourceSettings'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useToast } from '@/components/ui/Toast'

export default function DatasourcesPage() {
  const t = useI18nStore((s) => s.t)
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [showAddModal, setShowAddModal] = useState(false)
  const [formData, setFormData] = useState({ name: '', type: 'postgresql', properties: '' })
  const [refreshToken, setRefreshToken] = useState(0)

  const {
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['system-datasources'],
    queryFn: () => systemDatasourcesApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; type: string; properties: Record<string, unknown> }) =>
      systemDatasourcesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['system-datasources'] })
      setRefreshToken((value) => value + 1)
      toast(t('datasource.created', 'Datasource created'), 'success')
      setShowAddModal(false)
      setFormData({ name: '', type: 'postgresql', properties: '' })
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('datasource.failedToCreate', 'Failed to create datasource'), 'error'),
  })

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full max-w-md" />
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('datasource.failedToLoad', 'Failed to load datasources')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <DataSourceSettings onAdd={() => setShowAddModal(true)} refreshToken={refreshToken} />

      <Modal open={showAddModal} onClose={() => setShowAddModal(false)} title={t('datasource.add', 'Add Datasource')} size="lg">
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
              { label: 'PostgreSQL', value: 'postgresql' },
              { label: 'MySQL', value: 'mysql' },
              { label: 'BigQuery', value: 'bigquery' },
              { label: 'Snowflake', value: 'snowflake' },
            ]}
            onChange={(v) => setFormData((f) => ({ ...f, type: v }))}
          />
          <Input
            label={t('datasource.propertiesJson', 'Properties (JSON)')}
            value={formData.properties}
            onChange={(e) => setFormData((f) => ({ ...f, properties: e.target.value }))}
            placeholder='{"host": "localhost", "port": 5432}'
          />
          <div className="flex justify-end gap-3 pt-4">
            <Button variant="secondary" onClick={() => setShowAddModal(false)}>{t('common.cancel', 'Cancel')}</Button>
            <Button
              onClick={() => {
                if (!formData.name.trim()) {
                  toast(t('datasource.nameRequired', 'Please enter a name'), 'warning')
                  return
                }
                let properties: Record<string, unknown> = {}
                if (formData.properties.trim()) {
                  try {
                    properties = JSON.parse(formData.properties)
                  } catch {
                    toast(t('datasource.invalidJson', 'Invalid JSON in properties'), 'warning')
                    return
                  }
                }
                createMutation.mutate({ name: formData.name, type: formData.type, properties })
              }}
              loading={createMutation.isPending}
              disabled={!formData.name.trim()}
            >
              {t('common.save', 'Save')}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
