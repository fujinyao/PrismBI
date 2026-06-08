import { create } from 'zustand'
import { type LocaleCode, MESSAGES, normalizeLocale } from '@/lib/i18n/locales'

export const I18N_STORAGE_KEY = 'i18n-store'

const RTL_LOCALES: Set<string> = new Set(['ar', 'fa', 'ur', 'he'])

export function isRTLLocale(locale: string): boolean {
  return RTL_LOCALES.has((locale.split('-')[0] ?? locale).toLowerCase())
}

export function formatDate(value: string | Date, locale?: string, options?: Intl.DateTimeFormatOptions): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (isNaN(date.getTime())) return String(value)
  const loc = locale || 'en'
  const hasIntlDate = typeof Intl !== 'undefined' && typeof Intl.DateTimeFormat === 'function'
  if (!hasIntlDate) {
    const mm = date.getMonth() + 1
    const dd = date.getDate()
    return `${date.getFullYear()}-${mm < 10 ? `0${mm}` : String(mm)}-${dd < 10 ? `0${dd}` : String(dd)}`
  }
  return new Intl.DateTimeFormat(loc, options || { year: 'numeric', month: 'short', day: 'numeric' }).format(date)
}

export function formatNumber(value: number, locale?: string, options?: Intl.NumberFormatOptions): string {
  const loc = locale || 'en'
  if (typeof Intl === 'undefined' || typeof Intl.NumberFormat !== 'function') {
    return String(value)
  }
  return new Intl.NumberFormat(loc, options).format(value)
}

export function formatRelativeTime(date: string | Date, locale?: string): string {
  const d = typeof date === 'string' ? new Date(date) : date
  if (isNaN(d.getTime())) return ''
  const now = Date.now()
  const diff = now - d.getTime()
  const loc = locale || 'en'
  const intlObj = typeof Intl !== 'undefined' ? Intl : undefined
  const RelativeTimeFormatCtor = (intlObj as unknown as { RelativeTimeFormat?: new (locale?: string, options?: Intl.RelativeTimeFormatOptions) => Intl.RelativeTimeFormat } | undefined)?.RelativeTimeFormat
  if (!RelativeTimeFormatCtor) {
    return formatDate(d, loc)
  }
  const rtf = new RelativeTimeFormatCtor(loc, { numeric: 'auto' })
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return rtf.format(-seconds, 'second')
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return rtf.format(-minutes, 'minute')
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return rtf.format(-hours, 'hour')
  const days = Math.floor(hours / 24)
  if (days < 30) return rtf.format(-days, 'day')
  const months = Math.floor(days / 30)
  return rtf.format(-months, 'month')
}

export function readStoredLocale(): LocaleCode | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(I18N_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return typeof parsed?.state?.locale === 'string' ? normalizeLocale(parsed.state.locale) : null
  } catch {
    return null
  }
}

interface I18nState {
  locale: LocaleCode
  messages: Record<string, string>
  hydrated: boolean
  setLocale: (locale: LocaleCode, options?: { persist?: boolean }) => void
  t: (key: string, fallback?: string, params?: Record<string, string>) => string
}

export const useI18nStore = create<I18nState>()((set, get) => ({
  locale: 'en',
  messages: MESSAGES.en ?? {},
  hydrated: false,

  setLocale: (locale: LocaleCode, options?: { persist?: boolean }) => {
    const normalized = normalizeLocale(locale)
    if (options?.persist !== false && typeof window !== 'undefined') {
      try {
        localStorage.setItem(I18N_STORAGE_KEY, JSON.stringify({ state: { locale: normalized } }))
      } catch { /* ignore */ }
    }
    if (typeof document !== 'undefined') {
      document.documentElement.dir = isRTLLocale(normalized) ? 'rtl' : 'ltr'
      document.documentElement.lang = normalized
    }
    set({ locale: normalized, messages: MESSAGES[normalized] ?? MESSAGES.en ?? {}, hydrated: true })
  },

  t: (key: string, fallback?: string, params?: Record<string, string>) => {
    let msg = get().messages[key] ?? fallback ?? key
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        const needle = `{${k}}`
        msg = typeof msg.replaceAll === 'function'
          ? msg.replaceAll(needle, String(v))
          : msg.split(needle).join(String(v))
      }
    }
    return msg
  },
}))
