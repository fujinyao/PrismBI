'use client'

import { useState, useCallback, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { settingsApi, setRequestTimeout } from '@/lib/api'
import { Tabs } from '@/components/ui/Tabs'
import { BrandingSettings } from '@/components/settings/BrandingSettings'
import { ThemeSettings } from '@/components/settings/ThemeSettings'
import { LLMSettings } from '@/components/settings/LLMSettings'
import { GeneralSettings } from '@/components/settings/GeneralSettings'
import { SettingsAuditPanel } from '@/components/settings/SettingsAuditPanel'
import { SecuritySettings } from '@/components/settings/SecuritySettings'
import { LanguageSwitcher } from '@/components/settings/LanguageSwitcher'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'
import { useThemeStore } from '@/stores/themeStore'
import { useBrandingStore } from '@/stores/brandingStore'
import { normalizeLocale, type LocaleCode } from '@/lib/i18n/locales'
import { useAuthStore } from '@/stores/authStore'

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState('language')
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const t = useI18nStore((s) => s.t)
  const queryClient = useQueryClient()
  const setLocale = useI18nStore((s) => s.setLocale)
  const locale = useI18nStore((s) => s.locale)
  const setTheme = useThemeStore((s) => s.setTheme)
  const setBranding = useBrandingStore((s) => s.setBranding)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canReadSettings = hasPermission('settings', 'read')
  const canUpdateSettings = hasPermission('settings', 'update')
  const TABS = [
    { key: 'language', label: t('settings.tabs.interfaceLanguage', 'Interface Language') },
    { key: 'branding', label: t('settings.tabs.branding', 'Branding') },
    { key: 'theme', label: t('settings.tabs.theme', 'Theme') },
    { key: 'llm', label: t('settings.tabs.llm', 'LLM') },
    { key: 'security', label: t('settings.tabs.security', 'Security') },
    { key: 'general', label: t('settings.tabs.general', 'General') },
  ].filter((tab) => tab.key === 'language' || canReadSettings)

  useEffect(() => {
    if (activeTab !== 'language' && !canReadSettings) {
      setActiveTab('language')
    }
  }, [activeTab, canReadSettings])

  const { data: settingsData, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings', 'private'],
    queryFn: () => settingsApi.getAll(),
    enabled: canReadSettings,
  })

  const settings = useMemo(() => (canReadSettings ? ((settingsData as any)?.settings ?? {}) : {}), [canReadSettings, settingsData])

  useEffect(() => {
    if (settings.theme_mode || settings.theme_primary_color || settings.theme_border_radius || settings.theme_font) {
      const nextTheme = {
        mode: (settings.theme_mode as 'light' | 'dark' | 'system') || 'light',
        primaryColor: (settings.theme_primary_color as string) || '#1677ff',
        borderRadius: (settings.theme_border_radius as string) || 'md',
        font: (settings.theme_font as string) || 'Inter',
      }
      setTheme(nextTheme)
    }
  }, [settings.theme_mode, settings.theme_primary_color, settings.theme_border_radius, settings.theme_font, setTheme])

  const brandingMutation = useMutation({
    mutationFn: (data: { app_name?: string; app_description?: string; logo?: string; icon?: string }) =>
      settingsApi.branding(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'public'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      settingsApi.getAll().then((data) => {
        const s = (data as any)?.settings ?? (typeof data === 'object' && data !== null ? data : {})
        setBranding(s)
      }).catch(() => {})
      toast(t('toast.brandingSaved', 'Branding saved'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.brandingSaveFailed', 'Failed to save branding'), 'error'),
  })

  const themeMutation = useMutation({
    mutationFn: (data: { mode?: 'light' | 'dark' | 'system'; primary_color?: string; border_radius?: string; font?: string }) =>
      settingsApi.theme(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      toast(t('toast.themeSaved', 'Theme saved'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.themeSaveFailed', 'Failed to save theme'), 'error'),
  })

  const llmMutation = useMutation({
    mutationFn: (data: { provider?: string; api_key?: string; model?: string; endpoint?: string; max_tokens?: number; temperature?: number; extra_params?: Record<string, unknown>; system_prompt?: string }) =>
      settingsApi.llm(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      toast(t('toast.llmSaved', 'LLM settings saved'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.llmSaveFailed', 'Failed to save LLM settings'), 'error'),
  })

  const generalMutation = useMutation({
    mutationFn: (data: {
      language?: string
      default_page?: string
      telemetry?: boolean
      timezone?: string
      date_format?: string
      session_timeout?: number
      route_observability_window_minutes?: number
      request_timeout_ms?: number
      llm_read_timeout_s?: number
      db_connect_timeout_s?: number
      route_observability_persist_enabled?: boolean
      route_observability_persist_interval_seconds?: number
      route_observability_persist_event_delta?: number
      model_ref_case_sensitive?: boolean
    }) =>
      settingsApi.general(data),
    onSuccess: (_data, variables) => {
      queryClient.setQueryData(['settings', 'private'], (old: any) => {
        const currentSettings = old?.settings ?? old ?? {}
        const mapped: Record<string, unknown> = {
          ...variables,
          request_timeout_ms: variables.request_timeout_ms,
          timeout_request_ms: variables.request_timeout_ms,
          llm_read_timeout_s: variables.llm_read_timeout_s,
          timeout_llm_read_s: variables.llm_read_timeout_s,
          db_connect_timeout_s: variables.db_connect_timeout_s,
          timeout_db_connect_s: variables.db_connect_timeout_s,
          router_route_observability_persist_enabled: variables.route_observability_persist_enabled,
          router_route_observability_persist_interval_seconds: variables.route_observability_persist_interval_seconds,
          router_route_observability_persist_event_delta: variables.route_observability_persist_event_delta,
          router_model_ref_case_sensitive: variables.model_ref_case_sensitive,
        }
        if (typeof variables.route_observability_window_minutes === 'number') {
          mapped.router_route_observability_window_seconds = Math.round(variables.route_observability_window_minutes * 60)
        }
        const nextSettings = { ...currentSettings, ...mapped }
        return { ...(old ?? {}), settings: nextSettings, ...nextSettings }
      })
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      if (variables.request_timeout_ms) {
        setRequestTimeout(variables.request_timeout_ms)
      }
      toast(t('toast.generalSaved', 'General settings saved'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.generalSaveFailed', 'Failed to save general settings'), 'error'),
  })

  const routerRuntimeReloadMutation = useMutation({
    mutationFn: () => settingsApi.routerRuntimeReload(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      toast(t('toast.routerRuntimeReloaded', 'Router runtime reloaded'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.routerRuntimeReloadFailed', 'Failed to reload router runtime'), 'error'),
  })

  const languageMutation = useMutation({
    mutationFn: async (lang: LocaleCode) => lang,
    onSuccess: (_data, lang) => {
      setLocale(normalizeLocale(lang))
      toast(t('toast.languageUpdated', 'Language updated'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.languageUpdateFailed', 'Failed to update language'), 'error'),
  })

  const renderTab = useCallback(() => {
    if (activeTab !== 'language' && !canReadSettings) {
      return <ErrorToast message={t('auth.permissionDenied', 'Permission denied')} onClose={() => setActiveTab('language')} />
    }
    if (activeTab !== 'language' && isLoading) return <Skeleton className="h-40 w-full" />
    if (activeTab !== 'language' && isError) return <ErrorToast message={t('toast.settingsLoadFailed', 'Failed to load settings')} onRetry={() => refetch()} onClose={() => setError(null)} />

    switch (activeTab) {
      case 'branding':
        return (
            <BrandingSettings
              settings={settings}
              onSave={(s) => brandingMutation.mutate(s)}
              saving={brandingMutation.isPending}
              canSave={canUpdateSettings}
            />
        )
      case 'theme':
        return (
            <ThemeSettings
              settings={settings}
              onSave={(s) => themeMutation.mutate(s)}
              saving={themeMutation.isPending}
              canSave={canUpdateSettings}
            />
        )
      case 'llm':
        return (
            <LLMSettings
              settings={settings}
              onSave={(s) => llmMutation.mutate(s)}
              saving={llmMutation.isPending}
              canSave={canUpdateSettings}
            />
        )
      case 'security':
        return (
            <SecuritySettings
              settings={settings}
              canSave={canUpdateSettings}
            />
        )
      case 'general':
        return (
          <div className="space-y-6">
            <GeneralSettings
              settings={settings}
              onSave={(s) => generalMutation.mutate(s)}
              onRouterRuntimeReload={() => routerRuntimeReloadMutation.mutate()}
              routerRuntimeReloading={routerRuntimeReloadMutation.isPending}
              saving={generalMutation.isPending}
              canSave={canUpdateSettings}
            />
            <SettingsAuditPanel />
          </div>
        )
      case 'language':
        return (
          <LanguageSwitcher
            value={normalizeLocale(locale)}
            onChange={(locale) => languageMutation.mutate(locale)}
            saving={languageMutation.isPending}
          />
        )
      default:
        return <Skeleton className="h-40 w-full" />
    }
  }, [activeTab, canReadSettings, isLoading, isError, settings, brandingMutation, themeMutation, llmMutation, generalMutation, routerRuntimeReloadMutation, languageMutation, canUpdateSettings, locale, refetch, t, queryClient])

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <Tabs tabs={TABS} activeKey={activeTab} onChange={setActiveTab} />

      <div className="mt-6">{renderTab()}</div>
    </div>
  )
}
