export interface SSEClient {
  onMessage: (handler: (data: unknown) => void) => () => void
  onEvent: (event: string, handler: (data: unknown) => void) => () => void
  close: () => void
  readyState: 'connecting' | 'open' | 'closed'
}

const MAX_RECONNECT_ATTEMPTS = 10

function getToken(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem('auth-store')
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed?.state?.token) return parsed.state.token
    }
  } catch {
    /* ignore */
  }
  return null
}

export function createSSE(url: string, getTokenFn?: () => string | null): SSEClient {
  let eventSource: EventSource | null = null
  const messageHandlers = new Set<(data: unknown) => void>()
  const eventHandlers = new Map<string, (data: unknown) => void>()
  const nativeListeners = new Map<string, Set<(ev: MessageEvent) => void>>()
  let state: 'connecting' | 'open' | 'closed' = 'connecting'
  let destroyed = false
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function bindEventListeners(es: EventSource) {
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        messageHandlers.forEach((handler) => handler(data))
      } catch {
        messageHandlers.forEach((handler) => handler(event.data))
      }
    }

    for (const [eventName, handler] of eventHandlers) {
      const listener = (ev: MessageEvent) => {
        try {
          handler(JSON.parse(ev.data))
        } catch {
          handler(ev.data)
        }
      }
      if (!nativeListeners.has(eventName)) nativeListeners.set(eventName, new Set())
      nativeListeners.get(eventName)!.add(listener)
      es.addEventListener(eventName, listener)
    }
  }

  function connect() {
    if (destroyed) return
    state = 'connecting'

    const token = getTokenFn ? getTokenFn() : getToken()
    let sseUrl = url
    if (token) {
      const separator = url.includes('?') ? '&' : '?'
      sseUrl = `${url}${separator}token=${encodeURIComponent(token)}`
    }

    eventSource = new EventSource(sseUrl)

    eventSource.onopen = () => {
      state = 'open'
      reconnectAttempts = 0
    }

    eventSource.onerror = () => {
      state = 'closed'
      eventSource?.close()
      eventSource = null
      if (!destroyed && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000)
        reconnectAttempts++
        reconnectTimer = setTimeout(() => connect(), delay)
      }
    }

    bindEventListeners(eventSource)
  }

  connect()

  return {
    onMessage(handler: (data: unknown) => void) {
      messageHandlers.add(handler)
      return () => {
        messageHandlers.delete(handler)
      }
    },
    onEvent(event: string, handler: (data: unknown) => void) {
      eventHandlers.set(event, handler)
      if (eventSource) {
        const oldListeners = nativeListeners.get(event)
        if (oldListeners) {
          for (const old of oldListeners) eventSource.removeEventListener(event, old)
          oldListeners.clear()
        }
        const listener = (ev: MessageEvent) => {
          try {
            handler(JSON.parse(ev.data))
          } catch {
            handler(ev.data)
          }
        }
        if (!nativeListeners.has(event)) nativeListeners.set(event, new Set())
        nativeListeners.get(event)!.add(listener)
        eventSource.addEventListener(event, listener)
      }
      return () => {
        eventHandlers.delete(event)
        const listeners = nativeListeners.get(event)
        if (listeners) {
          if (eventSource) for (const l of listeners) eventSource.removeEventListener(event, l)
          nativeListeners.delete(event)
        }
      }
    },
    close() {
      destroyed = true
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      eventSource?.close()
      eventSource = null
      state = 'closed'
    },
    get readyState() {
      return state
    },
  }
}