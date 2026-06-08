'use client'

import { useEffect, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'
import { useToast } from '@/components/ui/Toast'
import { useQueryClient } from '@tanstack/react-query'
import { settingsApi } from '@/lib/api'

function TagEditor({ items, setItems, placeholder }: { items: string[]; setItems: (v: string[]) => void; placeholder: string }) {
  const [newVal, setNewVal] = useState('')
  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {items.map((item) => (
          <span key={item} className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-800 dark:bg-gray-700 dark:text-gray-200">
            {item}
            <button type="button" onClick={() => setItems(items.filter((i) => i !== item))} className="text-gray-400 hover:text-red-500">&times;</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <Input value={newVal} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewVal(e.target.value)} placeholder={placeholder} onKeyDown={(e: React.KeyboardEvent) => { if (e.key === 'Enter') { e.preventDefault(); const v = newVal.trim(); if (v && !items.includes(v)) { setItems([...items, v]); setNewVal('') } } }} />
        <Button type="button" variant="secondary" onClick={() => { const v = newVal.trim(); if (v && !items.includes(v)) { setItems([...items, v]); setNewVal('') } }} disabled={!newVal.trim()}>Add</Button>
      </div>
    </div>
  )
}

interface SecuritySettingsProps {
  settings: any
  canSave?: boolean
}

export function SecuritySettings({ settings, canSave = true }: SecuritySettingsProps) {
  const t = useI18nStore((s) => s.t)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [sqlKeywords, setSqlKeywords] = useState<string[]>([])
  const [duckdbFunctions, setDuckdbFunctions] = useState<string[]>([])
  const [operators, setOperators] = useState<string[]>([])
  const [accessTypes, setAccessTypes] = useState<string[]>([])
  const [rateWindow, setRateWindow] = useState(300)
  const [rateMax, setRateMax] = useState(10)
  const [rateEntries, setRateEntries] = useState(10000)
  const [wsTicketTtl, setWsTicketTtl] = useState(30)
  const [jwtExpiry, setJwtExpiry] = useState(24)
  const [ssoStateTtl, setSsoStateTtl] = useState(600)
  const [oidcCacheTtl, setOidcCacheTtl] = useState(3600)
  const [maxSessionDays, setMaxSessionDays] = useState(30)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    settingsApi.securitySettings().then((data: any) => {
      const d = data.data || data
      setSqlKeywords(d.sql_forbidden_keywords || [])
      setDuckdbFunctions(d.forbidden_duckdb_functions || [])
      setOperators(d.allowed_operators || [])
      setAccessTypes(d.allowed_access_types || [])
      setRateWindow(d.rate_limit_window_s ?? 300)
      setRateMax(d.rate_limit_max ?? 10)
      setRateEntries(d.rate_limit_max_entries ?? 10000)
      setWsTicketTtl(d.ws_ticket_ttl_s ?? 30)
      setJwtExpiry(d.jwt_expiry_hours ?? 24)
      setSsoStateTtl(d.sso_state_ttl_s ?? 600)
      setOidcCacheTtl(d.oidc_cache_ttl_s ?? 3600)
      setMaxSessionDays(d.max_session_days ?? 30)
    }).catch((err) => {
      console.error('Failed to load security settings:', err)
      toast(err instanceof Error ? err.message : 'Failed to load security settings', 'error')
    })
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await settingsApi.securitySettingsUpdate({
        sql_forbidden_keywords: sqlKeywords,
        forbidden_duckdb_functions: duckdbFunctions,
        allowed_operators: operators,
        allowed_access_types: accessTypes,
        rate_limit_window_s: rateWindow,
        rate_limit_max: rateMax,
        rate_limit_max_entries: rateEntries,
        ws_ticket_ttl_s: wsTicketTtl,
        jwt_expiry_hours: jwtExpiry,
        sso_state_ttl_s: ssoStateTtl,
        oidc_cache_ttl_s: oidcCacheTtl,
        max_session_days: maxSessionDays,
      })
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      toast(t('toast.securitySaved', 'Security settings saved'), 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('toast.securitySaveFailed', 'Failed to save security settings'), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle>{t('settings.security.sqlGuard', 'SQL Guard')}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('settings.security.forbiddenKeywords', 'Forbidden SQL Keywords')}</label>
            <TagEditor items={sqlKeywords} setItems={setSqlKeywords} placeholder="e.g. alter" />
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('settings.security.forbiddenDuckDBFunctions', 'Forbidden DuckDB Functions')}</label>
            <TagEditor items={duckdbFunctions} setItems={setDuckdbFunctions} placeholder="e.g. read_csv" />
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('settings.security.allowedOperators', 'Allowed RLS Operators')}</label>
            <TagEditor items={operators} setItems={setOperators} placeholder="e.g. !=" />
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">{t('settings.security.allowedAccessTypes', 'Allowed CLS Access Types')}</label>
            <TagEditor items={accessTypes} setItems={setAccessTypes} placeholder="e.g. MASK" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t('settings.security.rateLimitAuth', 'Rate Limit & Auth')}</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Input label={t('settings.security.rateLimitWindow', 'Rate Limit Window (s)')} type="number" value={rateWindow} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setRateWindow(Number.isNaN(v) ? 300 : v) }} min={10} />
          <Input label={t('settings.security.rateLimitMax', 'Max Attempts per Window')} type="number" value={rateMax} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setRateMax(Number.isNaN(v) ? 10 : v) }} min={1} />
          <Input label={t('settings.security.rateLimitEntries', 'Max Stored IPs')} type="number" value={rateEntries} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setRateEntries(Number.isNaN(v) ? 10000 : v) }} min={100} />
          <Input label={t('settings.security.wsTicketTtl', 'WS Ticket TTL (s)')} type="number" value={wsTicketTtl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setWsTicketTtl(Number.isNaN(v) ? 30 : v) }} min={5} />
          <Input label={t('settings.security.jwtExpiry', 'JWT Expiry (hours)')} type="number" value={jwtExpiry} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setJwtExpiry(Number.isNaN(v) ? 24 : v) }} min={1} />
          <Input label={t('settings.security.maxSessionDays', 'Max Session (days)')} type="number" value={maxSessionDays} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setMaxSessionDays(Number.isNaN(v) ? 30 : v) }} min={1} />
          <Input label={t('settings.security.ssoStateTtl', 'SSO State TTL (s)')} type="number" value={ssoStateTtl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setSsoStateTtl(Number.isNaN(v) ? 600 : v) }} min={60} />
          <Input label={t('settings.security.oidcCacheTtl', 'OIDC Cache TTL (s)')} type="number" value={oidcCacheTtl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => { const v = parseInt(e.target.value); setOidcCacheTtl(Number.isNaN(v) ? 3600 : v) }} min={60} />
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button type="button" onClick={handleSave} loading={saving} disabled={!canSave}>{t('settings.security.save', 'Save Security Settings')}</Button>
      </div>
    </div>
  )
}
