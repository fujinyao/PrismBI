'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { recommendationsApi, settingsApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { useProjectStore } from '@/stores/projectStore'
import { RecommenderSettings } from '@/components/recommendation/RecommenderSettings'
import { CatalogManager } from '@/components/recommendation/CatalogManager'
import { HintEditor } from '@/components/recommendation/HintEditor'
import { Tabs } from '@/components/ui/Tabs'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorToast } from '@/components/ui/ErrorToast'

export default function RecommendationsPage() {
  const t = useI18nStore((s) => s.t)
  const currentProject = useProjectStore((s) => s.currentProject)
  const queryClient = useQueryClient()

  const TABS = [
    { key: 'settings', label: t('recommendation.settings', 'Settings') },
    { key: 'catalog', label: t('recommendation.catalog', 'Catalog') },
    { key: 'hints', label: t('recommendation.hints', 'Hints') },
  ]

  const [activeTab, setActiveTab] = useState('settings')
  const [error, setError] = useState<string | null>(null)

  const {
    data: settings,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['recommendation-settings'],
    queryFn: () => settingsApi.getRecommendations(),
  })

  const { data: hintsData } = useQuery({
    queryKey: ['recommendation-hints', currentProject?.id],
    queryFn: () => recommendationsApi.hints.list(currentProject?.id),
    enabled: Boolean(currentProject?.id),
  })

  const projectId = currentProject?.id

  const createHint = useMutation({
    mutationFn: (hint: { text: string; category: string; weight: number }) => {
      if (!projectId) throw new Error('No active project')
      return recommendationsApi.hints.create(projectId, {
        hint_text: hint.text,
        confidence: hint.weight,
        source_query: hint.category,
      })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recommendation-hints', projectId] }),
  })

  const updateHint = useMutation({
    mutationFn: ({ id, hint }: { id: string; hint: any }) => {
      if (!projectId) throw new Error('No active project')
      return recommendationsApi.hints.update(projectId, Number(id), {
        hint_text: hint.text,
        source_query: hint.category,
        confidence: hint.weight,
      })
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recommendation-hints', projectId] }),
  })

  const deleteHint = useMutation({
    mutationFn: (id: string) => {
      if (!projectId) throw new Error('No active project')
      return recommendationsApi.hints.delete(projectId, Number(id))
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['recommendation-hints', projectId] }),
  })

  const normalizedSettings = {
    enabled: Boolean((settings as any)?.recommender_catalog_auto_learn ?? true),
    minRelevance: Math.round(Number((settings as any)?.recommender_low_score_threshold ?? 2) * 20),
    maxResults: Number((settings as any)?.recommender_max_results ?? 5),
  }

  const saveSettings = async (next: typeof normalizedSettings) => {
    await settingsApi.recommendations({
      max_results: next.maxResults,
      low_score_threshold: Math.max(1, Math.min(5, Math.round(next.minRelevance / 20))),
      auto_recover: next.enabled,
    })
    await refetch()
  }

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full max-w-md" />
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('recommendation.failedToLoad', 'Failed to load recommendation settings')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  const renderTab = () => {
    switch (activeTab) {
      case 'settings':
        return <RecommenderSettings settings={normalizedSettings} onSave={saveSettings} />
      case 'catalog':
        return <CatalogManager projectId={currentProject?.id} />
      case 'hints':
        return <HintEditor hints={((hintsData as any)?.hints ?? []).map((hint: any) => ({
          id: String(hint.id),
          text: hint.text ?? hint.hint_text ?? '',
          category: hint.source_query ?? 'general',
          weight: Number(hint.weight ?? hint.confidence ?? 1),
        }))} onAdd={(hint) => createHint.mutate(hint)} onUpdate={(id, hint) => updateHint.mutate({ id, hint })} onDelete={(id) => deleteHint.mutate(id)} />
      default:
        return null
    }
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <Tabs tabs={TABS} activeKey={activeTab} onChange={setActiveTab} />

      <div className="mt-6">{renderTab()}</div>
    </div>
  )
}
