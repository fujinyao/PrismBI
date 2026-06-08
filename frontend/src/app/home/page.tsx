'use client'

import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { threadsApi, recommendationsApi } from '@/lib/api'
import { createTemporaryThread } from '@/lib/tempThreads'
import { ThreadList } from '@/components/home/ThreadList'
import { PromptBar } from '@/components/home/PromptBar'
import { RecommendedQuestions } from '@/components/home/RecommendedQuestions'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { useProjectStore } from '@/stores/projectStore'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'
import { useBrandingStore } from '@/stores/brandingStore'
import { useAuthStore } from '@/stores/authStore'

export default function HomePage() {
  const router = useRouter()
  const { toast } = useToast()
  const currentProject = useProjectStore((s) => s.currentProject)
  const projectsLoading = useProjectStore((s) => s.loading)
  const projectsLoaded = useProjectStore((s) => s.loaded)
  const fetchProjects = useProjectStore((s) => s.fetchProjects)
  const t = useI18nStore((s) => s.t)
  const locale = useI18nStore((s) => s.locale)
  const appName = useBrandingStore((s) => s.appName)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canReadRecommendations = hasPermission('recommendations', 'read')

  const {
    data: recsData,
    isLoading: recsLoading,
    isError: recsError,
    isFetched: recsFetched,
    refetch: refetchRecommendations,
  } = useQuery({
    queryKey: ['recommendations', currentProject?.id, locale],
    queryFn: () => recommendationsApi.list({ project_id: currentProject?.id, max_results: 6, language: locale, include_generated: false }),
    enabled: !!currentProject?.id && canReadRecommendations,
  })

  const recommendations = recsData?.recommendations ?? []
  const shouldCheckBootstrapStatus = Boolean(currentProject?.id)
    && canReadRecommendations
    && recsFetched
    && !recsLoading
    && !recsError
    && recommendations.length === 0
  const { data: bootstrapStatus, isLoading: bootstrapLoading } = useQuery({
    queryKey: ['recommendations-bootstrap-status', currentProject?.id],
    queryFn: () => recommendationsApi.bootstrapStatus(currentProject?.id as number),
    enabled: shouldCheckBootstrapStatus,
    refetchInterval: (query) => {
      if (!shouldCheckBootstrapStatus) return false
      const status = String((query.state.data as { status?: string } | undefined)?.status || '').toLowerCase()
      return (status === 'pending' || status === 'running') ? 3000 : false
    },
  })

  const isBootstrappingRecommendations = Boolean(currentProject?.id) && Boolean(bootstrapStatus?.is_bootstrapping)
  const isPreparingRecommendations = shouldCheckBootstrapStatus && (bootstrapLoading || isBootstrappingRecommendations)
  const bootstrapFailed = shouldCheckBootstrapStatus && (bootstrapStatus?.status || '').toLowerCase() === 'failed'

  useEffect(() => {
    if (!projectsLoaded && !projectsLoading) fetchProjects()
  }, [fetchProjects, projectsLoaded, projectsLoading])

  useEffect(() => {
    if (!shouldCheckBootstrapStatus) return
    if ((bootstrapStatus?.status || '').toLowerCase() !== 'completed') return
    if ((bootstrapStatus?.active_recommendations || 0) <= 0) return
    void refetchRecommendations()
  }, [
    bootstrapStatus?.active_recommendations,
    bootstrapStatus?.status,
    refetchRecommendations,
    shouldCheckBootstrapStatus,
  ])

  const waitingForProjects = !projectsLoaded || projectsLoading
  const showRecommendationSkeleton = waitingForProjects || recsLoading || isPreparingRecommendations

  const handleAsk = async (question: string, previewRowLimit: number) => {
    try {
      if (!currentProject?.id) {
        if (waitingForProjects) return
        const thread = createTemporaryThread(question, previewRowLimit)
        router.push(`/home/${thread.id}?question=${encodeURIComponent(question)}`)
        return
      }
      const thread = await threadsApi.create(currentProject.id, undefined, previewRowLimit)
      router.push(`/home/${thread.id}?question=${encodeURIComponent(question)}`)
    } catch (err) {
      toast(err instanceof Error ? err.message : t('threads.createFailed', 'Failed to create thread'), 'error')
    }
  }

  return (
    <div className="flex h-full gap-[5px]">
      <ThreadList projectId={currentProject?.id} />

      <div className="flex min-w-0 flex-1 flex-col rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div className="flex-1 overflow-y-auto">
          <div className="flex min-h-full flex-col items-center justify-center px-5 py-8">
            {showRecommendationSkeleton ? (
              <div className="w-full max-w-xl space-y-3">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-3/4" />
                {isPreparingRecommendations && !waitingForProjects && !recsLoading ? (
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {t('home.preparingRecommendations', 'Preparing recommended questions for this project...')}
                  </p>
                ) : null}
              </div>
            ) : recommendations.length > 0 ? (
              <RecommendedQuestions
                recommendations={recommendations}
                onSelect={(question) => handleAsk(question, 20)}
                className="w-full max-w-xl"
              />
            ) : (
              <EmptyState
                message={currentProject ? t('home.askQuestion', 'Ask a question about your data') : t('home.askAnything', `Ask ${appName} anything`, { appName })}
                description={currentProject
                  ? bootstrapFailed
                    ? t('home.recommendationsBootstrapFailed', 'Recommended questions are temporarily unavailable. You can still ask your question directly below.')
                    : t('home.askDescription', 'Type a question below and I\'ll generate SQL, charts, and insights for you.')
                  : t('home.noProjectLlmDesc', `No project is selected, so ${appName} will answer as a general LLM assistant.`, { appName })}
              />
            )}
          </div>
        </div>

        <PromptBar onSubmit={handleAsk} disabled={waitingForProjects} className="rounded-b-xl" />
      </div>
    </div>
  )
}
