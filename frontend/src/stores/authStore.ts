import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { authApi, type Permission, type User, ApiClientError } from '@/lib/api'
import { clearAppQueryCache } from '@/lib/queryClientEvents'

interface AuthState {
  user: User | null
  token: string | null
  permissions: Permission[]
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<{ isFirstLogin: boolean }>
  logout: () => void
  refresh: () => Promise<void>
  fetchMe: () => Promise<void>
  setSession: (token: string, user: User) => void
  setPermissions: (permissions: Permission[]) => void
  hasPermission: (resource: string, action: string) => boolean
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      permissions: [],
      isAuthenticated: false,

      login: async (username: string, password: string) => {
        const res = await authApi.login(username, password)
        if (typeof window !== 'undefined') {
          localStorage.removeItem('auth_token')
        }
        set({
          token: res.token,
          user: res.user,
          permissions: res.user.permissions ?? [],
          isAuthenticated: true,
        })
        return { isFirstLogin: Boolean(res.is_first_login) }
      },

      logout: () => {
        set({
          user: null,
          token: null,
          permissions: [],
          isAuthenticated: false,
        })
        if (typeof window !== 'undefined') {
          localStorage.removeItem('auth_token')
          localStorage.removeItem('auth_user')
          localStorage.removeItem('auth-store')
        }
        clearAppQueryCache()
      },

      refresh: async () => {
        try {
          const res = await authApi.refresh()
          const prevUser = get().user
          if (typeof window !== 'undefined') {
            localStorage.removeItem('auth_token')
          }
          set({ token: res.token, isAuthenticated: true, user: prevUser })
          try {
            await get().fetchMe()
          } catch {
            // fetchMe failed but token is still valid — keep previous user data
          }
        } catch {
          get().logout()
        }
      },

      fetchMe: async () => {
        try {
          const user = await authApi.me()
          set({ user, permissions: user.permissions ?? [], isAuthenticated: true })
        } catch (e) {
          if (e instanceof ApiClientError && (e.code === '401' || e.code === 'UNAUTHORIZED')) {
            get().logout()
          }
        }
      },

      setPermissions: (permissions: Permission[]) => {
        set({ permissions })
      },

      setSession: (token: string, user: User) => {
        if (typeof window !== 'undefined') {
          localStorage.removeItem('auth_token')
        }
        set({ token, user, permissions: user.permissions ?? [], isAuthenticated: true })
      },

      hasPermission: (resource: string, action: string) => {
        const { permissions } = get()
        return permissions.some(
          (p) =>
            (p.resource === resource && (p.action === action || p.action === 'manage')) ||
            (p.resource === 'admin' && (p.action === action || p.action === 'manage')),
        )
      },
    }),
    {
      name: 'auth-store',
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        permissions: state.permissions,
      }),
      onRehydrateStorage: () => (state) => {
        if (typeof window !== 'undefined') {
          localStorage.removeItem('auth_token')
        }
      },
    },
  ),
)
