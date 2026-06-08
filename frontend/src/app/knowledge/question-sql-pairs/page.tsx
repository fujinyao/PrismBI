'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { knowledgeApi } from '@/lib/api'
import { SqlPairList } from '@/components/knowledge/SqlPairList'
import { SqlPairForm } from '@/components/knowledge/SqlPairForm'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useI18nStore } from '@/stores/i18nStore'
import { useProjectStore } from '@/stores/projectStore'
import { KnowledgeShell } from '@/components/knowledge/KnowledgeShell'

export default function QuestionSqlPairsPage() {
  const [showForm, setShowForm] = useState(false)
  const [editItem, setEditItem] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const t = useI18nStore((s) => s.t)
  const currentProject = useProjectStore((s) => s.currentProject)
  const queryClient = useQueryClient()
  const projectId = currentProject?.id

  const {
    data: sqlPairs,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ['sql-pairs', projectId],
    queryFn: () => knowledgeApi.sqlPairs.list({ project_id: projectId! }),
    enabled: Boolean(projectId),
  })

  const clearError = () => setError(null)

  const createMutation = useMutation({
    mutationFn: (data: { question: string; sql: string; description: string; category: string; scope: string }) =>
      knowledgeApi.sqlPairs.create({ ...data, project_id: projectId! }),
    onSuccess: () => {
      clearError()
      queryClient.invalidateQueries({ queryKey: ['sql-pairs', projectId] })
    },
    onError: (err: any) => {
      setError(err?.message || t('knowledge.sqlPairCreateFailed', 'Failed to create SQL pair'))
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: { id: number; question?: string; sql?: string; description?: string; category?: string; scope?: string }) =>
      knowledgeApi.sqlPairs.update(data.id, data),
    onSuccess: () => {
      clearError()
      queryClient.invalidateQueries({ queryKey: ['sql-pairs', projectId] })
    },
    onError: (err: any) => {
      setError(err?.message || t('knowledge.sqlPairUpdateFailed', 'Failed to update SQL pair'))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => knowledgeApi.sqlPairs.delete(id),
    onSuccess: () => {
      clearError()
      queryClient.invalidateQueries({ queryKey: ['sql-pairs', projectId] })
    },
    onError: (err: any) => {
      setError(err?.message || t('knowledge.sqlPairDeleteFailed', 'Failed to delete SQL pair'))
    },
  })

  if (isLoading) {
    return (
      <KnowledgeShell>
      <div className="flex flex-col gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
      </KnowledgeShell>
    )
  }

  if (isError) {
    return (
      <KnowledgeShell>
        <ErrorToast
          message={t('knowledge.sqlPairLoadFailed', 'Failed to load SQL pairs')}
          onRetry={() => queryClient.invalidateQueries({ queryKey: ['sql-pairs', projectId] })}
        />
      </KnowledgeShell>
    )
  }

  return (
    <KnowledgeShell>
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="primary" disabled={!projectId} onClick={() => { setEditItem(null); setShowForm(true) }}>
          {t('knowledge.addSqlPair', 'Add SQL Pair')}
        </Button>
      </div>

      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      {showForm && (
        <SqlPairForm
          open
          pair={editItem}
          onClose={() => setShowForm(false)}
          onSave={(data) => {
            if (data.id) {
              updateMutation.mutate(data, { onSuccess: () => setShowForm(false) })
            } else if (projectId) {
              createMutation.mutate(data, { onSuccess: () => setShowForm(false) })
            }
          }}
        />
      )}

      {sqlPairs && (sqlPairs as any).items?.length > 0 ? (
        <SqlPairList
          pairs={(sqlPairs as any).items}
          loading={false}
          onEdit={(item: any) => {
            setEditItem(item)
            setShowForm(true)
          }}
          onDelete={(id: any) => {
            if (confirm(t('knowledge.deleteSqlPairConfirm', 'Delete this SQL pair?'))) {
              deleteMutation.mutate(Number(id))
            }
          }}
        />
      ) : (
        <EmptyState
          message={projectId ? t('knowledge.noSqlPairs', 'No SQL pairs yet. Add your first question-SQL pair.') : t('knowledge.noProject', 'Select a project to manage project knowledge.')}
          action={projectId ? { label: t('knowledge.addFirstSqlPair', 'Add first SQL pair'), onClick: () => setShowForm(true) } : undefined}
        />
      )}
    </div>
    </KnowledgeShell>
  )
}
