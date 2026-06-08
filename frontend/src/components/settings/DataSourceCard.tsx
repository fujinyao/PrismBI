'use client'

import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { cn, formatDate } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface Datasource {
  id: string
  name: string
  type: string
  connectionString: string
  status: 'connected' | 'disconnected' | 'error'
  lastSyncAt: string | null
}

interface DataSourceCardProps {
  datasource: Datasource
  onTest: (id: string) => void
  onSync: (id: string) => void
  onEdit: (ds: Datasource) => void
  onDelete: (id: string) => void
}

const statusDot = (status: Datasource['status']) => {
  const colors = {
    connected: 'bg-success-500',
    disconnected: 'bg-warning-500',
    error: 'bg-error-500',
  }
  return <span className={cn('inline-block h-2.5 w-2.5 rounded-full', colors[status])} />
}

export function DataSourceCard({ datasource, onTest, onSync, onEdit, onDelete }: DataSourceCardProps) {
  const t = useI18nStore((s) => s.t)
  return (
    <Card>
      <CardContent>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <span className="text-2xl" role="img" aria-label={t('datasource.databaseIcon', 'database')}>
              {'\u{1F5C4}\uFE0F'}
            </span>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-gray-100">{datasource.name}</h4>
              <div className="mt-1 flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                <span className="capitalize">{datasource.type}</span>
                <span>|</span>
                <span className="font-mono text-xs">{datasource.connectionString}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2 text-sm">
              {statusDot(datasource.status)}
              <span className="capitalize text-gray-600 dark:text-gray-400">{datasource.status}</span>
            </div>

            <div className="text-right text-xs text-gray-400 dark:text-gray-500">
              {datasource.lastSyncAt ? (
                <>{t('datasource.lastSync', 'Last sync: ')}{formatDate(datasource.lastSyncAt)}</>
              ) : (
                t('datasource.neverSynced', 'Never synced')
              )}
            </div>

            <div className="flex items-center gap-2">
              <Button size="sm" variant="secondary" onClick={() => onTest(datasource.id)}>
                {t('datasource.test', 'Test')}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => onSync(datasource.id)}>
                {t('datasource.syncNow', 'Sync Now')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onEdit(datasource)}>
                {t('datasource.edit', 'Edit')}
              </Button>
              <Button size="sm" variant="danger" onClick={() => onDelete(datasource.id)}>
                {t('datasource.delete', 'Delete')}
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
