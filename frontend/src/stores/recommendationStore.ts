import { create } from 'zustand'
import { recommendationsApi } from '@/lib/api'

interface Recommendation {
  id: number
  question: string
  type: string
  source: string
  score: number
  llm_explanation?: string
  model_names?: string[]
}

interface Rating {
  id: number
  recommendation_id?: number
  score: number
  source_layer?: string
  recommend_type?: string
  created_at?: string
}

interface RecommendationState {
  recommendations: Recommendation[]
  onboardingQuestions: { question: string; category: string; model_names?: string[]; model_name?: string }[]
  ratings: Rating[]
  scoreState: Record<number, number>
  loading: boolean
  error: string | null
  fetchRecommendations: (params?: { project_id?: number; context?: string; max_results?: number; types?: string; language?: string; include_generated?: boolean; refresh_generated?: boolean }) => Promise<void>
  fetchOnboardingQuestions: (params?: { project_id?: number; max_results?: number; language?: string }) => Promise<void>
  fetchRatings: (params?: { source_layer?: string; from?: string; to?: string }) => Promise<void>
  submitRating: (recommendationId: number, score: number, context?: number | string) => Promise<void>
  submitFeedback: (recommendationId: number, action: 'accept' | 'dismiss', context?: number | string) => Promise<void>
  setScoreState: (id: number, score: number) => void
  reset: () => void
}

export const useRecommendationStore = create<RecommendationState>()((set, get) => ({
  recommendations: [],
  onboardingQuestions: [],
  ratings: [],
  scoreState: {},
  loading: false,
  error: null,

  fetchRecommendations: async (params) => {
    set({ loading: true, error: null })
    try {
      const res = await recommendationsApi.list(params)
      set({ recommendations: res.recommendations ?? [], loading: false })
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : 'Failed to fetch recommendations' })
    }
  },

  fetchOnboardingQuestions: async (params) => {
    try {
      const res = await recommendationsApi.onboarding(params)
      set({ onboardingQuestions: res.questions ?? [] })
    } catch (e) {
      set({ onboardingQuestions: [], error: e instanceof Error ? e.message : 'Failed to fetch onboarding questions' })
    }
  },

  fetchRatings: async (params) => {
    try {
      const res = await recommendationsApi.ratings(params)
      const items = Array.isArray(res.ratings) ? res.ratings : []
      set({ ratings: items as Rating[] })
    } catch (e) {
      set({ ratings: [], error: e instanceof Error ? e.message : 'Failed to fetch ratings' })
    }
  },

  submitRating: async (recommendationId: number, score: number, context?: number | string) => {
    try {
      await recommendationsApi.rate({ recommendation_id: recommendationId, score, context })
      set((state) => ({
        scoreState: { ...state.scoreState, [recommendationId]: score },
      }))
    } catch {
      throw new Error('Failed to submit rating')
    }
  },

  submitFeedback: async (recommendationId: number, action: 'accept' | 'dismiss', context?: number | string) => {
    try {
      await recommendationsApi.feedback({ recommendation_id: recommendationId, action, context })
    } catch {
      throw new Error('Failed to submit feedback')
    }
  },

  setScoreState: (id: number, score: number) => {
    set((state) => ({
      scoreState: { ...state.scoreState, [id]: score },
    }))
  },

  reset: () => {
    set({
      recommendations: [],
      onboardingQuestions: [],
      ratings: [],
      scoreState: {},
      loading: false,
      error: null,
    })
  },
}))
