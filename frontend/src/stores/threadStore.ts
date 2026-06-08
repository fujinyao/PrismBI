import { create } from 'zustand'
import { threadsApi } from '@/lib/api'

interface ThreadResponse {
  id: number
  thread_id: number
  question: string
  sql?: string
  summary?: string
  columns?: string[]
  rows?: Record<string, unknown>[]
  chart_spec?: Record<string, unknown>
  created_at?: string
}

interface Thread {
  id: number
  project_id: number
  summary?: string
  response_count?: number
  created_at?: string
  updated_at?: string
  responses: ThreadResponse[]
}

interface StreamContent {
  type: 'text' | 'sql' | 'chart' | 'state' | 'result' | 'error'
  content?: string
  state?: string
  sql?: string
  chart_spec?: Record<string, unknown>
  data?: Record<string, unknown>[]
  summary?: string
  message?: string
}

interface ThreadState {
  threads: Thread[]
  currentThread: Thread | null
  streamingContent: StreamContent | null
  streamBuffer: string
  loading: boolean
  streaming: boolean
  error: string | null
  fetchThreads: (projectId?: number) => Promise<void>
  fetchThread: (id: number) => Promise<void>
  createThread: (projectId: number, summary?: string) => Promise<number>
  deleteThread: (id: number) => Promise<void>
  setCurrentThread: (thread: Thread | null) => void
  addStreamContent: (content: StreamContent) => void
  clearStream: () => void
  appendToBuffer: (text: string) => void
  clearBuffer: () => void
  reset: () => void
}

export const useThreadStore = create<ThreadState>()((set, get) => ({
  threads: [],
  currentThread: null,
  streamingContent: null,
  streamBuffer: '',
  loading: false,
  streaming: false,
  error: null,

  fetchThreads: async (projectId?: number) => {
    set({ loading: true, error: null })
    try {
      const res = await threadsApi.list({ project_id: projectId })
      const items = Array.isArray(res.items) ? res.items : []
      set({ threads: items as Thread[], loading: false })
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : 'Failed to fetch threads' })
    }
  },

  fetchThread: async (id: number) => {
    set({ loading: true, error: null })
    try {
      const data = await threadsApi.get(id)
      const thread: Thread = {
        id: data.id,
        project_id: data.project_id,
        summary: data.summary,
        response_count: data.response_count,
        created_at: data.created_at,
        updated_at: data.updated_at,
        responses: (data.responses ?? []).map((r) => ({
          id: r.id,
          thread_id: r.thread_id ?? data.id,
          question: r.question,
          sql: r.sql ?? undefined,
          columns: r.answerDetail?.columns,
          rows: r.answerDetail?.rows,
          chart_spec: (r.chartDetail as Record<string, unknown> | undefined) ?? undefined,
          created_at: r.created_at,
        })),
      }
      set({ currentThread: thread, loading: false, error: null })
    } catch (e) {
      set({ currentThread: null, loading: false, error: e instanceof Error ? e.message : 'Failed to fetch thread' })
    }
  },

  createThread: async (projectId: number, summary?: string) => {
    try {
      const res = await threadsApi.create(projectId, summary)
      await get().fetchThreads(projectId)
      return res.id
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to create thread' })
      throw e
    }
  },

  deleteThread: async (id: number) => {
    try {
      await threadsApi.delete(id)
      const { threads, currentThread } = get()
      set({
        threads: threads.filter((t) => t.id !== id),
        currentThread: currentThread?.id === id ? null : currentThread,
      })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to delete thread' })
      throw e
    }
  },

  setCurrentThread: (thread: Thread | null) => {
    set({ currentThread: thread, streamingContent: null, streamBuffer: '' })
  },

  addStreamContent: (content: StreamContent) => {
    set({ streamingContent: content, streaming: content.type !== 'result' && content.type !== 'error' })
  },

  clearStream: () => {
    set({ streamingContent: null, streamBuffer: '', streaming: false })
  },

  appendToBuffer: (text: string) => {
    set((state) => ({ streamBuffer: state.streamBuffer + text }))
  },

  clearBuffer: () => {
    set({ streamBuffer: '' })
  },

  reset: () => {
    set({
      threads: [],
      currentThread: null,
      streamingContent: null,
      streamBuffer: '',
      streaming: false,
      loading: false,
      error: null,
    })
  },
}))
