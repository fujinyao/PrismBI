'use client'

import { useEffect, useRef, useCallback, useState } from 'react'
import { createWebSocket, type WebSocketClient, type WsReadyState } from '@/lib/ws'
import { useAuthStore } from '@/stores/authStore'
import { authApi } from '@/lib/api'

const _wsOverride = process.env.NEXT_PUBLIC_WS_URL || ''
const WS_TICKET_TIMEOUT_MS = 5000
const WS_TICKET_METRICS_DEBUG = String(process.env.NEXT_PUBLIC_WS_TICKET_METRICS_DEBUG ?? '0').trim().toLowerCase()

let wsTicketInFlight: Promise<string | null> | null = null
let wsTicketIssuedCount = 0
let wsTicketConsumedCount = 0
let wsTicketTokenFallbackCount = 0
let wsTicketRequestErrorCount = 0

function _wsTicketDebugEnabled(): boolean {
  return WS_TICKET_METRICS_DEBUG === '1' || WS_TICKET_METRICS_DEBUG === 'true' || WS_TICKET_METRICS_DEBUG === 'on'
}

function _logWsTicketMetrics(event: string, extra: Record<string, unknown> = {}) {
  if (!_wsTicketDebugEnabled()) {
    return
  }
  try {
    console.info('[ws-ticket]', {
      event,
      issued: wsTicketIssuedCount,
      consumed: wsTicketConsumedCount,
      tokenFallback: wsTicketTokenFallbackCount,
      errors: wsTicketRequestErrorCount,
      inFlight: Boolean(wsTicketInFlight),
      ...extra,
    })
  } catch {
    /* no-op */
  }
}

async function _requestWsTicketSingleFlight(): Promise<string | null> {
  if (wsTicketInFlight) {
    _logWsTicketMetrics('reuse_inflight')
    return wsTicketInFlight
  }
  wsTicketInFlight = (async () => {
    try {
      const ticketPromise = authApi.wsTicket()
      const data = await Promise.race([
        ticketPromise,
        new Promise<null>((resolve) => setTimeout(() => resolve(null), WS_TICKET_TIMEOUT_MS)),
      ])
      const ticket = data ? (data as { ticket: string }).ticket ?? null : null
      if (ticket) {
        wsTicketIssuedCount += 1
        _logWsTicketMetrics('issued')
      }
      return ticket
    } catch {
      wsTicketRequestErrorCount += 1
      _logWsTicketMetrics('request_error')
      return null
    }
  })().finally(() => {
    wsTicketInFlight = null
  })
  return wsTicketInFlight
}

function getWsOrigin(): string {
  if (_wsOverride) return _wsOverride
  if (typeof window === 'undefined') return 'ws://localhost:5173'
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}`
}

export function useWebSocket(path: string = '/ask') {
  const token = useAuthStore((s) => s.token)
  const wsRef = useRef<WebSocketClient | null>(null)
  const handlersRef = useRef<Set<(data: unknown) => void>>(new Set())
  const [readyState, setReadyState] = useState<WsReadyState>('closed')

  useEffect(() => {
    if (!token) return

    const url = `${getWsOrigin()}/ws${path}`
    const attemptAuthRef = { current: 'token' as 'ticket' | 'token' }

    const getTicket = async () => {
      const ticket = await _requestWsTicketSingleFlight()
      if (ticket) {
        attemptAuthRef.current = 'ticket'
        return ticket
      }
      attemptAuthRef.current = 'token'
      wsTicketTokenFallbackCount += 1
      _logWsTicketMetrics('token_fallback')
      return null
    }
    const client = createWebSocket(url, () => useAuthStore.getState().token ?? '', getTicket)
    wsRef.current = client

    const unsubscribe = client.onMessage((data) => {
      handlersRef.current.forEach((handler) => handler(data))
    })

    const unsubState = client.onStateChange?.((s) => {
      setReadyState(s)
      if (s === 'open' && attemptAuthRef.current === 'ticket') {
        wsTicketConsumedCount += 1
        _logWsTicketMetrics('ticket_consumed')
        attemptAuthRef.current = 'token'
      }
    })

    return () => {
      unsubState?.()
      unsubscribe()
      client.close()
      wsRef.current = null
    }
  }, [token, path])

  const send = useCallback((data: unknown) => {
    wsRef.current?.send(data)
  }, [])

  const onMessage = useCallback((handler: (data: unknown) => void) => {
    handlersRef.current.add(handler)
    return () => {
      handlersRef.current.delete(handler)
    }
  }, [])

  return { send, onMessage, readyState }
}
