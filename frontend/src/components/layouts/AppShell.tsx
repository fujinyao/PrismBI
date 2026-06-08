'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { CommandPalette } from '@/components/ui/CommandPalette'
import { MobileLayout } from '@/components/mobile/MobileLayout'
import { useIsMobile } from '@/hooks/useMediaQuery'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const isMobile = useIsMobile()
  const router = useRouter()

  const handleMobileRefresh = useCallback(async () => {
    router.refresh()
  }, [router])

  useEffect(() => {
    if (isMobile) {
      setSidebarCollapsed(true)
    }
  }, [isMobile])

  if (isMobile) {
    return (
      <MobileLayout onRefresh={handleMobileRefresh}>
        {children}
      </MobileLayout>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50 dark:bg-gray-950">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
      />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main id="main-content" className="flex-1 overflow-y-auto p-1.5" tabIndex={-1}>
          {children}
        </main>
      </div>
      <CommandPalette />
    </div>
  )
}