'use client'

import { useState, useEffect, useMemo } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { recommendationsApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'

interface CatalogEntry {
  id: string
  question: string
  sql: string
  inCatalog: boolean
}

export function CatalogManager({ projectId, className }: { projectId?: number; className?: string }) {
  const t = useI18nStore((s) => s.t)
  const [search, setSearch] = useState('')
  const [entries, setEntries] = useState<CatalogEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const data = await recommendationsApi.catalog.list({ project_id: projectId })
        const items = (data as any)?.entries ?? []
        setEntries(items.map((r: any) => ({
          id: String(r.id),
          question: r.question ?? r.title ?? '',
          sql: r.sql ?? r.sql_text ?? (typeof r.metadata === 'object' ? r.metadata?.sql : '') ?? '',
          inCatalog: r.in_catalog ?? true,
        })))
      } catch {
        // API may not be implemented yet
      }
      setLoading(false)
    }
    load()
  }, [projectId])

  const filtered = useMemo(() => {
    if (!search.trim()) return entries
    const q = search.toLowerCase()
    return entries.filter(
      (e) =>
        e.question.toLowerCase().includes(q) ||
        e.sql.toLowerCase().includes(q),
    )
  }, [search, entries])

  const toggleCatalog = async (itemId: string) => {
    const entry = entries.find((e) => e.id === itemId)
    if (!entry) return
    try {
      if (entry.inCatalog) {
        if (projectId) await recommendationsApi.catalog.delete(projectId, Number(itemId))
        setEntries((prev) =>
          prev.map((e) => (e.id === itemId ? { ...e, inCatalog: false } : e)),
        )
      } else if (projectId) {
        const created = await recommendationsApi.catalog.create(projectId, { question: entry.question, sql: entry.sql })
        setEntries((prev) =>
          prev.map((e) => (e.id === itemId ? { ...e, id: String(created.id), inCatalog: true } : e)),
        )
      }
    } catch {
      // ignore
    }
  }

  const inCatalog = filtered.filter((e) => e.inCatalog)
  const notInCatalog = filtered.filter((e) => !e.inCatalog)

  return (
    <div className={cn('space-y-4', className)}>
      <Input
        placeholder={t('catalogManager.searchPlaceholder', 'Search questions or SQL...')}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : filtered.length === 0 && (
        <EmptyState
          title={t('catalogManager.noEntries', 'No entries found')}
          description={search ? t('catalogManager.noResultsFor', `No results for "${search}"`) : t('catalogManager.noPairs', 'No question-SQL pairs yet.')}
        />
      )}

      {inCatalog.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
            {t('catalogManager.inCatalog', 'In Catalog')} ({inCatalog.length})
          </h3>
          {inCatalog.map((entry) => (
            <div
              key={entry.id}
              className="rounded-lg border border-gray-200 p-3 dark:border-gray-700"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {entry.question}
                  </p>
                  <pre className="mt-1 overflow-x-auto text-xs text-gray-500 dark:text-gray-400">
                    {entry.sql}
                  </pre>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => toggleCatalog(entry.id)}
                >
                  {t('catalogManager.remove', 'Remove')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {notInCatalog.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
            {t('catalogManager.available', 'Available')} ({notInCatalog.length})
          </h3>
          {notInCatalog.map((entry) => (
            <div
              key={entry.id}
              className="rounded-lg border border-gray-200 p-3 dark:border-gray-700"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {entry.question}
                  </p>
                  <pre className="mt-1 overflow-x-auto text-xs text-gray-500 dark:text-gray-400">
                    {entry.sql}
                  </pre>
                </div>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => toggleCatalog(entry.id)}
                >
                  {t('catalogManager.add', 'Add')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
