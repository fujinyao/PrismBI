'use client'

import { useQuery } from '@tanstack/react-query'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'

export function MobileProfile() {
  const t = useI18nStore((s) => s.t)
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  const { data: tokens } = useQuery({
    queryKey: ['api-tokens'],
    queryFn: async () => {
      const res = await fetch('/api/profile/tokens', { headers: { Authorization: `Bearer ${useAuthStore.getState().token}` } })
      return res.json().then((d: Record<string, unknown>) => d.data)
    },
  })

  return (
    <div className="space-y-4 px-4 py-4">
      <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-3 text-base font-semibold text-gray-900 dark:text-gray-100">
          {t('profile.title', 'Profile')}
        </h2>
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-gray-500 dark:text-gray-400">{t('profile.username', 'Username')}</span>
            <span className="text-gray-900 dark:text-gray-100">{user?.username}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-gray-500 dark:text-gray-400">{t('profile.displayName', 'Display Name')}</span>
            <span className="text-gray-900 dark:text-gray-100">{user?.display_name || '—'}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-gray-500 dark:text-gray-400">{t('profile.email', 'Email')}</span>
            <span className="text-gray-900 dark:text-gray-100">{user?.email || '—'}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-gray-500 dark:text-gray-400">{t('profile.role', 'Role')}</span>
            <span className="text-gray-900 dark:text-gray-100">
              {user?.roles?.map((r: { name: string }) => r.name).join(', ') || '—'}
            </span>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-3 text-base font-semibold text-gray-900 dark:text-gray-100">
          {t('profile.apiTokens', 'API Tokens')}
        </h2>
        {Array.isArray(tokens) && tokens.length > 0 ? (
          <div className="space-y-2">
            {tokens.map((token: { id: number; name: string; created_at: string }) => (
              <div key={token.id} className="flex justify-between text-sm">
                <span className="text-gray-900 dark:text-gray-100">{token.name}</span>
                <span className="text-gray-500 dark:text-gray-400">{new Date(token.created_at).toLocaleDateString()}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">{t('profile.noTokens', 'No API tokens')}</p>
        )}
      </div>

      <button
        onClick={logout}
        className="w-full rounded-lg border border-error-200 bg-error-50 py-3 text-sm font-medium text-error-600 hover:bg-error-100 dark:border-error-900/30 dark:bg-error-900/10 dark:text-error-400 dark:hover:bg-error-900/20"
      >
        {t('auth.logout', 'Log out')}
      </button>
    </div>
  )
}