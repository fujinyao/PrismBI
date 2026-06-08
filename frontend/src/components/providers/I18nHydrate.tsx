'use client'

import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { settingsApi } from '@/lib/api'
import { readStoredLocale, useI18nStore } from '@/stores/i18nStore'
import { detectBrowserLocale, normalizeLocale } from '@/lib/i18n/locales'

export function I18nHydrate() {
  const locale = useI18nStore((s) => s.locale)
  const setLocale = useI18nStore((s) => s.setLocale)
  const detected = useRef(false)
  const storedLocale = readStoredLocale()
  const { data: publicSettings } = useQuery({
    queryKey: ['settings', 'public'],
    queryFn: () => settingsApi.getPublic(),
    enabled: !storedLocale,
    staleTime: 5 * 60 * 1000,
  })

  useEffect(() => {
    if (detected.current) return
    detected.current = true

    const saved = readStoredLocale()
    if (saved) {
      setLocale(saved)
      return
    }

    setLocale(detectBrowserLocale(), { persist: false })
  }, [setLocale])

  useEffect(() => {
    if (storedLocale) return
    const systemLanguage = normalizeLocale((publicSettings as any)?.settings?.language ?? (publicSettings as any)?.language)
    if (systemLanguage) setLocale(systemLanguage, { persist: false })
  }, [publicSettings, setLocale, storedLocale])

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  return null
}
