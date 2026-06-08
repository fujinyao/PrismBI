'use client'

import { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminSSOApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { RequirePermission } from '@/components/providers/RequirePermission'

export default function SSOConfigPage() {
  return (
    <RequirePermission resource="sso" action="read">
      <SSOConfigContent />
    </RequirePermission>
  )
}

function SSOConfigContent() {
  const t = useI18nStore((s) => s.t)
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const { data: ssoConfig, isLoading } = useQuery({
    queryKey: ['sso-config'],
    queryFn: () => adminSSOApi.get(),
  })

  const effectiveConfig: Record<string, unknown> = useMemo(() => (ssoConfig && typeof ssoConfig === 'object' && Object.keys(ssoConfig as object).length > 0) ? (ssoConfig as Record<string, unknown>) : {}, [ssoConfig])

  const [provider, setProvider] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [issuerUrl, setIssuerUrl] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [mappingRules, setMappingRules] = useState<Record<string, unknown>>({})

  const [configApplied, setConfigApplied] = useState(false)

  useEffect(() => {
    if (configApplied || !effectiveConfig || typeof effectiveConfig !== 'object' || Object.keys(effectiveConfig).length === 0) return
    setProvider(String(effectiveConfig.provider ?? ''))
    setClientId(String(effectiveConfig.client_id ?? ''))
    setClientSecret(String(effectiveConfig.client_secret ?? ''))
    setIssuerUrl(String(effectiveConfig.issuer_url ?? ''))
    setEnabled(Boolean(effectiveConfig.enabled))
    setMappingRules(effectiveConfig.mapping_rules && typeof effectiveConfig.mapping_rules === 'object' ? effectiveConfig.mapping_rules as Record<string, unknown> : {})
    setConfigApplied(true)
  }, [effectiveConfig, configApplied])

  const saveMutation = useMutation({
    mutationFn: () => adminSSOApi.update({
      provider: provider || undefined,
      client_id: clientId,
      client_secret: clientSecret || undefined,
      issuer_url: issuerUrl,
      mapping_rules: Object.keys(mappingRules).length > 0 ? mappingRules : undefined,
      enabled,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sso-config'] })
      setSuccess(t('admin.sso.saved', 'SSO configuration saved'))
      setError(null)
      setTimeout(() => setSuccess(null), 3000)
    },
    onError: () => {
      setError(t('admin.sso.failedToSave', 'Failed to save SSO configuration'))
      setSuccess(null)
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
          {t('admin.sso.title', 'SSO / OIDC Configuration')}
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {t('admin.sso.description', 'Configure Single Sign-On (SSO) using OpenID Connect (OIDC) or SAML providers.')}
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">{t('common.dismiss', 'Dismiss')}</button>
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-lg bg-green-50 p-3 text-sm text-green-700 dark:bg-green-900/20 dark:text-green-400">
          {success}
        </div>
      )}

      <div className="max-w-2xl space-y-6">
        <div className="flex items-center gap-3">
          <label className="relative inline-flex cursor-pointer items-center">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="peer sr-only" />
            <div className="peer h-6 w-11 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-gray-200 after:bg-white after:transition-all after:content-[''] peer-checked:bg-primary-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none dark:bg-gray-700"></div>
          </label>
          <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
            {t('admin.sso.enableSSO', 'Enable SSO')}
          </span>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('admin.sso.provider', 'Provider')}</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm dark:border-gray-600 dark:bg-gray-800">
            <option value="">{t('admin.sso.selectProvider', 'Select provider...')}</option>
            <option value="oidc">OpenID Connect (OIDC)</option>
            <option value="google">Google</option>
            <option value="github">GitHub</option>
            <option value="azure">Azure AD</option>
            <option value="okta">Okta</option>
          </select>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('admin.sso.issuerUrl', 'Issuer URL')}</label>
          <input
            type="url"
            value={issuerUrl}
            onChange={(e) => setIssuerUrl(e.target.value)}
            placeholder="https://accounts.google.com"
            className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm dark:border-gray-600 dark:bg-gray-800"
          />
          <p className="mt-1 text-xs text-gray-500">{t('admin.sso.issuerUrlHelp', 'The OIDC issuer URL (e.g., https://accounts.google.com). Used for auto-discovery of endpoints.')}</p>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('admin.sso.clientId', 'Client ID')}</label>
          <input
            type="text"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="your-client-id"
            className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm dark:border-gray-600 dark:bg-gray-800"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('admin.sso.clientSecret', 'Client Secret')}</label>
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder={effectiveConfig?.client_secret === '********' ? t('admin.sso.secretPlaceholder', 'Leave empty to keep current secret') : ''}
            className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm dark:border-gray-600 dark:bg-gray-800"
          />
          {effectiveConfig?.client_secret === '********' && (
            <p className="mt-1 text-xs text-gray-500">{t('admin.sso.secretMasked', 'Current secret is masked. Leave empty to keep it unchanged.')}</p>
          )}
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('admin.sso.mappingRules', 'Claim → Role Mapping')}</label>
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800">
            <p className="mb-2 text-xs text-gray-500">{t('admin.sso.mappingRulesHelp', 'Map OIDC claims to PrismBI roles. JSON format: {"claim_name": "role_name"}. Example: {"groups": "admin", "department": "analytics"}')}</p>
            <textarea
              value={JSON.stringify(mappingRules, null, 2)}
              onChange={(e) => {
                try {
                  setMappingRules(JSON.parse(e.target.value))
                  setError(null)
                } catch {
                  setError(t('admin.sso.invalidJson', 'Invalid JSON format'))
                }
              }}
              rows={4}
              className="w-full rounded border border-gray-300 px-3 py-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-900"
            />
          </div>
        </div>

        <div className="pt-4">
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? t('common.saving', 'Saving...') : t('common.save', 'Save')}
          </Button>
        </div>

        {enabled && provider && (
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-900/20">
            <h4 className="text-sm font-medium text-blue-800 dark:text-blue-300">{t('admin.sso.loginNote', 'SSO Login Flow')}</h4>
            <p className="mt-1 text-xs text-blue-700 dark:text-blue-400">
              {t('admin.sso.redirectUrl', 'Redirect URL for your IdP:')} <code className="rounded bg-blue-100 px-1 dark:bg-blue-900/40">{typeof window !== 'undefined' ? `${window.location.origin}/api/auth/sso/callback` : '/api/auth/sso/callback'}</code>
            </p>
          </div>
        )}
      </div>
    </div>
  )
}