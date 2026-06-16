'use client'

import { useEffect, useMemo, useRef, useCallback, useState } from 'react'
import { useParams, useSearchParams } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { askApi, knowledgeApi, modelingApi, threadsApi, type ThreadDetail, type ThreadResponse } from '@/lib/api'
import { getRequestTimeout } from '@/lib/api'
import { appendTemporaryResponse, getTemporaryThread, saveTemporaryThread } from '@/lib/tempThreads'
import { ThreadList } from '@/components/home/ThreadList'
import { ResponseCard } from '@/components/home/ResponseCard'
import { PromptBar } from '@/components/home/PromptBar'
import { CompactPromptBar } from '@/components/mobile/CompactPromptBar'
import { Skeleton, SkeletonCard } from '@/components/ui/Skeleton'
import { useProjectStore } from '@/stores/projectStore'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { displayThreadSummary } from '@/lib/utils'
import { queryMetricsQueryKey } from '@/lib/queryMetrics'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAuthStore } from '@/stores/authStore'

interface AskResult {
  sql?: string
  summary?: string | null
  thread_id?: number
  response?: import('@/lib/api').ThreadResponse
}

interface AskTransportError extends Error {
  code?: string
}

const REQUEST_STILL_PROCESSING_CODE = 'REQUEST_STILL_PROCESSING'
const ASK_HTTP_FALLBACK_FLAG = String(process.env.NEXT_PUBLIC_ASK_HTTP_FALLBACK ?? '0').trim().toLowerCase()
const ENABLE_HTTP_SHORT_FALLBACK = ASK_HTTP_FALLBACK_FLAG === '1' || ASK_HTTP_FALLBACK_FLAG === 'true' || ASK_HTTP_FALLBACK_FLAG === 'on'
const SSE_IDLE_TIMEOUT_MS_RAW = Number(process.env.NEXT_PUBLIC_ASK_SSE_IDLE_TIMEOUT_MS || '0')
const SSE_IDLE_TIMEOUT_MS = Number.isFinite(SSE_IDLE_TIMEOUT_MS_RAW) && SSE_IDLE_TIMEOUT_MS_RAW > 0 ? SSE_IDLE_TIMEOUT_MS_RAW : 0
const HOME_SHORT_CACHE_MS = 5000
const PREVIOUS_CONTEXT_LIMIT = 5
const PREVIOUS_ANSWER_MAX_CHARS = 400

function compactPreviousAnswer(value: string): string {
  const normalized = String(value || '').trim()
  if (normalized.length <= PREVIOUS_ANSWER_MAX_CHARS) {
    return normalized
  }
  return normalized.slice(0, PREVIOUS_ANSWER_MAX_CHARS)
}

interface ResponseSaveOverrides {
  sql?: string
  answerContent?: string | null
  columns?: string[]
}

