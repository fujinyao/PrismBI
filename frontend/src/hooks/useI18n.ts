import { useI18nStore } from '@/stores/i18nStore'

export function useI18n() {
  const locale = useI18nStore((s) => s.locale)
  const setLocale = useI18nStore((s) => s.setLocale)
  const t = useI18nStore((s) => s.t)

  return { locale, setLocale, t }
}
