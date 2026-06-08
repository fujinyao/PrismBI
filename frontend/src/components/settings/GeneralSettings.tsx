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
  onRouterRuntimeReload?: () => void
  routerRuntimeReloading?: boolean
  saving?: boolean
  canSave?: boolean
}

export interface GeneralSettingsPayload {
  language: string
  timezone: string
  date_format: string
  session_timeout: number
  request_timeout_ms: number
  llm_read_timeout_s: number
  db_connect_timeout_s: number
  route_observability_window_minutes: number
  route_observability_persist_enabled: boolean
  route_observability_persist_interval_seconds: number
  route_observability_persist_event_delta: number
  model_ref_case_sensitive: boolean
}

export interface GeneralSettingsSubmitValues {
  language: string
  timezone: string
  dateFormat: string
  sessionTimeout: number
  requestTimeout: number
  llmReadTimeout: number
  dbConnectTimeout: number
  routeObservabilityWindowMinutes: number
  routeObservabilityPersistEnabled: boolean
  routeObservabilityPersistIntervalSeconds: number
  routeObservabilityPersistEventDelta: number
  modelRefCaseSensitive: boolean
}

export function buildGeneralSettingsPayload(values: GeneralSettingsSubmitValues): GeneralSettingsPayload {
  const normalizedRouteWindowMinutes = Math.min(
    1440,
    Math.max(
      5,
      Number.isFinite(values.routeObservabilityWindowMinutes)
        ? values.routeObservabilityWindowMinutes
        : 30,
    ),
  )
  const normalizedPersistIntervalSeconds = Math.min(
    3600,
    Math.max(
      1,
      Number.isFinite(values.routeObservabilityPersistIntervalSeconds)
        ? values.routeObservabilityPersistIntervalSeconds
        : 30,
    ),
  )
  const normalizedPersistEventDelta = Math.min(
    10000,
    Math.max(
      1,
      Math.round(
        Number.isFinite(values.routeObservabilityPersistEventDelta)
          ? values.routeObservabilityPersistEventDelta
          : 20,
      ),
    ),
  )
  return {
    language: values.language,
    timezone: values.timezone,
    date_format: values.dateFormat,
    session_timeout: values.sessionTimeout,
    request_timeout_ms: values.requestTimeout,
    llm_read_timeout_s: values.llmReadTimeout,
    db_connect_timeout_s: values.dbConnectTimeout,
    route_observability_window_minutes: normalizedRouteWindowMinutes,
    route_observability_persist_enabled: values.routeObservabilityPersistEnabled,
    route_observability_persist_interval_seconds: normalizedPersistIntervalSeconds,
    route_observability_persist_event_delta: normalizedPersistEventDelta,
    model_ref_case_sensitive: values.modelRefCaseSensitive,
  }
}

const parseBooleanSetting = (value: unknown, fallback: boolean): boolean => {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (value === 1) return true
    if (value === 0) return false
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'true' || normalized === '1') return true
    if (normalized === 'false' || normalized === '0') return false
  }
  return fallback
}

