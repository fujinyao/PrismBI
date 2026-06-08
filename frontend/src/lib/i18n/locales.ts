import en from './locales/en.json'
import zh from './locales/zh.json'
import es from './locales/es.json'
import fr from './locales/fr.json'
import de from './locales/de.json'
import ja from './locales/ja.json'
import ko from './locales/ko.json'
import pt from './locales/pt.json'
import ru from './locales/ru.json'
import ar from './locales/ar.json'
import hi from './locales/hi.json'
import id from './locales/id.json'
import it from './locales/it.json'
import nl from './locales/nl.json'
import pl from './locales/pl.json'
import bn from './locales/bn.json'
import ur from './locales/ur.json'
import ms from './locales/ms.json'
import vi from './locales/vi.json'
import th from './locales/th.json'
import tr from './locales/tr.json'
import uk from './locales/uk.json'
import fa from './locales/fa.json'
import sw from './locales/sw.json'

export const LOCALES = [
  { code: 'en', label: 'English', nativeLabel: 'English' },
  { code: 'zh', label: 'Chinese', nativeLabel: '中文' },
  { code: 'es', label: 'Spanish', nativeLabel: 'Español' },
  { code: 'ar', label: 'Arabic', nativeLabel: 'العربية' },
  { code: 'fr', label: 'French', nativeLabel: 'Français' },
  { code: 'ru', label: 'Russian', nativeLabel: 'Русский' },
  { code: 'pt', label: 'Portuguese', nativeLabel: 'Português' },
  { code: 'de', label: 'German', nativeLabel: 'Deutsch' },
  { code: 'ja', label: 'Japanese', nativeLabel: '日本語' },
  { code: 'ko', label: 'Korean', nativeLabel: '한국어' },
  { code: 'hi', label: 'Hindi', nativeLabel: 'हिन्दी' },
  { code: 'bn', label: 'Bengali', nativeLabel: 'বাংলা' },
  { code: 'ur', label: 'Urdu', nativeLabel: 'اردو' },
  { code: 'id', label: 'Indonesian', nativeLabel: 'Bahasa Indonesia' },
  { code: 'ms', label: 'Malay', nativeLabel: 'Bahasa Melayu' },
  { code: 'vi', label: 'Vietnamese', nativeLabel: 'Tiếng Việt' },
  { code: 'th', label: 'Thai', nativeLabel: 'ไทย' },
  { code: 'tr', label: 'Turkish', nativeLabel: 'Türkçe' },
  { code: 'it', label: 'Italian', nativeLabel: 'Italiano' },
  { code: 'nl', label: 'Dutch', nativeLabel: 'Nederlands' },
  { code: 'pl', label: 'Polish', nativeLabel: 'Polski' },
  { code: 'uk', label: 'Ukrainian', nativeLabel: 'Українська' },
  { code: 'fa', label: 'Persian', nativeLabel: 'فارسی' },
  { code: 'sw', label: 'Swahili', nativeLabel: 'Kiswahili' },
] as const

export type LocaleCode = (typeof LOCALES)[number]['code']

export interface LocaleDef {
  code: LocaleCode
  label: string
  nativeLabel: string
}

export const SYSTEM_LANGUAGES = [
  { code: 'ar', label: 'Arabic', nativeLabel: 'العربية' },
  { code: 'zh', label: 'Chinese', nativeLabel: '中文' },
  { code: 'en', label: 'English', nativeLabel: 'English' },
  { code: 'fr', label: 'French', nativeLabel: 'Français' },
  { code: 'ru', label: 'Russian', nativeLabel: 'Русский' },
  { code: 'es', label: 'Spanish', nativeLabel: 'Español' },
] as const

export type SystemLanguageCode = (typeof SYSTEM_LANGUAGES)[number]['code']

export const MESSAGES: Partial<Record<LocaleCode, Record<string, string>>> = { en, zh, es, fr, de, ja, ko, pt, ru, ar, hi, id, it, nl, pl, bn, ur, ms, vi, th, tr, uk, fa, sw }

const LOCALE_CODES = new Set<string>(LOCALES.map((locale) => locale.code))

export function normalizeLocale(value: unknown): LocaleCode {
  if (typeof value !== 'string') return 'en'
  const normalized = value.toLowerCase().replace('_', '-').trim()
  const exact = normalized as LocaleCode
  if (LOCALE_CODES.has(exact)) return exact
  const base = normalized.split('-')[0] as LocaleCode
  return LOCALE_CODES.has(base) ? base : 'en'
}

export function detectBrowserLocale(): LocaleCode {
  if (typeof window === 'undefined') return 'en'
  const raw = navigator.language || (navigator as any).userLanguage || ''
  return normalizeLocale(raw)
}
