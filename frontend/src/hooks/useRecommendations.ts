'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { recommendationsApi } from '@/lib/api'

export function useRecommendations(params?: {
  project_id?: number
  context?: string
  max_results?: number
  types?: string
  language?: string
  include_generated?: boolean
  refresh_generated?: boolean
}) {
  const query = useQuery({
    queryKey: ['recommendations', params],
    queryFn: () => recommendationsApi.list(params),
  })

  return {
    recommendations: query.data?.recommendations ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  }
}

export function useRecommendationsOnboarding(params?: {
  project_id?: number
  max_results?: number
  language?: string
}) {
  return useQuery({
    queryKey: ['recommendations-onboarding', params],
    queryFn: () => recommendationsApi.onboarding(params),
  })
}

export function useSubmitRating() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: { recommendation_id: number; score: number; context?: string }) =>
      recommendationsApi.rate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recommendations'] })
      queryClient.invalidateQueries({ queryKey: ['recommendations-onboarding'] })
      queryClient.invalidateQueries({ queryKey: ['recommendation-ratings'] })
    },
  })
}

export function useSubmitFeedback() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: { recommendation_id: number; action: 'accept' | 'dismiss'; context?: string }) =>
      recommendationsApi.feedback(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recommendations'] })
    },
  })
}

export function useRecommendationRatings(params?: {
  project_id?: number
  source_layer?: string
  from?: string
  to?: string
}) {
  return useQuery({
    queryKey: ['recommendation-ratings', params],
    queryFn: () => recommendationsApi.ratings(params),
  })
}
