import type { ThreadResponse } from '@/lib/api'

export interface TemporaryThread {
  id: string
  summary: string
  preview_row_limit: number
  responses: ThreadResponse[]
  created_at: string
  updated_at: string
}

const STORAGE_KEY = 'prismbi-temp-thread'

function isBrowser() {
  return typeof window !== 'undefined'
}

function fallbackThread(id?: string): TemporaryThread {
  const now = new Date().toISOString()
  return {
    id: id?.startsWith('temp-') ? id : `temp-${Date.now()}`,
    summary: '',
    preview_row_limit: 20,
    responses: [],
    created_at: now,
    updated_at: now,
  }
}

export function getTemporaryThread(id?: string): TemporaryThread {
  if (!isBrowser()) return fallbackThread(id)
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as TemporaryThread
      if (parsed?.id?.startsWith('temp-') && (!id || parsed.id === id)) return { ...fallbackThread(id), ...parsed }
    }
  } catch {
    /* ignore */
  }
  const thread = fallbackThread(id)
  saveTemporaryThread(thread)
  return thread
}

export function saveTemporaryThread(thread: TemporaryThread) {
  if (!isBrowser()) return
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(thread))
  } catch {
    sessionStorage.removeItem(STORAGE_KEY)
  }
}

export function createTemporaryThread(question?: string, previewRowLimit = 20): TemporaryThread {
  const now = new Date().toISOString()
  const thread: TemporaryThread = {
    id: `temp-${Date.now()}`,
    summary: question?.trim().slice(0, 24) || '',
    preview_row_limit: previewRowLimit,
    responses: [],
    created_at: now,
    updated_at: now,
  }
  saveTemporaryThread(thread)
  return thread
}

export function appendTemporaryResponse(response: ThreadResponse, previewRowLimit: number): TemporaryThread {
  const thread = getTemporaryThread()
  const now = new Date().toISOString()
  const question = response.question?.trim() || ''
  const next: TemporaryThread = {
    ...thread,
    summary: thread.summary || question.slice(0, 24),
    preview_row_limit: previewRowLimit,
    responses: [...thread.responses, response],
    updated_at: now,
  }
  saveTemporaryThread(next)
  return next
}

export function clearTemporaryThread() {
  if (!isBrowser()) return
  sessionStorage.removeItem(STORAGE_KEY)
}
