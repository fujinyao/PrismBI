export type WsReadyState = 'connecting' | 'open' | 'closing' | 'closed'

export interface WebSocketClient {
  send: (data: unknown) => void
  onMessage: (handler: (data: unknown) => void) => () => void
  onStateChange?: (handler: (state: WsReadyState) => void) => () => void
  close: () => void
  readyState: WsReadyState
}

const MAX_PENDING = 100
const MAX_RECONNECT_ATTEMPTS = 10
const PONG_TIMEOUT_MS = 90000
const APP_HEARTBEAT_FLAG = String(process.env.NEXT_PUBLIC_WS_APP_HEARTBEAT_ENABLED ?? '0').trim().toLowerCase()
const APP_HEARTBEAT_ENABLED = APP_HEARTBEAT_FLAG === '1' || APP_HEARTBEAT_FLAG === 'true' || APP_HEARTBEAT_FLAG === 'on'
const HEARTBEAT_INTERVAL_RAW_MS = Number(process.env.NEXT_PUBLIC_WS_APP_HEARTBEAT_INTERVAL_MS ?? '30000')
const HEARTBEAT_INTERVAL_MS = Number.isFinite(HEARTBEAT_INTERVAL_RAW_MS) && HEARTBEAT_INTERVAL_RAW_MS > 0
  ? HEARTBEAT_INTERVAL_RAW_MS
  : 30000

const WS_TICKET_TIMEOUT_MS = 10000

export function createWebSocket(url: string, getToken: () => string, getTicket?: () => Promise<string | null>): WebSocketClient {
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let pongTimeoutTimer: ReturnType<typeof setTimeout> | null = null
  const messageHandlers = new Set<(data: unknown) => void>()
  const stateHandlers = new Set<(state: WsReadyState) => void>()
  const pendingQueue: (() => void)[] = []
  let state: WsReadyState = 'connecting'
  let reconnectAttempts = 0
  let destroyed = false

  function getState(): WsReadyState {
    if (!ws) return 'closed'
    switch (ws.readyState) {
      case WebSocket.CONNECTING: return 'connecting'
      case WebSocket.OPEN: return 'open'
      case WebSocket.CLOSING: return 'closing'
      case WebSocket.CLOSED: return 'closed'
      default: return 'closed'
    }
  }

  function updateState() {
    const prev = state
    state = getState()
    if (prev !== state) {
      stateHandlers.forEach((handler) => handler(state))
    }
  }

  function flushPending() {
    while (pendingQueue.length) {
      const send = pendingQueue.shift()
      if (send) send()
    }
  }

  function resetPongTimeout() {
    if (pongTimeoutTimer) clearTimeout(pongTimeoutTimer)
    pongTimeoutTimer = setTimeout(() => {
      ws?.close()
    }, PONG_TIMEOUT_MS)
  }

  function startHeartbeat() {
    stopHeartbeat()
    if (!APP_HEARTBEAT_ENABLED) {
      return
    }
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
        resetPongTimeout()
      }
    }, HEARTBEAT_INTERVAL_MS)
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
    if (pongTimeoutTimer) {
      clearTimeout(pongTimeoutTimer)
      pongTimeoutTimer = null
    }
  }

  async function connect() {
    if (destroyed) return
    state = 'connecting'
    const wsUrl: string = url
    let wsProtocols: string[] = []
    if (getTicket) {
      let ticket: string | null = null
      try {
        ticket = await Promise.race([
          getTicket(),
          new Promise<null>((_, reject) => setTimeout(() => reject(new Error('Ticket timeout')), WS_TICKET_TIMEOUT_MS)),
        ])
      } catch {
        ticket = null
      }
      if (destroyed) return
      if (ticket) {
        wsProtocols = [`prismbi-ticket.${encodeURIComponent(ticket)}`]
      } else {
        const token = getToken()
        if (token) {
          wsProtocols = [`prismbi-token.${encodeURIComponent(token)}`]
        }
      }
    } else {
      const token = getToken()
      if (token) {
        wsProtocols = [`prismbi-token.${encodeURIComponent(token)}`]
      }
    }
    ws = wsProtocols.length > 0 ? new WebSocket(wsUrl, wsProtocols) : new WebSocket(wsUrl)

    ws.onopen = () => {
      updateState()
      reconnectAttempts = 0
      flushPending()
      startHeartbeat()
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'pong') {
          if (pongTimeoutTimer) clearTimeout(pongTimeoutTimer)
          pongTimeoutTimer = null
          return
        }
        messageHandlers.forEach((handler) => handler(data))
      } catch {
        messageHandlers.forEach((handler) => handler(event.data))
      }
    }

    ws.onclose = (event) => {
      updateState()
      stopHeartbeat()
      if (event.code === 1008) {
        return
      }
      if (!destroyed && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000)
        reconnectAttempts++
        reconnectTimer = setTimeout(() => {
          connect()
        }, delay)
      }
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  connect()

  return {
    send(data: unknown) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data))
      } else if (!destroyed && pendingQueue.length < MAX_PENDING) {
        pendingQueue.push(() => {
          if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(data))
          }
        })
      }
    },
    onMessage(handler: (data: unknown) => void) {
      messageHandlers.add(handler)
      return () => {
        messageHandlers.delete(handler)
      }
    },
    onStateChange(handler: (state: WsReadyState) => void) {
      stateHandlers.add(handler)
      handler(state)
      return () => {
        stateHandlers.delete(handler)
      }
    },
    close() {
      destroyed = true
      pendingQueue.length = 0
      stopHeartbeat()
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      ws?.close()
      state = 'closed'
    },
    get readyState() {
      return state
    },
  }
}
