'use client'

import { useState, useCallback, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { settingsApi } from '@/lib/api'
import { Tabs } from '@/components/ui/Tabs'
import { BrandingSettings } from '@/components/settings/BrandingSettings'
import { ThemeSettings } from '@/components/settings/ThemeSettings'
import { LLMSettings } from '@/components/settings/LLMSettings'
import { GeneralSettings } from '@/components/settings/GeneralSettings'
import { SettingsAuditPanel } from '@/components/settings/SettingsAuditPanel'
import { SecuritySettings } from '@/components/settings/SecuritySettings'
import { AskRouterSettings } from '@/components/settings/AskRouterSettings'
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
    { key: 'ask', label: t('settings.tabs.ask', '问答') },
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
    mutationFn: (data: {
      provider?: string
      api_key?: string
      model?: string
      endpoint?: string
      max_tokens?: number
      temperature?: number
      extra_params?: Record<string, unknown>
      system_prompt?: string
      probed_capabilities?: Record<string, unknown>
      probe_session_id?: string
    }) =>
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
    }) =>
      settingsApi.general(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      toast(t('toast.generalSaved', 'General settings saved'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.generalSaveFailed', 'Failed to save general settings'), 'error'),
  })

  const askMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      settingsApi.askSettingsUpdate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      toast(t('toast.askSettingsSaved', '设置已保存'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('toast.askSettingsSaveFailed', '保存问答设置失败'), 'error'),
  })

  const routerMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      settingsApi.routerSettingsUpdate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
    },
    onError: (err) => {
      console.warn('Router settings save failed (ask save may have succeeded):', err)
    },
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
              onSave={(s) => llmMutation.mutateAsync(s)}
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
      case 'ask':
        return (
            <AskRouterSettings
              settings={settings}
              onSaveAsk={(s) => askMutation.mutate(s)}
              onSaveRouter={(s) => routerMutation.mutate(s)}
              savingAsk={askMutation.isPending}
              savingRouter={routerMutation.isPending}
              canSave={canUpdateSettings}
            />
            )
      case 'general':
        return (
          <div className="space-y-6">
            <GeneralSettings
              settings={settings}
              onSave={(s) => generalMutation.mutate(s)}
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
  }, [activeTab, canReadSettings, isLoading, isError, settings, brandingMutation, themeMutation, llmMutation, generalMutation, askMutation, routerMutation, languageMutation, canUpdateSettings, locale, refetch, t, queryClient])

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <Tabs tabs={TABS} activeKey={activeTab} onChange={setActiveTab} />

      <div className="mt-6">{renderTab()}</div>
    </div>
  )
}
