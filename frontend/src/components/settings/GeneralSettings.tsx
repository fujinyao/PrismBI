'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'
import { SYSTEM_LANGUAGES, type SystemLanguageCode } from '@/lib/i18n/locales'

interface GeneralSettingsProps {
  settings: any
  onSave: (s: any) => void
  saving?: boolean
  canSave?: boolean
}

export interface GeneralSettingsPayload {
  language: string
  timezone: string
  date_format: string
  session_timeout: number
}

export interface GeneralSettingsSubmitValues {
  language: string
  timezone: string
  dateFormat: string
  sessionTimeout: number
}

export function buildGeneralSettingsPayload(values: GeneralSettingsSubmitValues): GeneralSettingsPayload {
  return {
    language: values.language,
    timezone: values.timezone,
    date_format: values.dateFormat,
    session_timeout: values.sessionTimeout,
  }
}

const timezones = [
  { label: 'UTC (Coordinated Universal Time)', value: 'UTC' },
  { label: 'US/Eastern', value: 'US/Eastern' },
  { label: 'US/Pacific', value: 'US/Pacific' },
  { label: 'Europe/London', value: 'Europe/London' },
  { label: 'Asia/Shanghai', value: 'Asia/Shanghai' },
  { label: 'Asia/Tokyo', value: 'Asia/Tokyo' },
]

const dateFormats = [
  { label: 'YYYY-MM-DD', value: 'YYYY-MM-DD' },
  { label: 'DD-MM-YYYY', value: 'DD-MM-YYYY' },
  { label: 'MM-DD-YYYY', value: 'MM-DD-YYYY' },
  { label: 'YYYY/MM/DD', value: 'YYYY/MM/DD' },
]

const LANGUAGE_DEFAULTS: Record<string, { timezone: string; dateFormat: string }> = {
  en: { timezone: 'US/Eastern', dateFormat: 'MM-DD-YYYY' },
  zh: { timezone: 'Asia/Shanghai', dateFormat: 'YYYY-MM-DD' },
  ar: { timezone: 'UTC', dateFormat: 'DD-MM-YYYY' },
  es: { timezone: 'Europe/Madrid', dateFormat: 'DD-MM-YYYY' },
  fr: { timezone: 'Europe/Paris', dateFormat: 'DD-MM-YYYY' },
  ru: { timezone: 'Europe/Moscow', dateFormat: 'DD-MM-YYYY' },
}

export function GeneralSettings({
  settings,
  onSave,
  saving,
  canSave = true,
}: GeneralSettingsProps) {
  const t = useI18nStore((s) => s.t)
  const [language, setLanguage] = useState<SystemLanguageCode>((settings.language as SystemLanguageCode) ?? 'en')
  const [timezone, setTimezone] = useState(settings.timezone ?? 'UTC')
  const [dateFormat, setDateFormat] = useState(settings.date_format ?? settings.dateFormat ?? 'YYYY-MM-DD')
  const [sessionTimeout, setSessionTimeout] = useState(settings.session_timeout ?? settings.sessionTimeout ?? 60)

  const [settingsApplied, setSettingsApplied] = useState(false)

  useEffect(() => {
    if (settingsApplied || !settings) return
    const nextLanguage = SYSTEM_LANGUAGES.some((locale) => locale.code === settings.language) ? settings.language as SystemLanguageCode : 'en'
    setLanguage(nextLanguage)
    setTimezone(settings.timezone ?? 'UTC')
    setDateFormat(settings.date_format ?? settings.dateFormat ?? 'YYYY-MM-DD')
    setSessionTimeout(settings.session_timeout ?? settings.sessionTimeout ?? 60)
    setSettingsApplied(true)
  }, [settings, settingsApplied])

  const handleLanguageChange = (nextLanguage: SystemLanguageCode) => {
    setLanguage(nextLanguage)
    const defaults = LANGUAGE_DEFAULTS[nextLanguage]
    if (defaults) {
      setTimezone(defaults.timezone)
      setDateFormat(defaults.dateFormat)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    try {
      onSave(buildGeneralSettingsPayload({
        language,
        timezone,
        dateFormat,
        sessionTimeout,
      }))
    } catch {
      /* caller handles */
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <Card>
        <CardContent className="space-y-4">
          <Card>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.general.defaultLanguage', 'Default Language')}
                </label>
                <select
                  value={language}
                  onChange={(e) => handleLanguageChange(e.target.value as SystemLanguageCode)}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  {SYSTEM_LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>{l.nativeLabel}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.general.timezone', 'Timezone')}
                </label>
                <select
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  {timezones.map((tz) => (
                    <option key={tz.value} value={tz.value}>{t(`settings.general.tz.${tz.value.replace(/[^a-zA-Z]/g, '')}`, tz.label)}</option>
                  ))}
                </select>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.general.dateFormat', 'Date Format')}
                </label>
                <select
                  value={dateFormat}
                  onChange={(e) => setDateFormat(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  {dateFormats.map((df) => (
                    <option key={df.value} value={df.value}>{t(`settings.general.dateFormat.${df.value}`, df.label)}</option>
                  ))}
                </select>
              </div>

              <Input
                label={t('settings.general.sessionTimeout', 'Session Timeout (minutes)')}
                type="number"
                value={sessionTimeout}
                onChange={(e) => setSessionTimeout(parseInt(e.target.value) || 0)}
                min={1}
                max={1440}
              />
            </CardContent>
          </Card>



          <Card>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" loading={saving} disabled={!canSave}>{t('settings.general.save', 'Save General Settings')}</Button>
              </div>
            </CardContent>
          </Card>
        </CardContent>
      </Card>
    </form>
  )
}
