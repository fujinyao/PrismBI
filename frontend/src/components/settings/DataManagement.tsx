'use client'

import { useState } from 'react'
import { useI18nStore } from '@/stores/i18nStore'
import { useMutation } from '@tanstack/react-query'
import { projectsApi } from '@/lib/api'
import { useToast } from '@/components/ui/Toast'

interface DataManagementProps {
  projectId: number
  projectName: string
}

export function DataManagement({ projectId, projectName }: DataManagementProps) {
  const t = useI18nStore((s) => s.t)
  const { toast } = useToast()
  const [importFormat, setImportFormat] = useState<'yaml' | 'json'>('yaml')
  const [migrating, setMigrating] = useState(false)

  const exportMutation = useMutation({
    mutationFn: (format: 'yaml' | 'json') => projectsApi.exportProject(projectId, format),
    onSuccess: (blob, format) => {
      if (!blob) { toast(t('settings.exportEmpty', 'No data to export'), 'error'); return }
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${projectName}-export.${format === 'yaml' ? 'yml' : 'json'}`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast(t('settings.exportSuccess', 'Export downloaded'), 'success')
    },
    onError: () => toast(t('settings.exportFailed', 'Export failed'), 'error'),
  })

  const importMutation = useMutation({
    mutationFn: (file: File) => projectsApi.importProject(file, importFormat),
    onSuccess: (data) => {
      toast(t('settings.importSuccess', 'Project imported successfully'), 'success')
    },
    onError: (err) => toast(err instanceof Error ? err.message : t('settings.importFailed', 'Import failed'), 'error'),
  })

  const handleMigrate = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setMigrating(true)
    try {
      const result = await projectsApi.migrateFromSqlite(file)
      toast(t('settings.migrateSuccess', 'Migration completed'), 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('settings.migrateFailed', 'Migration failed'), 'error')
    } finally {
      setMigrating(false)
      e.target.value = ''
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <h3 className="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">
          {t('settings.exportProject', 'Export Project')}
        </h3>
        <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
          {t('settings.exportProjectDesc', 'Download project configuration, models, and knowledge base.')}
        </p>
        <div className="flex gap-2">
          <button
            onClick={() => exportMutation.mutate('yaml')}
            disabled={exportMutation.isPending}
            className="rounded-lg bg-primary-500 px-4 py-2 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
          >
            {exportMutation.isPending ? t('common.loading', 'Loading...') : 'YAML'}
          </button>
          <button
            onClick={() => exportMutation.mutate('json')}
            disabled={exportMutation.isPending}
            className="rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            JSON
          </button>
        </div>
      </div>

      <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <h3 className="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">
          {t('settings.importProject', 'Import Project')}
        </h3>
        <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
          {t('settings.importProjectDesc', 'Import a project from a YAML or JSON export file.')}
        </p>
        <div className="mb-3 flex gap-2">
          <button
            onClick={() => setImportFormat('yaml')}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${importFormat === 'yaml' ? 'bg-primary-500 text-white' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'}`}
          >
            YAML
          </button>
          <button
            onClick={() => setImportFormat('json')}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${importFormat === 'json' ? 'bg-primary-500 text-white' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'}`}
          >
            JSON
          </button>
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700">
          {importMutation.isPending ? t('common.loading', 'Loading...') : t('settings.chooseFile', 'Choose File')}
          <input
            type="file"
            accept={importFormat === 'yaml' ? '.yml,.yaml' : '.json'}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) importMutation.mutate(file)
            }}
            className="hidden"
            disabled={importMutation.isPending}
          />
        </label>
      </div>

      <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
        <h3 className="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">
          {t('settings.migrateFromWrenUI', 'Migrate from wren-ui')}
        </h3>
        <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
          {t('settings.migrateFromWrenUIDesc', 'Import projects, models, and knowledge from a wren-ui SQLite database.')}
        </p>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700">
          {migrating ? t('common.loading', 'Loading...') : t('settings.chooseSqliteFile', 'Choose SQLite File')}
          <input
            type="file"
            accept=".db,.sqlite,.sqlite3"
            onChange={handleMigrate}
            className="hidden"
            disabled={migrating}
          />
        </label>
      </div>
    </div>
  )
}