const parsePositiveNumberSetting = (value: unknown, fallback: number): number => {
  const parsed = Number(value)
  if (Number.isFinite(parsed) && parsed > 0) return parsed
  return fallback
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
  onRouterRuntimeReload,
  routerRuntimeReloading,
  saving,
  canSave = true,
}: GeneralSettingsProps) {
  const t = useI18nStore((s) => s.t)
  const [language, setLanguage] = useState<SystemLanguageCode>((settings.language as SystemLanguageCode) ?? 'en')
  const [timezone, setTimezone] = useState(settings.timezone ?? 'UTC')
  const [dateFormat, setDateFormat] = useState(settings.date_format ?? settings.dateFormat ?? 'YYYY-MM-DD')
  const [sessionTimeout, setSessionTimeout] = useState(settings.session_timeout ?? settings.sessionTimeout ?? 60)
  const [requestTimeout, setRequestTimeout] = useState(settings.request_timeout_ms ?? settings.timeout_request_ms ?? 120000)
  const [llmReadTimeout, setLlmReadTimeout] = useState(settings.llm_read_timeout_s ?? settings.timeout_llm_read_s ?? 120)
  const [dbConnectTimeout, setDbConnectTimeout] = useState(settings.db_connect_timeout_s ?? settings.timeout_db_connect_s ?? 10)
  const [routeObservabilityWindowMinutes, setRouteObservabilityWindowMinutes] = useState(() => {
    const rawSeconds = settings.router_route_observability_window_seconds
    const parsedSeconds = Number(rawSeconds)
    if (Number.isFinite(parsedSeconds) && parsedSeconds > 0) {
      return Math.max(5, Math.round(parsedSeconds / 60))
    }
    return 30
  })
  const [routeObservabilityPersistEnabled, setRouteObservabilityPersistEnabled] = useState(() =>
    parseBooleanSetting(settings.router_route_observability_persist_enabled, true),
  )
  const [routeObservabilityPersistIntervalSeconds, setRouteObservabilityPersistIntervalSeconds] = useState(() =>
    parsePositiveNumberSetting(settings.router_route_observability_persist_interval_seconds, 30),
  )
  const [routeObservabilityPersistEventDelta, setRouteObservabilityPersistEventDelta] = useState(() =>
    parsePositiveNumberSetting(settings.router_route_observability_persist_event_delta, 20),
  )
  const [modelRefCaseSensitive, setModelRefCaseSensitive] = useState(() =>
    parseBooleanSetting(settings.router_model_ref_case_sensitive, true),
  )

  const [settingsApplied, setSettingsApplied] = useState(false)

  useEffect(() => {
    if (settingsApplied || !settings) return
    const nextLanguage = SYSTEM_LANGUAGES.some((locale) => locale.code === settings.language) ? settings.language as SystemLanguageCode : 'en'
    setLanguage(nextLanguage)
    setTimezone(settings.timezone ?? 'UTC')
    setDateFormat(settings.date_format ?? settings.dateFormat ?? 'YYYY-MM-DD')
    setSessionTimeout(settings.session_timeout ?? settings.sessionTimeout ?? 60)
    setRequestTimeout(settings.request_timeout_ms ?? settings.timeout_request_ms ?? 120000)
    setLlmReadTimeout(settings.llm_read_timeout_s ?? settings.timeout_llm_read_s ?? 120)
    setDbConnectTimeout(settings.db_connect_timeout_s ?? settings.timeout_db_connect_s ?? 10)
    const rawWindowSeconds = Number(settings.router_route_observability_window_seconds)
    if (Number.isFinite(rawWindowSeconds) && rawWindowSeconds > 0) {
      setRouteObservabilityWindowMinutes(Math.max(5, Math.round(rawWindowSeconds / 60)))
    } else {
      setRouteObservabilityWindowMinutes(30)
    }
    setRouteObservabilityPersistEnabled(parseBooleanSetting(settings.router_route_observability_persist_enabled, true))
    setRouteObservabilityPersistIntervalSeconds(parsePositiveNumberSetting(settings.router_route_observability_persist_interval_seconds, 30))
    setRouteObservabilityPersistEventDelta(parsePositiveNumberSetting(settings.router_route_observability_persist_event_delta, 20))
    setModelRefCaseSensitive(parseBooleanSetting(settings.router_model_ref_case_sensitive, true))
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
        requestTimeout,
        llmReadTimeout,
        dbConnectTimeout,
        routeObservabilityWindowMinutes,
        routeObservabilityPersistEnabled,
        routeObservabilityPersistIntervalSeconds,
        routeObservabilityPersistEventDelta,
        modelRefCaseSensitive,
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
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Input
                label={t('settings.general.requestTimeout', 'Request Timeout (ms)')}
                type="number"
                value={requestTimeout}
                onChange={(e) => setRequestTimeout(parseInt(e.target.value) || 30000)}
                min={5000}
                max={600000}
                hint={t('settings.general.requestTimeoutHelp', 'HTTP request timeout in milliseconds. Increase for slow LLM responses.')}
              />
              <Input
                label={t('settings.general.llmReadTimeout', 'LLM Read Timeout (seconds)')}
                type="number"
                value={llmReadTimeout}
                onChange={(e) => setLlmReadTimeout(parseInt(e.target.value) || 10)}
                min={5}
                max={600}
                hint={t('settings.general.llmReadTimeoutHelp', 'Timeout for waiting on LLM response data.')}
              />
              <Input
                label={t('settings.general.dbConnectTimeout', 'DB Connect Timeout (seconds)')}
                type="number"
                value={dbConnectTimeout}
                onChange={(e) => setDbConnectTimeout(parseInt(e.target.value) || 10)}
                min={1}
                max={120}
                hint={t('settings.general.dbConnectTimeoutHelp', 'Connection timeout for external database connections.')}
              />
              <Input
                label={t('settings.general.routeObservabilityWindow', 'Route Observability Window (minutes)')}
                type="number"
                value={routeObservabilityWindowMinutes}
                onChange={(e) => setRouteObservabilityWindowMinutes(parseInt(e.target.value) || 30)}
                min={5}
                max={1440}
                hint={t('settings.general.routeObservabilityWindowHelp', 'Used by route observability counters and alerts. Default is 30 minutes.')}
              />
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                  <input
                    type="checkbox"
                    checked={routeObservabilityPersistEnabled}
                    onChange={(e) => setRouteObservabilityPersistEnabled(e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                  />
                  {t('settings.general.routeObservabilityPersistEnabled', 'Persist route observability snapshot')}
                </label>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {t('settings.general.routeObservabilityPersistHelp', 'When enabled, route observability snapshots are periodically saved to metadata settings for restart recovery.')}
              </p>
              <div className="grid gap-4 md:grid-cols-2">
                <Input
                  label={t('settings.general.routeObservabilityPersistInterval', 'Snapshot persist interval (seconds)')}
                  type="number"
                  value={routeObservabilityPersistIntervalSeconds}
                  onChange={(e) => setRouteObservabilityPersistIntervalSeconds(parseFloat(e.target.value) || 30)}
                  min={1}
                  max={3600}
                  disabled={!routeObservabilityPersistEnabled}
                  hint={t('settings.general.routeObservabilityPersistIntervalHelp', 'Persist snapshot at least once per interval, even under low traffic.')}
                />
                <Input
                  label={t('settings.general.routeObservabilityPersistEventDelta', 'Snapshot persist event delta')}
                  type="number"
                  value={routeObservabilityPersistEventDelta}
                  onChange={(e) => setRouteObservabilityPersistEventDelta(parseInt(e.target.value) || 20)}
                  min={1}
                  max={10000}
                  disabled={!routeObservabilityPersistEnabled}
                  hint={t('settings.general.routeObservabilityPersistEventDeltaHelp', 'Persist immediately after this many new events since the last snapshot.')}
                />
              </div>
              <div className="flex items-center gap-3 pt-2">
                <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                  <input
                    type="checkbox"
                    checked={modelRefCaseSensitive}
                    onChange={(e) => setModelRefCaseSensitive(e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                  />
                  {t('settings.general.modelRefCaseSensitive', 'Model reference case-sensitive matching')}
                </label>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {t('settings.general.modelRefCaseSensitiveHelp', 'When enabled, SQL model references must match semantic model names with exact letter casing. Disable only if your project requires case-insensitive matching.')}
              </p>
              {!modelRefCaseSensitive && (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                  {t('settings.general.modelRefCaseSensitiveWarning', 'Case-insensitive matching can bind similarly named models unexpectedly. Keep this disabled only for legacy projects with inconsistent model-name casing.')}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3">
              <div>
                <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.general.routerRuntime', 'Router Runtime')}
                </h3>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  {t('settings.general.routerReloadHint', 'Use this when router settings are changed directly in the database and need to be applied without restarting services.')}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={onRouterRuntimeReload}
                  loading={routerRuntimeReloading}
                  disabled={!canSave || !onRouterRuntimeReload}
                >
                  {t('settings.general.routerReload', 'Reload Runtime Settings')}
                </Button>
                <div className="ml-auto">
                  <Button type="submit" loading={saving} disabled={!canSave}>{t('settings.general.save', 'Save General Settings')}</Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </CardContent>
      </Card>
    </form>
  )
}
