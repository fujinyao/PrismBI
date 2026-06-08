'use client'

import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRouter, usePathname } from 'next/navigation'
import { threadsApi } from '@/lib/api'
import { clearTemporaryThread, createTemporaryThread, getTemporaryThread } from '@/lib/tempThreads'
import { cn, displayThreadSummary, formatDate } from '@/lib/utils'
import { SkeletonRow } from '@/components/ui/Skeleton'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'

interface Thread {
  id: number | string
  project_id?: number
  summary?: string
  summary_manual?: boolean
  response_count?: number
  preview_row_limit?: number
  created_at?: string
  updated_at?: string
}

interface ThreadListProps {
  projectId?: number
  className?: string
}

const THREAD_LIST_SHORT_CACHE_MS = 5000

export function ThreadList({ projectId, className }: ThreadListProps) {
  const router = useRouter()
  const pathname = usePathname()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const t = useI18nStore((s) => s.t)
  const [search, setSearch] = useState('')
  const [editingThreadId, setEditingThreadId] = useState<string | number | null>(null)
  const [editingSummary, setEditingSummary] = useState('')
  const [editingOriginalSummary, setEditingOriginalSummary] = useState('')
  const searchComposing = useRef(false)
  const editComposing = useRef(false)
  const isEmptyProject = !projectId

  const { data, isLoading } = useQuery({
    queryKey: ['threads', projectId],
    queryFn: () => threadsApi.list({ project_id: projectId, page_size: 50 }),
    enabled: Boolean(projectId),
    staleTime: THREAD_LIST_SHORT_CACHE_MS,
    gcTime: THREAD_LIST_SHORT_CACHE_MS * 20,
    refetchOnWindowFocus: false,
  })

  const temporaryThread = isEmptyProject ? getTemporaryThread() : null
  const threads = isEmptyProject
    ? (temporaryThread && temporaryThread.responses.length > 0 ? [{
        id: temporaryThread.id,
        summary: temporaryThread.summary,
        response_count: temporaryThread.responses.length,
        preview_row_limit: temporaryThread.preview_row_limit,
        created_at: temporaryThread.created_at,
        updated_at: temporaryThread.updated_at,
      }] as Thread[] : [])
    : (data?.items ?? []) as Thread[]

  const filteredThreads = search
    ? threads.filter((thread) =>
        displayThreadSummary(thread.summary, t).toLowerCase().includes(search.toLowerCase()),
      )
    : threads

  const currentThreadId = (() => {
    const match = pathname.match(/\/home\/(\d+)/)
    return match ? Number(match[1]) || null : null
  })()
  const currentTempThreadId = pathname.match(/\/home\/(temp-[^/?#]+)/)?.[1] ?? null

  const createThread = useMutation({
    mutationFn: () => threadsApi.create(projectId, undefined, 20),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['threads', projectId] })
      router.push(`/home/${res.id}`)
    },
    onError: (err) => {
      toast(err instanceof Error ? err.message : t('threads.createFailed', 'Failed to create thread'), 'error')
    },
  })

  const handleCreateThread = () => {
    if (isEmptyProject) {
      const thread = createTemporaryThread(undefined, 20)
      router.push(`/home/${thread.id}`)
      return
    }
    createThread.mutate()
  }

  const renameThread = useMutation({
    mutationFn: ({ id, summary }: { id: number; summary: string }) => threadsApi.update(id, { summary }),
    onSuccess: (_res, variables) => {
      queryClient.invalidateQueries({ queryKey: ['threads', projectId] })
      queryClient.invalidateQueries({ queryKey: ['thread', variables.id] })
      setEditingThreadId(null)
      setEditingSummary('')
      setEditingOriginalSummary('')
    },
    onError: (err) => {
      toast(err instanceof Error ? err.message : t('threads.renameFailed', 'Failed to rename thread'), 'error')
    },
  })

  const startRename = (thread: Thread) => {
    if (isEmptyProject) return
    const summary = displayThreadSummary(thread.summary, t)
    setEditingThreadId(thread.id)
    setEditingSummary(summary)
    setEditingOriginalSummary(summary)
  }

  const commitRename = () => {
    if (editingThreadId === null || typeof editingThreadId !== 'number' || editComposing.current || renameThread.isPending) return
    const summary = editingSummary.trim()
    if (!summary || summary === editingOriginalSummary) {
      setEditingThreadId(null)
      setEditingSummary('')
      setEditingOriginalSummary('')
      return
    }
    renameThread.mutate({ id: editingThreadId, summary })
  }

  const deleteThread = useMutation({
    mutationFn: (id: number) => threadsApi.delete(id),
    onSuccess: (_res, deletedId) => {
      queryClient.removeQueries({ queryKey: ['thread', deletedId] })
      queryClient.invalidateQueries({ queryKey: ['threads', projectId] })
      if (currentThreadId === deletedId) router.push('/home')
    },
    onError: (err) => {
      toast(err instanceof Error ? err.message : t('threads.deleteFailed', 'Failed to delete thread'), 'error')
    },
  })

  return (
    <aside
      className={cn(
        'flex w-80 shrink-0 flex-col rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900',
        className,
      )}
    >
      <div className="px-2 py-1.5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t('threads.title', 'Threads')}</p>
          </div>
          <button
            type="button"
            onClick={handleCreateThread}
            disabled={createThread.isPending}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-primary-50 disabled:opacity-50 dark:hover:bg-primary-900/20"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            {t('common.new', 'New')}
          </button>
        </div>
      </div>

      <div className="px-2 py-1.5">
        <input
          placeholder={t('threads.search', 'Search threads...')}
          onInput={(e) => { if (!searchComposing.current && !(e.nativeEvent as InputEvent).isComposing) setSearch((e.target as HTMLInputElement).value) }}
          onCompositionStart={() => { searchComposing.current = true }}
          onCompositionEnd={(e) => { searchComposing.current = false; setSearch((e.target as HTMLInputElement).value) }}
          className="block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder:text-gray-500"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
        {!isEmptyProject && isLoading ? (
          <SkeletonRow count={5} className="px-3" />
        ) : filteredThreads.length === 0 ? (
          <div className="px-3 py-8 text-center text-sm text-gray-500">
            {search ? t('threads.noMatch', 'No matching threads') : t('threads.empty', 'No threads yet')}
          </div>
        ) : (
          <div className="space-y-0.5">
            {filteredThreads.map((thread) => {
              const isActive = isEmptyProject ? currentTempThreadId === temporaryThread?.id : currentThreadId === thread.id
              return (
                <div
                  key={thread.id}
                  className="group relative"
                >
                  {editingThreadId === thread.id ? (
                    <input
                      value={editingSummary}
                      autoFocus
                      onChange={(event) => setEditingSummary(event.target.value)}
                      onCompositionStart={() => { editComposing.current = true }}
                      onCompositionEnd={(event) => { editComposing.current = false; setEditingSummary(event.currentTarget.value) }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && !editComposing.current && !(event.nativeEvent as KeyboardEvent).isComposing) commitRename()
                        if (event.key === 'Escape') setEditingThreadId(null)
                      }}
                      onBlur={commitRename}
                      className="w-full rounded-lg border border-primary-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:ring-2 focus:ring-primary-300 dark:border-primary-700 dark:bg-gray-800 dark:text-gray-100"
                    />
                  ) : (
                    <button
                      onClick={() => router.push(isEmptyProject ? `/home/${temporaryThread?.id ?? createTemporaryThread().id}` : `/home/${thread.id}`)}
                      onDoubleClick={() => startRename(thread)}
                      className={cn(
                        'flex w-full items-center rounded-lg px-3 py-2.5 text-left text-sm transition-colors',
                        isActive
                          ? 'bg-primary-50 text-primary dark:bg-primary-900/20 dark:text-primary-300'
                          : 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800',
                      )}
                    >
                      <div className="flex-1 overflow-hidden">
                        <p className="truncate font-medium">
                          {displayThreadSummary(thread.summary, t)}
                        </p>
                        {thread.created_at && (
                          <p className="text-xs text-gray-400 dark:text-gray-500">
                            {formatDate(thread.created_at)}
                          </p>
                        )}
                      </div>
                      {thread.response_count !== undefined && thread.response_count > 0 && (
                        <span className="ml-2 text-xs text-gray-400">{thread.response_count}</span>
                      )}
                    </button>
                  )}
                  {!isEmptyProject && editingThreadId !== thread.id && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        startRename(thread)
                      }}
                      className="absolute right-8 top-1/2 hidden -translate-y-1/2 text-gray-400 hover:text-primary group-hover:block"
                      aria-label={t('threads.renameLabel', 'Rename thread')}
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5M18.5 2.5a2.121 2.121 0 113 3L12 15l-4 1 1-4 9.5-9.5z" />
                      </svg>
                    </button>
                  )}
                  {isEmptyProject && editingThreadId !== thread.id && <button
                    onClick={(e) => {
                      e.stopPropagation()
                      if (confirm(t('threads.deleteConfirm', 'Delete this thread?'))) {
                        clearTemporaryThread()
                        router.push('/home')
                      }
                    }}
                    className="absolute right-2 top-1/2 hidden -translate-y-1/2 text-gray-400 hover:text-error group-hover:block"
                    aria-label={t('threads.deleteLabel', 'Delete thread')}
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>}
                  {!isEmptyProject && editingThreadId !== thread.id && <button
                    onClick={(e) => {
                      e.stopPropagation()
                      if (confirm(t('threads.deleteConfirm', 'Delete this thread?'))) {
                        if (typeof thread.id === 'number') deleteThread.mutate(thread.id)
                      }
                    }}
                    className="absolute right-2 top-1/2 hidden -translate-y-1/2 text-gray-400 hover:text-error group-hover:block"
                    aria-label={t('threads.deleteLabel', 'Delete thread')}
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>}
                </div>
              )
            })}
          </div>
        )}
      </div>

    </aside>
  )
}
