'use client'

import { Suspense, useState, useMemo, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useAuthStore } from '@/stores/authStore'
import { readStoredLocale, useI18nStore } from '@/stores/i18nStore'
import { authApi } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card } from '@/components/ui/Card'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { LOCALES, type LocaleCode } from '@/lib/i18n/locales'
import { BrandLogo } from '@/components/brand/BrandLogo'
import { useBrandingStore } from '@/stores/brandingStore'

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  )
}

function LoginPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const login = useAuthStore((s) => s.login)
  const setSession = useAuthStore((s) => s.setSession)
  const t = useI18nStore((s) => s.t)
  const locale = useI18nStore((s) => s.locale)
  const setLocale = useI18nStore((s) => s.setLocale)
  const appName = useBrandingStore((s) => s.appName)
  const appDescription = useBrandingStore((s) => s.appDescription)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [ssoEnabled, setSsoEnabled] = useState(false)
  const [ssoProvider, setSsoProvider] = useState<string | null>(null)
  const localLocale = readStoredLocale()
  const redirectTo = useMemo(() => {
    const r = searchParams.get('redirect')
    if (r && r.startsWith('/') && !r.startsWith('//')) return r
    return null
  }, [searchParams])

  useEffect(() => {
    if (searchParams.get('sso') !== '1') return
    let cancelled = false
    ;(async () => {
      try {
        const ssoToken = searchParams.get('token')
        if (ssoToken) {
          const resp = await fetch('/api/auth/me', { headers: { Authorization: `Bearer ${ssoToken}` } })
          if (cancelled) return
          if (resp.ok) {
            const data = await resp.json()
            setSession(ssoToken, data.data || data)
            router.replace(redirectTo || '/home')
            return
          }
        }
        try {
          const cookieData = await authApi.ssoCookieToken()
          if (cancelled) return
          if (cookieData.token && cookieData.user) {
            setSession(cookieData.token, cookieData.user)
            router.replace(redirectTo || '/home')
            return
          }
        } catch { /* cookie token not available */ }
        if (!cancelled) setError(t('login.ssoFailed', 'SSO login failed'))
      } catch {
        if (!cancelled) setError(t('login.ssoFailed', 'SSO login failed'))
      }
    })()
    return () => { cancelled = true }
  }, [searchParams, redirectTo, router, setSession, t])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const resp = await fetch('/api/settings/public')
        if (cancelled) return
        if (resp.ok) {
          const data = await resp.json()
          const settings = data.data?.settings || data.settings || {}
          if (settings.sso_enabled) {
            setSsoEnabled(true)
            setSsoProvider(settings.sso_provider || 'oidc')
          }
        }
      } catch { /* SSO check failed, ignore */ }
    })()
    return () => { cancelled = true }
  }, [])

  const handleLanguageChange = (nextLocale: LocaleCode) => {
    setLocale(nextLocale)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const { isFirstLogin } = await login(username, password)
      if (isFirstLogin) setLocale(localLocale || 'en')
      router.replace(redirectTo || '/home')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('login.failed', 'Login failed'))
    } finally {
      setLoading(false)
    }
  }

  const handleSSOLogin = () => {
    window.location.href = '/api/auth/sso/login'
  }

  const showSSO = ssoEnabled

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f5f5f5] p-4">
      <Card className="w-full max-w-md p-8 shadow-lg">
        <div className="mb-6 flex items-start justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <BrandLogo className="h-14 w-14 shrink-0" />
            <div className="min-w-0">
              <h1 className="truncate text-2xl font-bold leading-8">{appName}</h1>
              <p className="truncate text-sm leading-5 text-gray-500">{appDescription}</p>
            </div>
          </div>
          <select
            value={localLocale ?? locale}
            onChange={(e) => handleLanguageChange(e.target.value as LocaleCode)}
            className="shrink-0 self-start rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-400"
          >
            {LOCALES.map((l) => (
              <option key={l.code} value={l.code}>{l.nativeLabel}</option>
            ))}
          </select>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            label={t('login.username', 'Username')}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={t('login.usernamePlaceholder', 'Enter your username')}
            required
          />
          <Input
            label={t('login.password', 'Password')}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('login.passwordPlaceholder', 'Enter your password')}
            required
          />

          {error && <ErrorToast message={error} onClose={() => setError(null)} />}

          <Button type="submit" variant="primary" size="lg" loading={loading} className="mt-4 w-full">
            {t('login.signIn', 'Sign In')}
          </Button>
        </form>

        {showSSO && <div className="mt-6">
          <div className="relative mb-4">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-gray-300" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-white px-2 text-gray-500">{t('login.orContinueWith', 'Or continue with')}</span>
            </div>
          </div>

          <Button variant="secondary" size="lg" onClick={handleSSOLogin} className="w-full">
            {t('login.sso', 'SSO Login')} ({ssoProvider?.toUpperCase() || 'SSO'})
          </Button>
        </div>}
      </Card>
    </div>
  )
}
