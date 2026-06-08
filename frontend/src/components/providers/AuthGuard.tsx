'use client'

import { useEffect, useRef, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { useAuthStore } from '@/stores/authStore'
import { AppShell } from '@/components/layouts/AppShell'
import { ErrorBoundary } from '@/components/ui/ErrorBoundary'
import { useRouteFocus } from '@/hooks/useRouteFocus'

const publicPaths = ['/login']
const noShellPaths = ['/setup']

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const token = useAuthStore((s) => s.token)
  const fetchMe = useAuthStore((s) => s.fetchMe)
  const [verifying, setVerifying] = useState(true)
  const [hydrated, setHydrated] = useState(false)
  const lastFetchRef = useRef(0)

  useRouteFocus()

  useEffect(() => {
    const unsub = useAuthStore.persist.onFinishHydration(() => {
      setHydrated(true)
    })
    if (useAuthStore.persist.hasHydrated()) {
      setHydrated(true)
    }
    return () => { unsub() }
  }, [])

  useEffect(() => {
    if (!hydrated) return
    if (publicPaths.includes(pathname)) {
      setVerifying(false)
      return
    }
    if (!token) {
      setVerifying(false)
      return
    }
    const now = Date.now()
    if (now - lastFetchRef.current < 30000) {
      setVerifying(false)
      return
    }
    lastFetchRef.current = now
    let cancelled = false
    setVerifying(true)
    fetchMe()
      .then(() => {
        if (!cancelled) setVerifying(false)
      })
      .catch((err) => {
        if (!cancelled) setVerifying(false)
        if (err && typeof err === 'object' && 'code' in err && (err as { code: string }).code === 'UNAUTHORIZED') {
          useAuthStore.getState().logout()
        }
      })
    return () => {
      cancelled = true
    }
  }, [token, fetchMe, hydrated])

  useEffect(() => {
    if (!hydrated) return
    if (publicPaths.includes(pathname)) return
    if (!token && typeof window !== 'undefined') {
      router.replace('/login')
    }
  }, [pathname, token, router, hydrated])

  if (!hydrated) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-white dark:bg-gray-900">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-primary" />
      </div>
    )
  }

  if (!publicPaths.includes(pathname) && !token) {
    return null
  }

  if (!publicPaths.includes(pathname) && verifying) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-white dark:bg-gray-900">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-primary" />
      </div>
    )
  }

  if (publicPaths.includes(pathname) || noShellPaths.some((p) => pathname.startsWith(p))) {
    return <ErrorBoundary>{children}</ErrorBoundary>
  }

  return <AppShell><ErrorBoundary>{children}</ErrorBoundary></AppShell>
}