export default function ThreadPage() {
  const params = useParams<{ threadId: string }>()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const t = useI18nStore((s) => s.t)
  const isMobile = useIsMobile()
  const locale = useI18nStore((s) => s.locale)
  const authToken = useAuthStore((s) => s.token)
  const currentProject = useProjectStore((s) => s.currentProject)
  const rawThreadId = params.threadId
  const isTemporaryThread = rawThreadId.startsWith('temp-')
  const numericThreadId = Number(rawThreadId) || 0
  const threadId = isTemporaryThread ? Number(rawThreadId.replace('temp-', '')) || 0 : numericThreadId
  const bottomRef = useRef<HTMLDivElement>(null)
  const [temporaryVersion, setTemporaryVersion] = useState(0)
  const { send: sendWs, onMessage: onWsMessage, readyState: wsReadyState } = useWebSocket('/ask')
  const pendingWsRequests = useRef(new Map<string, {
    resolve: (value: AskResult) => void
    reject: (error: Error) => void
    timer: ReturnType<typeof setTimeout>
    streamText?: string
    started?: boolean
    dispatched?: boolean
  }>())
  const threadRefreshTimersRef = useRef<Array<ReturnType<typeof setTimeout>>>([])
  const [wsStreamText, setWsStreamText] = useState<string>('')
  const [wsStepProgress, setWsStepProgress] = useState<{ key: string; detail?: string }[]>([])

  const {
    data: thread,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ['thread', rawThreadId],
    queryFn: () => threadsApi.get(numericThreadId) as Promise<ThreadDetail>,
    enabled: !isTemporaryThread,
    staleTime: HOME_SHORT_CACHE_MS,
    gcTime: HOME_SHORT_CACHE_MS * 20,
    refetchOnWindowFocus: false,
  })

  const temporaryThread = isTemporaryThread ? getTemporaryThread(rawThreadId) : null
  const visibleThread = isTemporaryThread
    ? {
        id: threadId,
        project_id: 0,
        summary: displayThreadSummary(temporaryThread?.summary, t),
        preview_row_limit: temporaryThread?.preview_row_limit ?? 20,
        responses: temporaryThread?.responses ?? [],
      } as ThreadDetail
    : thread
  const activeProjectId = useMemo(() => {
    const threadProjectId = visibleThread?.project_id
    if (typeof threadProjectId === 'number' && threadProjectId > 0) {
      return threadProjectId
    }
    const currentProjectId = currentProject?.id
    if (typeof currentProjectId === 'number' && currentProjectId > 0) {
      return currentProjectId
    }
    return undefined
  }, [currentProject?.id, visibleThread?.project_id])
  const responses = useMemo(() => visibleThread?.responses ?? [], [visibleThread?.responses])
  const previousQuestionsContext = useMemo(
    () => responses.map((response) => response.question).slice(-PREVIOUS_CONTEXT_LIMIT),
    [responses],
  )
  const previousAnswersContext = useMemo(
    () => responses
      .map((response) => compactPreviousAnswer(response.answerDetail?.content || response.answerDetail?.error || ''))
      .slice(-PREVIOUS_CONTEXT_LIMIT),
    [responses],
  )
  const initialQuestion = searchParams.get('question')

  const createClientRequestId = useCallback(() => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
    return `ask-${Date.now()}-${Math.random().toString(36).slice(2)}`
  }, [])

  const createRequestStillProcessingError = useCallback((): AskTransportError => {
    const err = new Error(t('thread.wsAlreadyProcessing', 'Request is still processing on server. Please wait for the thread to refresh.')) as AskTransportError
    err.code = REQUEST_STILL_PROCESSING_CODE
    return err
  }, [t])

  const askTransportDebugEnabled = useMemo(() => {
    if (process.env.NEXT_PUBLIC_ASK_TRANSPORT_DEBUG === '1') {
      return true
    }
    if (typeof window === 'undefined') {
      return false
    }
    try {
      if (window.localStorage.getItem('prismbi:askTransportDebug') === '1') {
        return true
      }
      const flag = new URLSearchParams(window.location.search).get('askDebug')
      return flag === '1' || flag === 'true'
    } catch {
      return false
    }
  }, [])

  const logAskTransport = useCallback((event: string, fields: Record<string, unknown> = {}) => {
    if (!askTransportDebugEnabled) {
      return
    }
    try {
      console.info('[ask-transport]', {
        event,
        at: new Date().toISOString(),
        threadId: rawThreadId,
        wsReadyState,
        ...fields,
      })
    } catch {
      /* no-op */
    }
  }, [askTransportDebugEnabled, rawThreadId, wsReadyState])

  const clearScheduledThreadRefresh = useCallback(() => {
    threadRefreshTimersRef.current.forEach((timer) => clearTimeout(timer))
    threadRefreshTimersRef.current = []
  }, [])

  const invalidateThreadListCache = useCallback(() => {
    if (typeof activeProjectId === 'number' && activeProjectId > 0) {
      queryClient.invalidateQueries({ queryKey: ['threads', activeProjectId] })
      return
    }
    queryClient.invalidateQueries({ queryKey: ['threads'] })
  }, [activeProjectId, queryClient])

  const patchThreadListCacheAfterResponse = useCallback((newResponse?: ThreadResponse) => {
    if (typeof activeProjectId !== 'number' || activeProjectId <= 0) {
      invalidateThreadListCache()
      return
    }

    let patched = false
    queryClient.setQueryData<{ items?: Array<Record<string, unknown>> }>(['threads', activeProjectId], (old) => {
      if (!old || !Array.isArray(old.items)) {
        return old
      }
      const updatedAt = (newResponse as { created_at?: string } | undefined)?.created_at ?? new Date().toISOString()
      const nextItems = old.items.map((item) => {
        const itemId = Number((item as { id?: unknown }).id ?? 0)
        if (itemId !== numericThreadId) {
          return item
        }
        patched = true
        const countRaw = Number((item as { response_count?: unknown }).response_count ?? 0)
        const responseCount = Number.isFinite(countRaw) ? Math.max(0, countRaw) + 1 : 1
        return {
          ...item,
          response_count: responseCount,
          updated_at: updatedAt,
        }
      })
      if (!patched) {
        return old
      }
      return {
        ...old,
        items: nextItems,
      }
    })

    if (!patched) {
      queryClient.invalidateQueries({ queryKey: ['threads', activeProjectId] })
    }
  }, [activeProjectId, invalidateThreadListCache, numericThreadId, queryClient])

  const scheduleThreadRefreshWithBackoff = useCallback((reason: string) => {
    clearScheduledThreadRefresh()
    const delays = [0, 1200, 3000, 6000]
    delays.forEach((delayMs, index) => {
      const timer = setTimeout(() => {
        logAskTransport('thread_refresh_attempt', {
          reason,
          attempt: index + 1,
          delayMs,
        })
        queryClient.invalidateQueries({ queryKey: ['thread', rawThreadId] })
        if (index === 0) {
          invalidateThreadListCache()
        }
      }, delayMs)
      threadRefreshTimersRef.current.push(timer)
    })
  }, [clearScheduledThreadRefresh, invalidateThreadListCache, logAskTransport, queryClient, rawThreadId])

  useEffect(() => {
    return () => {
      clearScheduledThreadRefresh()
    }
  }, [clearScheduledThreadRefresh])

  useEffect(() => {
    return onWsMessage((message) => {
      if (!message || typeof message !== 'object') return
      const payload = message as { type?: string; request_id?: string; data?: AskResult; message?: string; content_type?: string; content?: string }
      let requestId = typeof payload.request_id === 'string' && payload.request_id ? payload.request_id : ''
      if (!requestId && (payload.type === 'result' || payload.type === 'error') && pendingWsRequests.current.size === 1) {
        requestId = Array.from(pendingWsRequests.current.keys())[0] ?? ''
      }
      if (!requestId) return
      const pending = pendingWsRequests.current.get(requestId)
      if (!pending) return
      if (payload.type === 'delta' && payload.content_type === 'state' && payload.content === 'running') {
        pending.started = true
        return
      }
      if (payload.type === 'delta' && payload.content_type === 'text' && payload.content) {
        pending.started = true
        pending.streamText = (pending.streamText || '') + payload.content
        setWsStreamText(pending.streamText)
      }
      if (payload.type === 'delta' && payload.content_type === 'sql' && payload.content) {
        pending.started = true
        const res = pending.streamText ? { sql: payload.content } : {}
        Object.assign(pending, res)
      }
      if (payload.type === 'delta' && payload.content_type === 'step' && payload.content) {
        pending.started = true
        try {
          const stepInfo = JSON.parse(payload.content)
          setWsStepProgress((prev) => {
            const existing = prev.findIndex((s) => s.key === stepInfo.key)
            if (existing >= 0) {
              const updated = [...prev]
              updated[existing] = { key: stepInfo.key, detail: stepInfo.detail }
              return updated
            }
            return [...prev, { key: stepInfo.key, detail: stepInfo.detail }]
          })
        } catch { /* ignore malformed step delta */ }
      }
      if (payload.type === 'result') {
        clearTimeout(pending.timer)
        pendingWsRequests.current.delete(requestId)
        setWsStreamText('')
        setWsStepProgress([])
        logAskTransport('ws_result', { requestId, started: !!pending.started })
        const resultData = (payload.data ?? {}) as Record<string, unknown>
        if (resultData.compact_result === true && resultData.response == null) {
          resultData.response = {
            id: resultData.response_id as number,
            created_at: resultData.response_created_at as string | undefined,
          } as ThreadResponse
        }
        pending.resolve(resultData as AskResult)
      }
      if (payload.type === 'error') {
        clearTimeout(pending.timer)
        pendingWsRequests.current.delete(requestId)
        setWsStreamText('')
        setWsStepProgress([])
        logAskTransport('ws_error', {
          requestId,
          started: !!pending.started,
          message: payload.message || 'WebSocket ask failed',
        })
        if (pending.started) {
          pending.reject(createRequestStillProcessingError())
        } else {
          pending.reject(new Error(payload.message || 'WebSocket ask failed'))
        }
      }
    })
  }, [createRequestStillProcessingError, logAskTransport, onWsMessage])

  useEffect(() => {
    if (wsReadyState !== 'closing' && wsReadyState !== 'closed') {
      return
    }
    if (pendingWsRequests.current.size === 0) {
      return
    }

    pendingWsRequests.current.forEach((pending) => {
      clearTimeout(pending.timer)
      const stillProcessing = !!pending.started || !!pending.dispatched
      logAskTransport('ws_disconnected_with_pending', {
        started: !!pending.started,
        dispatched: !!pending.dispatched,
        stillProcessing,
      })
      pending.reject(stillProcessing ? createRequestStillProcessingError() : new Error(t('thread.wsDisconnected', 'WebSocket disconnected before result')))
    })
    pendingWsRequests.current.clear()
    setWsStepProgress([])
    setWsStreamText('')
  }, [createRequestStillProcessingError, logAskTransport, wsReadyState, t])

  useEffect(() => {
    const pendingRequests = pendingWsRequests.current
    return () => {
      pendingRequests.forEach((pending) => {
        clearTimeout(pending.timer)
        const stillProcessing = !!pending.started || !!pending.dispatched
        logAskTransport('ws_cancelled_with_pending', { started: !!pending.started, dispatched: !!pending.dispatched, stillProcessing })
        pending.reject(stillProcessing ? createRequestStillProcessingError() : new Error('WebSocket ask cancelled'))
      })
      pendingRequests.clear()
    }
  }, [createRequestStillProcessingError, logAskTransport])

  const askViaWebSocket = useCallback((question: string, previewRowLimit: number, clientRequestId: string) => new Promise<AskResult>((resolve, reject) => {
    const requestId = clientRequestId
    const dispatched = wsReadyState === 'open'
    logAskTransport('ws_send', { requestId, clientRequestId, previewRowLimit, wsReadyState, dispatched })
    const timer = setTimeout(() => {
      const pending = pendingWsRequests.current.get(requestId)
      if (!pending) return
      pendingWsRequests.current.delete(requestId)
      setWsStreamText('')
      setWsStepProgress([])
      const stillProcessing = !!pending.started || !!pending.dispatched
      logAskTransport('ws_timeout', { requestId, started: !!pending.started, dispatched: !!pending.dispatched, stillProcessing })
      pending.reject(stillProcessing ? createRequestStillProcessingError() : new Error(t('thread.wsTimeout', 'Ask WebSocket timed out')))
    }, Math.max(getRequestTimeout(), 600000))
    setWsStepProgress([])
    setWsStreamText('')
    pendingWsRequests.current.set(requestId, {
      resolve: (value: AskResult) => resolve(value),
      reject: (reason: unknown) => { try { reject(reason) } catch { /* already settled */ } },
      timer,
      started: false,
      dispatched,
    })
    sendWs({
      type: 'ask',
      request_id: requestId,
      client_request_id: clientRequestId,
      question,
      thread_id: isTemporaryThread ? threadId : numericThreadId,
      previous_questions: previousQuestionsContext,
      previous_answers: previousAnswersContext,
      language: locale,
      preview_row_limit: previewRowLimit,
      temporary: isTemporaryThread,
    })
  }), [createRequestStillProcessingError, isTemporaryThread, locale, logAskTransport, numericThreadId, previousAnswersContext, previousQuestionsContext, sendWs, t, threadId, wsReadyState])

  const askViaSSE = useCallback(async (question: string, previewRowLimit: number, clientRequestId: string): Promise<AskResult> => {
    setWsStepProgress([])
    setWsStreamText('')
    logAskTransport('sse_start', { clientRequestId, previewRowLimit })
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
    const timeoutMs = Math.max(getRequestTimeout(), 600000)
    const timeoutToken = '__PRISMBI_SSE_TIMEOUT__'
    const idleToken = '__PRISMBI_SSE_IDLE_TIMEOUT__'
    let serverStarted = false
    let timeout: ReturnType<typeof setTimeout> | null = null

    const body = {
      question,
      thread_id: isTemporaryThread ? threadId : numericThreadId,
      previous_questions: previousQuestionsContext,
      previous_answers: previousAnswersContext,
      language: locale,
      preview_row_limit: previewRowLimit,
      temporary: isTemporaryThread,
      client_request_id: clientRequestId,
    }

    let response: Response
    try {
      const fetchPromise = fetch('/api/ask/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        },
        body: JSON.stringify(body),
        signal: controller?.signal,
      })
      const timeoutPromise = new Promise<Response>((_, reject) => {
        timeout = setTimeout(() => {
          controller?.abort()
          reject(new Error(timeoutToken))
        }, timeoutMs)
      })
      response = await Promise.race([fetchPromise, timeoutPromise])
    } catch (err) {
      logAskTransport('sse_fetch_error', { clientRequestId, message: err instanceof Error ? err.message : String(err) })
      if (err instanceof Error && err.message === timeoutToken) {
        throw new Error(t('thread.sseTimeout', 'SSE ask timed out'))
      }
      throw err
    } finally {
      if (timeout) clearTimeout(timeout)
    }

    if (!response.ok || !response.body) {
      throw new Error(t('thread.sseFallbackFailed', 'SSE fallback request failed'))
    }

    const reader = response.body.getReader()
    const decoder = typeof TextDecoder !== 'undefined' ? new TextDecoder() : null
    const decodeChunk = (chunk: Uint8Array): string => {
      if (decoder) {
        return decoder.decode(chunk, { stream: true })
      }

      let percentEncoded = ''
      for (let i = 0; i < chunk.length; i++) {
        const b = chunk[i] ?? 0
        const hex = b.toString(16)
        percentEncoded += `%${hex.length === 1 ? `0${hex}` : hex}`
      }

      try {
        return decodeURIComponent(percentEncoded)
      } catch {
        let fallback = ''
        for (let i = 0; i < chunk.length; i++) {
          fallback += String.fromCharCode(chunk[i] ?? 0)
        }
        return fallback
      }
    }
    let buffer = ''
    let finalResult: AskResult | null = null

    const markServerStarted = () => {
      if (serverStarted) return
      serverStarted = true
      logAskTransport('sse_running', { clientRequestId })
    }

    const processDataPayload = (raw: string) => {
      let parsed: Record<string, unknown>
      try {
        parsed = JSON.parse(raw) as Record<string, unknown>
      } catch {
        return
      }
      const type = String(parsed.type || '')
      const contentType = String(parsed.content_type || '')
      const content = parsed.content

      if (type === 'delta' && contentType === 'text' && typeof content === 'string') {
        markServerStarted()
        setWsStreamText((prev) => prev + content)
        return
      }
      if (type === 'delta' && contentType === 'step' && typeof content === 'string') {
        markServerStarted()
        try {
          const stepInfo = JSON.parse(content) as { key?: string; detail?: string }
          if (!stepInfo?.key) return
          setWsStepProgress((prev) => {
            const existing = prev.findIndex((s) => s.key === stepInfo.key)
            if (existing >= 0) {
              const updated = [...prev]
              updated[existing] = { key: stepInfo.key || '', detail: stepInfo.detail }
              return updated
            }
            return [...prev, { key: stepInfo.key || '', detail: stepInfo.detail }]
          })
        } catch {
          /* ignore malformed step */
        }
        return
      }
      if (type === 'delta' && contentType === 'state' && content === 'running') {
        markServerStarted()
        return
      }
      if (type === 'error') {
        const message = typeof parsed.message === 'string' ? parsed.message : t('thread.sseFallbackFailed', 'SSE fallback request failed')
        logAskTransport('sse_error', { clientRequestId, message })
        throw new Error(message)
      }
      if (type === 'result' && parsed.data && typeof parsed.data === 'object') {
        markServerStarted()
        const resultData = parsed.data as Record<string, unknown>
        if (resultData.compact_result === true && resultData.response == null) {
          resultData.response = {
            id: resultData.response_id as number,
            created_at: resultData.response_created_at as string | undefined,
          } as ThreadResponse
        }
        finalResult = resultData as AskResult
      }
    }

    try {
      while (true) {
        let readResult: ReadableStreamReadResult<Uint8Array>
        if (SSE_IDLE_TIMEOUT_MS > 0) {
          let idleTimer: ReturnType<typeof setTimeout> | null = null
          try {
            readResult = await Promise.race([
              reader.read(),
              new Promise<ReadableStreamReadResult<Uint8Array>>((_, reject) => {
                idleTimer = setTimeout(() => {
                  controller?.abort()
                  reject(new Error(idleToken))
                }, SSE_IDLE_TIMEOUT_MS)
              }),
            ])
          } finally {
            if (idleTimer) clearTimeout(idleTimer)
          }
        } else {
          readResult = await reader.read()
        }
        const { value, done } = readResult
        if (done) break
        if (!value) continue
        buffer += decodeChunk(value).replace(/\r\n/g, '\n')

        let boundary = buffer.indexOf('\n\n')
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const dataLines = frame
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trimStart())
          if (dataLines.length > 0) {
            processDataPayload(dataLines.join('\n'))
          }
          boundary = buffer.indexOf('\n\n')
        }
      }
    } catch (err) {
      if (err instanceof Error && err.message === idleToken) {
        logAskTransport('sse_idle_timeout', { clientRequestId, serverStarted, timeoutMs: SSE_IDLE_TIMEOUT_MS })
        throw createRequestStillProcessingError()
      }
      logAskTransport('sse_stream_error', { clientRequestId, message: err instanceof Error ? err.message : String(err) })
      throw err
    } finally {
      try {
        reader.releaseLock()
      } catch {
        /* ignore */
      }
      setWsStepProgress([])
      setWsStreamText('')
    }

    if (!finalResult) {
      if (serverStarted) {
        logAskTransport('sse_end_without_result_after_started', { clientRequestId })
        throw createRequestStillProcessingError()
      }
      logAskTransport('sse_end_without_result', { clientRequestId })
      throw new Error(t('thread.sseNoResult', 'SSE stream ended without a result'))
    }
    const sseResult = finalResult as AskResult
    logAskTransport('sse_success', {
      clientRequestId,
      hasSql: !!sseResult.sql,
      hasSummary: !!sseResult.summary,
      hasResponse: !!sseResult.response,
    })
    return sseResult
  }, [authToken, createRequestStillProcessingError, isTemporaryThread, locale, logAskTransport, numericThreadId, previousAnswersContext, previousQuestionsContext, t, threadId])

  const shouldFallbackAfterTransportError = useCallback((err: unknown): boolean => {
    if (!(err instanceof Error)) return true
    return (err as AskTransportError).code !== REQUEST_STILL_PROCESSING_CODE
  }, [])

  const createResponse = useMutation({
    mutationFn: async ({ question, previewRowLimit }: { question: string; previewRowLimit: number }) => {
      clearScheduledThreadRefresh()
      const clientRequestId = createClientRequestId()
      logAskTransport('ask_start', {
        clientRequestId,
        previewRowLimit,
        wsOpen: wsReadyState === 'open',
        longConnectionOnly: !ENABLE_HTTP_SHORT_FALLBACK,
      })
      if (wsReadyState === 'open') {
        try {
          const wsResult = await askViaWebSocket(question, previewRowLimit, clientRequestId)
          logAskTransport('ws_success', { clientRequestId })
          return wsResult
        } catch (err) {
          const shouldFallback = shouldFallbackAfterTransportError(err)
          logAskTransport('ws_failed', {
            clientRequestId,
            shouldFallback,
            code: err instanceof Error ? (err as AskTransportError).code : undefined,
            message: err instanceof Error ? err.message : String(err),
          })
          if (!shouldFallback) {
            throw err
          }
        }
      } else {
        logAskTransport('ws_skipped_not_open', { clientRequestId, wsReadyState })
      }

      try {
        const sseResult = await askViaSSE(question, previewRowLimit, clientRequestId)
        logAskTransport('sse_success_returned', { clientRequestId })
        return sseResult
      } catch (err) {
        const shouldFallback = shouldFallbackAfterTransportError(err)
        logAskTransport('sse_failed', {
          clientRequestId,
          shouldFallback,
          code: err instanceof Error ? (err as AskTransportError).code : undefined,
          message: err instanceof Error ? err.message : String(err),
        })
        if (!shouldFallback) {
          throw err
        }
        if (!ENABLE_HTTP_SHORT_FALLBACK) {
          logAskTransport('http_fallback_disabled', {
            clientRequestId,
            reason: 'long_connection_mode',
          })
          throw err
        }
        logAskTransport('http_fallback_start', { clientRequestId })
        const httpResult = await askApi.ask(
          question,
          isTemporaryThread ? threadId : numericThreadId,
          previousQuestionsContext,
          previousAnswersContext,
          locale,
          previewRowLimit,
          isTemporaryThread,
          clientRequestId,
        )
        logAskTransport('http_fallback_success', { clientRequestId })
        return httpResult
      }
    },
    onSuccess: (data, variables) => {
      clearScheduledThreadRefresh()
      logAskTransport('ask_success', {
        question: variables.question,
        responseId: data.response?.id,
        threadId: data.thread_id,
      })
      if (isTemporaryThread && data.response) {
        appendTemporaryResponse(data.response, variables.previewRowLimit)
        setTemporaryVersion((value) => value + 1)
        return
      }
      if (data.response) {
        queryClient.setQueryData<ThreadDetail>(['thread', rawThreadId], (old) => {
          if (!old) return old
          return { ...old, responses: [...old.responses, data.response as ThreadResponse] }
        })
      }
      if (!isTemporaryThread) {
        queryClient.invalidateQueries({ queryKey: ['thread', rawThreadId] })
      }
      patchThreadListCacheAfterResponse(data.response as ThreadResponse | undefined)
      const metricsProjectId = visibleThread?.project_id ?? currentProject?.id
      if (typeof metricsProjectId === 'number' && metricsProjectId > 0) {
        queryClient.invalidateQueries({ queryKey: queryMetricsQueryKey(metricsProjectId) })
      }
    },
    onError: (err) => {
      logAskTransport('ask_error', {
        code: err instanceof Error ? (err as AskTransportError).code : undefined,
        message: err instanceof Error ? err.message : String(err),
      })
      if (err instanceof Error && (err as AskTransportError).code === REQUEST_STILL_PROCESSING_CODE) {
        scheduleThreadRefreshWithBackoff('still_processing')
        toast(t('thread.wsAlreadyProcessing', 'Request is still processing on server. Please wait for the thread to refresh.'), 'warning')
        return
      }
      toast(err instanceof Error ? err.message : t('thread.sendError', 'Failed to send message'), 'error')
    },
  })

  const pendingResponse = useMemo(() => {
    if (!createResponse.isPending || !createResponse.variables) return null
    return {
      id: -1,
      threadId,
      question: createResponse.variables.question,
      sql: null,
      askingTask: {
        type: 'ASK',
        status: 'RUNNING',
        traceId: `pending-${threadId}`,
        retrievedTables: [],
        intentReasoning: t('thinking.pendingIntent', 'Understanding your question and preparing to search the project semantic model.'),
      },
      answerDetail: {
        status: 'STREAMING',
        content: wsStreamText || null,
        error: null,
        numRowsUsedInLLM: 0,
        queryId: null,
      },
      breakdownDetail: {
        status: 'RUNNING',
        description: t('thinking.pendingDescription', 'PrismBI is preparing the answer.'),
        steps: ['understand_question', 'retrieve_metadata', 'route_or_answer'],
      },
    }
  }, [createResponse.isPending, createResponse.variables, threadId, t, wsStreamText])

  const autoSubmittedRef = useRef(false)
  const { isPending: mutationPending, mutate: submitQuestion } = createResponse
  useEffect(() => {
    if (
      initialQuestion &&
      !autoSubmittedRef.current &&
      responses.length === 0 &&
      !mutationPending
    ) {
      autoSubmittedRef.current = true
      window.history.replaceState(null, '', `/home/${rawThreadId}`)
      submitQuestion({ question: initialQuestion, previewRowLimit: visibleThread?.preview_row_limit ?? 20 })
      if (isTemporaryThread && temporaryThread && !temporaryThread.summary) {
        saveTemporaryThread({ ...temporaryThread, summary: initialQuestion.slice(0, 24), preview_row_limit: visibleThread?.preview_row_limit ?? 20 })
        setTemporaryVersion((value) => value + 1)
      }
    }
  }, [initialQuestion, responses.length, mutationPending, submitQuestion, rawThreadId, visibleThread?.preview_row_limit, isTemporaryThread, temporaryThread])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [responses.length, createResponse.isPending])

  const handleReRun = useCallback(
    (responseId: number) => {
      const response = responses.find((item) => item.id === responseId)
      if (!response?.question) return
      createResponse.mutate({ question: response.question, previewRowLimit: visibleThread?.preview_row_limit ?? 20 })
    },
    [createResponse, responses, visibleThread?.preview_row_limit],
  )

  const handleSaveAsView = useCallback(
    (responseId: number, overrides?: ResponseSaveOverrides) => {
      const response = responses.find((item) => item.id === responseId)
      if (!response) return
      const projectId = currentProject?.id ?? visibleThread?.project_id
      const sql = (overrides?.sql ?? response?.sql ?? '').trim()
      if (!projectId || !sql) {
        toast(t('thread.saveAsViewUnavailable', 'A project and SQL result are required to save a view.'), 'warning')
        return
      }
      const name = `view_${response.id}_${Date.now()}`.replace(/[^A-Za-z0-9_]/g, '_').slice(0, 120)
      const columns = (overrides?.columns ?? response.answerDetail?.columns ?? []).map((column) => ({ name: column, type: 'UNKNOWN' }))
      modelingApi.views.create(projectId, {
        name,
        display_name: response.question.slice(0, 80) || name,
        description: overrides?.answerContent ?? response.answerDetail?.content ?? undefined,
        sql,
        source_response_id: response.id,
        columns,
      }).then(() => {
        queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
        toast(t('thread.saveAsViewDone', 'Saved as View'), 'success')
      }).catch((err) => {
        toast(err instanceof Error ? err.message : t('thread.saveAsViewFailed', 'Failed to save view'), 'error')
      })
    },
    [currentProject?.id, queryClient, responses, visibleThread?.project_id, toast, t],
  )

  const handleSaveAsPair = useCallback(
    (responseId: number, overrides?: ResponseSaveOverrides) => {
      const response = responses.find((item) => item.id === responseId)
      if (!response) return
      const projectId = currentProject?.id ?? visibleThread?.project_id
      const sql = (overrides?.sql ?? response?.sql ?? '').trim()
      if (!projectId || !response?.question || !sql) {
        toast(t('thread.saveAsPairUnavailable', 'A project, question, and SQL are required to save a pair.'), 'warning')
        return
      }
      knowledgeApi.sqlPairs.create({
        project_id: projectId,
        question: response.question,
        sql,
        description: overrides?.answerContent ?? response.answerDetail?.content ?? undefined,
        category: 'saved_answer',
        scope: 'project',
      }).then(() => {
        queryClient.invalidateQueries({ queryKey: ['sql-pairs', projectId] })
        toast(t('thread.saveAsPairDone', 'Saved as Question-SQL Pair'), 'success')
      }).catch((err) => {
        toast(err instanceof Error ? err.message : t('thread.saveAsPairFailed', 'Failed to save pair'), 'error')
      })
    },
    [currentProject?.id, queryClient, responses, visibleThread?.project_id, toast, t],
  )

  return (
    <div className="flex h-full gap-[5px]">
      {!isMobile && <ThreadList projectId={currentProject?.id ?? visibleThread?.project_id ?? 0} />}

      <div className="flex min-w-0 flex-1 flex-col rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[min(100%,84rem)] space-y-6 px-3 py-3 md:px-5 md:py-5">
            {!isTemporaryThread && isLoading ? (
              <>
                <Skeleton className="h-6 w-1/3" />
                <SkeletonCard />
                <SkeletonCard />
              </>
            ) : !isTemporaryThread && isError ? (
              <div className="rounded-md bg-error-50 p-4 text-sm text-error-700 dark:bg-error-900/30 dark:text-error-400">
                {t('thread.loadError', 'Failed to load thread. It may have been deleted.')}
              </div>
            ) : (
              <>
                <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  {displayThreadSummary(visibleThread?.summary, t)}
                </h1>

                {responses.length === 0 && !pendingResponse ? (
                  <div className="py-12 text-center text-sm text-gray-500">
                    {t('thread.startConversation', 'Start a conversation by asking a question below.')}
                  </div>
                ) : (
                  <div className="space-y-6">
                    {responses.map((response) => (
                      <ResponseCard
                        key={response.id}
                        response={response}
                        onReRun={handleReRun}
                        onSaveAsView={isTemporaryThread ? undefined : handleSaveAsView}
                        onSaveAsPair={isTemporaryThread ? undefined : handleSaveAsPair}
                      />
                    ))}
                    {pendingResponse && <ResponseCard response={pendingResponse} isPending liveSteps={wsStepProgress} />}
                  </div>
                )}

                <div ref={bottomRef} />
              </>
            )}
          </div>
        </div>

        {isMobile ? (
          <CompactPromptBar
            onSubmit={(question) => createResponse.mutate({ question, previewRowLimit: visibleThread?.preview_row_limit ?? 20 })}
            disabled={createResponse.isPending || (!isTemporaryThread && (isLoading || isError))}
            className="rounded-b-xl"
          />
        ) : (
          <PromptBar
            onSubmit={(question, previewRowLimit) => createResponse.mutate({ question, previewRowLimit })}
            previewRowLimit={visibleThread?.preview_row_limit ?? 20}
            disabled={createResponse.isPending || (!isTemporaryThread && (isLoading || isError))}
            className="rounded-b-xl"
          />
        )}
      </div>
    </div>
  )
}
