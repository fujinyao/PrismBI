'use client'

import { useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { authApi } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'

export function useAuth() {
  const store = useAuthStore()
  const { isAuthenticated, token, user, logout: storeLogout, fetchMe, setSession } = store

  const meQuery = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => authApi.me(),
    enabled: isAuthenticated && !!token,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })

  useEffect(() => {
    if (meQuery.data && !user) {
      const meData = meQuery.data
      useAuthStore.setState({
        user: meData,
        permissions: meData.permissions ?? [],
        isAuthenticated: true,
      })
    }
  }, [meQuery.data, user])

  const loginMutation = useMutation({
    mutationFn: (params: { username: string; password: string }) =>
      authApi.login(params.username, params.password),
    onSuccess: (data) => {
      setSession(data.token, data.user)
    },
  })

  const refreshMutation = useMutation({
    mutationFn: () => authApi.refresh(),
    onSuccess: async (data) => {
      useAuthStore.setState({ token: data.token, isAuthenticated: true })
      await fetchMe()
    },
  })

  return {
    user,
    token,
    isAuthenticated,
    isLoading: meQuery.isLoading,
    login: loginMutation.mutateAsync,
    loginError: loginMutation.error,
    isLoggingIn: loginMutation.isPending,
    logout: storeLogout,
    refresh: refreshMutation.mutateAsync,
  }
}
