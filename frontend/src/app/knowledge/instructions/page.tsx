'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { knowledgeApi } from '@/lib/api'
import { InstructionList } from '@/components/knowledge/InstructionList'
import { InstructionForm } from '@/components/knowledge/InstructionForm'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useI18nStore } from '@/stores/i18nStore'
import { useProjectStore } from '@/stores/projectStore'
import { KnowledgeShell } from '@/components/knowledge/KnowledgeShell'

export default function KnowledgeInstructionsPage() {
  const [showForm, setShowForm] = useState(false)
  const [editItem, setEditItem] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const t = useI18nStore((s) => s.t)
  const currentProject = useProjectStore((s) => s.currentProject)
  const queryClient = useQueryClient()
  const projectId = currentProject?.id

  const {
    data: instructions,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ['instructions', projectId],
    queryFn: () => knowledgeApi.instructions.list({ project_id: projectId! }),
    enabled: Boolean(projectId),
  })

  const clearError = () => setError(null)

  const createMutation = useMutation({
    mutationFn: (data: { text: string; category: string; scope: string; priority: number }) =>
      knowledgeApi.instructions.create({ ...data, project_id: projectId! }),
    onSuccess: () => {
      clearError()
      queryClient.invalidateQueries({ queryKey: ['instructions', projectId] })
    },
    onError: (err: any) => {
      setError(err?.message || t('knowledge.instructionCreateFailed', 'Failed to create instruction'))
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: { id: number; text?: string; category?: string; scope?: string; priority?: number }) =>
      knowledgeApi.instructions.update(data.id, data),
    onSuccess: () => {
      clearError()
      queryClient.invalidateQueries({ queryKey: ['instructions', projectId] })
    },
    onError: (err: any) => {
      setError(err?.message || t('knowledge.instructionUpdateFailed', 'Failed to update instruction'))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => knowledgeApi.instructions.delete(id),
    onSuccess: () => {
      clearError()
      queryClient.invalidateQueries({ queryKey: ['instructions', projectId] })
    },
    onError: (err: any) => {
      setError(err?.message || t('knowledge.instructionDeleteFailed', 'Failed to delete instruction'))
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
          message={t('knowledge.instructionLoadFailed', 'Failed to load instructions')}
          onRetry={() => queryClient.invalidateQueries({ queryKey: ['instructions', projectId] })}
        />
      </KnowledgeShell>
    )
  }

  return (
    <KnowledgeShell>
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="primary" disabled={!projectId} onClick={() => { setEditItem(null); setShowForm(true) }}>
          {t('knowledge.createInstruction', 'Create Instruction')}
        </Button>
      </div>

      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      {showForm && (
        <InstructionForm
          open
          instruction={editItem}
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

      {instructions && (instructions as any).items?.length > 0 ? (
        <InstructionList
          instructions={(instructions as any).items}
          loading={false}
          onEdit={(item: any) => {
            setEditItem(item)
            setShowForm(true)
          }}
          onDelete={(id: any) => {
            if (confirm(t('knowledge.deleteInstructionConfirm', 'Delete this instruction?'))) {
              deleteMutation.mutate(Number(id))
            }
          }}
        />
      ) : (
        <EmptyState
          message={projectId ? t('knowledge.noInstructions', 'No instructions yet. Create your first instruction.') : t('knowledge.noProject', 'Select a project to manage project knowledge.')}
          action={projectId ? { label: t('knowledge.createFirstInstruction', 'Create first instruction'), onClick: () => setShowForm(true) } : undefined}
        />
      )}
    </div>
    </KnowledgeShell>
  )
}
