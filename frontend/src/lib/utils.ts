import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const DEFAULT_THREAD_SUMMARY = 'New Conversation'

export function displayThreadSummary(summary: string | undefined | null, t: (key: string, fallback?: string) => string): string {
  if (!summary) return t('threads.newTitle', 'New conversation')
  if (summary === DEFAULT_THREAD_SUMMARY) return t('threads.newTitle', 'New conversation')
  return summary
}

export function formatDate(date: Date | string | number | null | undefined, options?: Intl.DateTimeFormatOptions): string {
  if (date == null) return '-'
  const d = typeof date === 'string' || typeof date === 'number' ? new Date(date) : date
  if (isNaN(d.getTime())) return '-'
  if (typeof Intl === 'undefined' || typeof Intl.DateTimeFormat !== 'function') {
    const mm = d.getMonth() + 1
    const dd = d.getDate()
    const hh = d.getHours()
    const mi = d.getMinutes()
    const mmText = mm < 10 ? `0${mm}` : String(mm)
    const ddText = dd < 10 ? `0${dd}` : String(dd)
    const hhText = hh < 10 ? `0${hh}` : String(hh)
    const miText = mi < 10 ? `0${mi}` : String(mi)
    return `${d.getFullYear()}-${mmText}-${ddText} ${hhText}:${miText}`
  }
  return d.toLocaleDateString(undefined, options ?? {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatNumber(n: number, options?: Intl.NumberFormatOptions): string {
  if (typeof Intl === 'undefined' || typeof Intl.NumberFormat !== 'function') {
    return String(n)
  }
  return new Intl.NumberFormat(undefined, options).format(n)
}

export function debounce<T extends (...args: unknown[]) => unknown>(
  fn: T,
  delay: number,
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  return (...args: Parameters<T>) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      fn(...args)
      timer = null
    }, delay)
  }
}

export function truncate(str: string, maxLength: number): string {
  if (str.length <= maxLength) return str
  return str.slice(0, maxLength) + '...'
}

let _fallbackIdCounter = 0

function _fallbackHex(length: number): string {
  let out = ''
  for (let i = 0; i < length; i++) {
    out += Math.floor(Math.random() * 16).toString(16)
  }
  return out
}

function _uuidFromRandomValues(getRandomValues: (array: Uint8Array) => Uint8Array): string {
    const bytes = new Uint8Array(16)
    getRandomValues(bytes)
    bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40
    bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80

    const toHex2 = (value: number): string => {
      const n = value & 0xff
      const hex = n.toString(16)
      return hex.length === 1 ? `0${hex}` : hex
    }

    const hex = Array.from(bytes, (b) => toHex2(b)).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function _runtimeGlobal(): {
  crypto?: {
    randomUUID?: () => string
    getRandomValues?: (array: Uint8Array) => Uint8Array
  }
  msCrypto?: {
    getRandomValues?: (array: Uint8Array) => Uint8Array
  }
} {
  if (typeof globalThis !== 'undefined') return globalThis as unknown as {
    crypto?: {
      randomUUID?: () => string
      getRandomValues?: (array: Uint8Array) => Uint8Array
    }
    msCrypto?: {
      getRandomValues?: (array: Uint8Array) => Uint8Array
    }
  }
  if (typeof self !== 'undefined') return self as unknown as {
    crypto?: {
      randomUUID?: () => string
      getRandomValues?: (array: Uint8Array) => Uint8Array
    }
    msCrypto?: {
      getRandomValues?: (array: Uint8Array) => Uint8Array
    }
  }
  if (typeof window !== 'undefined') return window as unknown as {
    crypto?: {
      randomUUID?: () => string
      getRandomValues?: (array: Uint8Array) => Uint8Array
    }
    msCrypto?: {
      getRandomValues?: (array: Uint8Array) => Uint8Array
    }
  }
  return {}
}

export function generateId(): string {
  const g = _runtimeGlobal()
  const cryptoApi = g.crypto ?? g.msCrypto
  const randomUUID = g.crypto?.randomUUID

  if (randomUUID) {
    try {
      return randomUUID()
    } catch {
      /* continue to fallback */
    }
  }
  if (cryptoApi?.getRandomValues) {
    try {
      return _uuidFromRandomValues(cryptoApi.getRandomValues.bind(cryptoApi))
    } catch {
      /* continue to fallback */
    }
  }

  _fallbackIdCounter = (_fallbackIdCounter + 1) % 0x100000
  return `id-${Date.now().toString(36)}-${_fallbackIdCounter.toString(36)}-${_fallbackHex(8)}`
}
