'use client'

import { useState, useMemo } from 'react'
import { Table, type Column } from '@/components/ui/Table'
import { Tag } from '@/components/ui/Tag'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { EmptyState } from '@/components/ui/EmptyState'
import { SkeletonTable } from '@/components/ui/Skeleton'
import { formatDate, truncate } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface SqlPair {
  id: number
  question: string
  sql: string
  category: string | null
  created_at: string
}

interface SqlPairListProps {
  pairs: SqlPair[]
  loading: boolean
  onEdit: (p: SqlPair) => void
  onDelete: (id: number) => void
  searchQuery?: string
}

export function SqlPairList({ pairs, loading, onEdit, onDelete, searchQuery = '' }: SqlPairListProps) {
  const t = useI18nStore((s) => s.t)
  const [search, setSearch] = useState(searchQuery)

  const filtered = useMemo(() => {
    if (!search.trim()) return pairs
    const q = search.toLowerCase()
    return pairs.filter(
      (p) =>
        p.question.toLowerCase().includes(q) ||
        p.sql.toLowerCase().includes(q) ||
        (p.category ?? '').toLowerCase().includes(q),
    )
  }, [pairs, search])

  const columns: Column<SqlPair>[] = useMemo(
    () => [
      {
        key: 'question',
        header: t('knowledge.question', 'Question'),
        sortable: true,
        render: (p) => (
          <span className="text-gray-900 dark:text-gray-100" title={p.question}>
            {truncate(p.question, 60)}
          </span>
        ),
      },
      {
        key: 'sql',
        header: t('knowledge.sql', 'SQL'),
        render: (p) => (
          <code className="block max-w-xs truncate rounded bg-gray-100 dark:bg-gray-800 px-2 py-0.5 font-mono text-xs text-gray-700 dark:text-gray-300">
            {truncate(p.sql, 50)}
          </code>
        ),
      },
      {
        key: 'category',
        header: t('knowledge.category', 'Category'),
        render: (p) => (
          <Tag variant="default">{p.category ?? '-'}</Tag>
        ),
      },
      {
        key: 'created_at',
        header: t('knowledge.created', 'Created'),
        sortable: true,
        render: (p) => (
          <span className="text-gray-500 dark:text-gray-400 text-sm">
            {formatDate(p.created_at)}
          </span>
        ),
      },
      {
        key: 'actions',
        header: '',
        className: 'w-24 text-right',
        render: (p) => (
          <div className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => onEdit(p)}>{t('knowledge.edit', 'Edit')}</Button>
            <Button size="sm" variant="ghost" onClick={() => onDelete(p.id)}>{t('knowledge.delete', 'Delete')}</Button>
          </div>
        ),
      },
    ],
    [onEdit, onDelete, t],
  )

  if (loading) return <SkeletonTable rows={5} cols={6} />

  return (
    <div className="space-y-4">
      <div className="max-w-sm">
        <Input
          placeholder={t('knowledge.searchSqlPairs', 'Search question, SQL, or category...')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title={t('knowledge.noSqlPairsTitle', 'No SQL pairs found')}
          description={
            search
              ? t('knowledge.tryDifferentSearch', 'Try a different search term')
              : t('knowledge.addSqlPairsHint', 'Add question-SQL pairs to help the AI generate accurate queries')
          }
        />
      ) : (
        <Table columns={columns} data={filtered} sortable />
      )}
    </div>
  )
}
