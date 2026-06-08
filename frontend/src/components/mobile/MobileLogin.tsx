'use client'

import { useState } from 'react'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'

interface MobileLoginProps {
  onSSOClick?: () => void
  ssoEnabled?: boolean
}

export function MobileLogin({ onSSOClick, ssoEnabled }: MobileLoginProps) {
  const t = useI18nStore((s) => s.t)
  const login = useAuthStore((s) => s.login)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return
    setLoading(true)
    setError('')
    try {
      await login(username, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.loginFailed', 'Login failed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 px-6 dark:bg-gray-900">
      <div className="w-full max-w-sm">
        <h1 className="mb-8 text-center text-2xl font-bold text-gray-900 dark:text-gray-100">
          {t('auth.signIn', 'Sign In')}
        </h1>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('auth.username', 'Username')}
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              className="w-full rounded-lg border border-gray-300 bg-white px-4 py-3 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              placeholder={t('auth.usernamePlaceholder', 'Enter your username')}
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('auth.password', 'Password')}
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full rounded-lg border border-gray-300 bg-white px-4 py-3 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              placeholder={t('auth.passwordPlaceholder', 'Enter your password')}
            />
          </div>

          {error && (
            <p className="text-sm text-error">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full rounded-lg bg-primary-500 py-3 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50 dark:bg-primary-600 dark:hover:bg-primary-500"
          >
            {loading ? t('common.loading', 'Loading...') : t('auth.signIn', 'Sign In')}
          </button>
        </form>

        {ssoEnabled && onSSOClick && (
          <div className="mt-4">
            <button
              onClick={onSSOClick}
              className="w-full rounded-lg border border-gray-300 bg-white py-3 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              {t('auth.signInWithSSO', 'Sign in with SSO')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}