'use client'

import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { profileApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { Tabs } from '@/components/ui/Tabs'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useToast } from '@/components/ui/Toast'

export default function ProfilePage() {
  const t = useI18nStore((s) => s.t)

  const TABS = [
    { key: 'profile', label: t('profile.title', 'Profile') },
    { key: 'password', label: t('profile.password', 'Password') },
    { key: 'tokens', label: t('profile.apiTokens', 'API Tokens') },
  ]

  const [activeTab, setActiveTab] = useState('profile')
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const queryClient = useQueryClient()

  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [initialized, setInitialized] = useState(false)

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const [showCreateToken, setShowCreateToken] = useState(false)
  const [tokenName, setTokenName] = useState('')
  const [tokenExpiry, setTokenExpiry] = useState('')
  const [createdToken, setCreatedToken] = useState<string | null>(null)

  const {
    data: profile,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['profile'],
    queryFn: () => profileApi.get(),
  })

  const {
    data: tokens,
    isLoading: tokensLoading,
    refetch: refetchTokens,
  } = useQuery({
    queryKey: ['profile-tokens'],
    queryFn: () => profileApi.tokens.list(),
    enabled: activeTab === 'tokens',
  })

  const prof = profile as any

  useEffect(() => {
    if (!initialized && prof) {
      setDisplayName(prof.display_name ?? prof.displayName ?? '')
      setEmail(prof.email ?? '')
      setInitialized(true)
    }
  }, [initialized, prof])

  const updateMutation = useMutation({
    mutationFn: (data: { display_name?: string; email?: string }) =>
      profileApi.update(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profile'] })
      toast(t('profile.updated', 'Profile updated'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('profile.failedToUpdate', 'Failed to update profile'), 'error'),
  })

  const passwordMutation = useMutation({
    mutationFn: (data: { current_password: string; new_password: string }) =>
      profileApi.changePassword(data.current_password, data.new_password),
    onSuccess: () => {
      toast(t('profile.passwordChanged', 'Password changed'), 'success')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('profile.failedToChangePassword', 'Failed to change password'), 'error'),
  })

  const createTokenMutation = useMutation({
    mutationFn: (data: { name: string; expires_at?: string }) =>
      profileApi.tokens.create(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['profile-tokens'] })
      setCreatedToken((result as any).token)
      toast(t('profile.tokenCreated', 'API token created'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('profile.failedToCreateToken', 'Failed to create token'), 'error'),
  })

  const revokeTokenMutation = useMutation({
    mutationFn: (id: number) => profileApi.tokens.revoke(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profile-tokens'] })
      toast(t('profile.tokenRevoked', 'Token revoked'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('profile.failedToRevokeToken', 'Failed to revoke token'), 'error'),
  })

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <Skeleton className="mb-3 h-12 w-full" />
        <Skeleton className="mb-3 h-12 w-full" />
        <Skeleton className="mb-3 h-12 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('profile.failedToLoad', 'Failed to load profile')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <Tabs tabs={TABS} activeKey={activeTab} onChange={setActiveTab} />

      <div className="mt-6">
        {activeTab === 'profile' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-500">{t('profile.username', 'Username')}</label>
              <p className="mt-1">{prof?.username}</p>
            </div>
            <Input
              label={t('profile.displayName', 'Display Name')}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
            <Input
              label={t('profile.email', 'Email')}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <div className="flex justify-end">
              <Button
                onClick={() => updateMutation.mutate({ display_name: displayName, email })}
                loading={updateMutation.isPending}
              >
                {t('profile.save', 'Save Profile')}
              </Button>
            </div>
          </div>
        )}
        {activeTab === 'password' && (
          <div className="space-y-4">
            <Input
              label={t('profile.currentPassword', 'Current Password')}
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
            <Input
              label={t('profile.newPassword', 'New Password')}
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
            <Input
              label={t('profile.confirmPassword', 'Confirm New Password')}
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
            <div className="flex justify-end">
              <Button
                onClick={() => {
                  if (newPassword !== confirmPassword) {
                    toast(t('error.passwordMismatch', 'Passwords do not match'), 'warning')
                    return
                  }
                  if (!currentPassword || !newPassword) {
                    toast(t('profile.pleaseFillFields', 'Please fill in all fields'), 'warning')
                    return
                  }
                  passwordMutation.mutate({ current_password: currentPassword, new_password: newPassword })
                }}
                loading={passwordMutation.isPending}
              >
                {t('profile.changePassword', 'Change Password')}
              </Button>
            </div>
          </div>
        )}
        {activeTab === 'tokens' && (
          <div>
            <div className="mb-4 flex justify-end">
              <Button onClick={() => { setShowCreateToken(true); setTokenName(''); setTokenExpiry(''); setCreatedToken(null) }}>
                {t('profile.createToken', 'Create Token')}
              </Button>
            </div>
            {tokensLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : tokens && (tokens as any[]).length > 0 ? (
              <div className="space-y-2">
                {(tokens as any[]).map((token: any) => (
                  <div key={token.id} className="flex items-center justify-between rounded border border-gray-200 p-3">
                    <div>
                      <p className="font-medium">{token.name}</p>
                      <p className="text-xs text-gray-500">
                        {token.last_used_at ? `${t('profile.lastUsed', 'Last used')}: ${token.last_used_at}` : t('profile.neverUsed', 'Never used')}
                      </p>
                    </div>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => {
                        if (confirm(t('profile.revokeConfirm', 'Revoke this token?'))) revokeTokenMutation.mutate(token.id)
                      }}
                    >
                      {t('profile.revokeToken', 'Revoke')}
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">{t('profile.noTokens', 'No API tokens yet.')}</p>
            )}

            <Modal open={showCreateToken} onClose={() => setShowCreateToken(false)} title={t('profile.createApiToken', 'Create API Token')}>
              <div className="space-y-4">
                {createdToken ? (
                  <div>
                    <p className="mb-2 text-sm font-medium">{t('profile.tokenCreatedMessage', 'Token created. Copy it now - it won\'t be shown again.')}</p>
                    <div className="rounded border border-gray-200 bg-gray-50 p-3 font-mono text-xs break-all">
                      {createdToken}
                    </div>
                    <div className="mt-4 flex justify-end">
                      <Button onClick={() => { setShowCreateToken(false); setCreatedToken(null) }}>{t('common.done', 'Done')}</Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <Input
                      label={t('profile.tokenName', 'Token Name')}
                      value={tokenName}
                      onChange={(e) => setTokenName(e.target.value)}
                      placeholder={t('profile.tokenNamePlaceholder', 'My API Token')}
                    />
                    <Input
                      label={t('profile.tokenExpiry', 'Expires At (optional)')}
                      type="date"
                      value={tokenExpiry}
                      onChange={(e) => setTokenExpiry(e.target.value)}
                    />
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" onClick={() => setShowCreateToken(false)}>{t('common.cancel', 'Cancel')}</Button>
                      <Button
                        onClick={() => {
                          if (!tokenName.trim()) {
                            toast(t('profile.pleaseEnterTokenName', 'Please enter a token name'), 'warning')
                            return
                          }
                          createTokenMutation.mutate({
                            name: tokenName,
                            expires_at: tokenExpiry || undefined,
                          })
                        }}
                        loading={createTokenMutation.isPending}
                      >
                        {t('common.create', 'Create')}
                      </Button>
                    </div>
                  </>
                )}
              </div>
            </Modal>
          </div>
        )}
      </div>
    </div>
  )
}
