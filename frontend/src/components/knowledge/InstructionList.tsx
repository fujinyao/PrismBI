'use client'

import { useState, useMemo } from 'react'
import { Table, type Column } from '@/components/ui/Table'
import { Tag } from '@/components/ui/Tag'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { EmptyState } from '@/components/ui/EmptyState'
import { formatDate, truncate } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface Instruction {
  id: number
  text: string
  category: string | null
  scope: 'global' | 'project'
  priority: number
  created_at: string
}

interface InstructionListProps {
  instructions: Instruction[]
  loading: boolean
  onEdit: (i: Instruction) => void
  onDelete: (id: number) => void
  searchQuery?: string
}

const priorityVariant = (p: number) => {
  if (p >= 3) return 'error' as const
  if (p >= 2) return 'warning' as const
  if (p >= 1) return 'info' as const
  return 'default' as const
}

const scopeBadge = (s: Instruction['scope'], t: (key: string, fallback: string) => string) => {
  return s === 'global'
    ? <Tag variant="info">{t('knowledge.scope.global', 'Global')}</Tag>
    : <Tag variant="default">{t('knowledge.scope.project', 'Project')}</Tag>
}

export function InstructionList({ instructions, loading, onEdit, onDelete, searchQuery = '' }: InstructionListProps) {
  const t = useI18nStore((s) => s.t)
  const [search, setSearch] = useState(searchQuery)

  const filtered = useMemo(() => {
    if (!search.trim()) return instructions
    const q = search.toLowerCase()
    return instructions.filter(
      (i) =>
        i.text.toLowerCase().includes(q) ||
        (i.category ?? '').toLowerCase().includes(q),
    )
  }, [instructions, search])

  const columns: Column<Instruction>[] = useMemo(
    () => [
      {
        key: 'text',
        header: t('knowledge.instruction', 'Instruction'),
        sortable: true,
        render: (i) => (
          <span className="text-gray-900 dark:text-gray-100" title={i.text}>
            {truncate(i.text, 80)}
          </span>
        ),
      },
      {
        key: 'category',
        header: t('knowledge.category', 'Category'),
        render: (i) => (
          <Tag variant="default">{i.category ?? '-'}</Tag>
        ),
      },
      {
        key: 'scope',
        header: t('knowledge.scope', 'Scope'),
        render: (i) => scopeBadge(i.scope, t),
      },
      {
        key: 'priority',
        header: t('knowledge.priority', 'Priority'),
        sortable: true,
        render: (i) => (
          <Tag variant={priorityVariant(i.priority)}>{i.priority}</Tag>
        ),
      },
      {
        key: 'created_at',
        header: t('knowledge.created', 'Created'),
        sortable: true,
        render: (i) => (
          <span className="text-gray-500 dark:text-gray-400 text-sm">
            {formatDate(i.created_at)}
          </span>
        ),
      },
      {
        key: 'actions',
        header: '',
        className: 'w-24 text-right',
        render: (i) => (
          <div className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => onEdit(i)}>{t('knowledge.edit', 'Edit')}</Button>
            <Button size="sm" variant="ghost" onClick={() => onDelete(i.id)}>{t('knowledge.delete', 'Delete')}</Button>
          </div>
        ),
      },
    ],
    [onEdit, onDelete, t],
  )

  return (
    <div className="space-y-4">
      <div className="max-w-sm">
        <Input
          placeholder={t('knowledge.searchInstructions', 'Search instructions...')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {!loading && filtered.length === 0 ? (
        <EmptyState
          title={t('knowledge.noInstructionsTitle', 'No instructions found')}
          description={search ? t('knowledge.tryDifferentSearch', 'Try a different search term') : t('knowledge.addInstructionHint', 'Create your first instruction to guide the AI')}
        />
      ) : (
        <Table
          columns={columns}
          data={filtered}
          loading={loading}
          sortable
        />
      )}
    </div>
  )
}
