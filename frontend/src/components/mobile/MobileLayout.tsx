'use client'

import { useState, useCallback } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { PullToRefresh } from './PullToRefresh'
import { BottomSheet } from './BottomSheet'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface MobileLayoutProps {
  children: React.ReactNode
  onRefresh?: () => Promise<void>
}

const TAB_ROUTES: Record<string, string> = {
  home: '/home',
  qa: '/home',
  dashboard: '/home/dashboard',
  settings: '/settings/profile',
  profile: '/settings/profile',
  knowledge: '/knowledge',
  modeling: '/modeling',
  projects: '/projects',
}

export function MobileLayout({ children, onRefresh }: MobileLayoutProps) {
  const t = useI18nStore((s) => s.t)
  const router = useRouter()
  const pathname = usePathname()
  const [moreOpen, setMoreOpen] = useState(false)

  const tabs = [
    {
      key: 'home',
      label: t('nav.home', 'Home'),
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
        </svg>
      ),
    },
    {
      key: 'dashboard',
      label: t('nav.dashboard', 'Dashboard'),
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm0 8a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zm12 0a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
        </svg>
      ),
    },
    {
      key: 'more',
      label: t('nav.more', 'More'),
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      ),
    },
    {
      key: 'settings',
      label: t('nav.settings', 'Settings'),
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      ),
    },
  ]

  const moreItems = [
    { key: 'knowledge', label: t('nav.knowledge', 'Knowledge'), route: '/knowledge', icon: '📚' },
    { key: 'modeling', label: t('nav.modeling', 'Modeling'), route: '/modeling', icon: '🔗' },
    { key: 'projects', label: t('nav.projects', 'Projects'), route: '/projects', icon: '📁' },
  ]

  const activeTab = pathname?.startsWith('/home/dashboard') ? 'dashboard'
    : pathname?.startsWith('/settings') ? 'settings'
    : pathname?.startsWith('/knowledge') ? 'more'
    : pathname?.startsWith('/modeling') ? 'more'
    : pathname?.startsWith('/projects') ? 'more'
    : 'home'

  const handleTabChange = (tab: string) => {
    if (tab === 'more') {
      setMoreOpen(true)
      return
    }
    const route = TAB_ROUTES[tab] ?? TAB_ROUTES['home'] ?? '/home'
    router.push(route)
  }

  const handleMoreSelect = (route: string) => {
    setMoreOpen(false)
    router.push(route)
  }

  const content = (
    <main id="main-content" className="flex-1 overflow-y-auto pb-16" tabIndex={-1}>
      {children}
    </main>
  )

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col bg-gray-50 dark:bg-gray-900">
      {onRefresh ? (
        <PullToRefresh onRefresh={onRefresh}>{content}</PullToRefresh>
      ) : (
        content
      )}

      <nav className="fixed bottom-0 left-0 right-0 z-50 border-t border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800" aria-label="Mobile navigation">
        <div className="mx-auto flex max-w-md items-center justify-around">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => handleTabChange(tab.key)}
              aria-label={tab.label}
              aria-current={activeTab === tab.key ? 'page' : undefined}
              className={cn(
                'flex flex-1 flex-col items-center gap-0.5 py-2 text-xs font-medium transition-colors',
                activeTab === tab.key
                  ? 'text-primary'
                  : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
              )}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          ))}
        </div>
      </nav>

      <BottomSheet open={moreOpen} onClose={() => setMoreOpen(false)} title={t('nav.more', 'More')}>
        <div className="flex flex-col gap-1">
          {moreItems.map((item) => (
            <button
              key={item.key}
              onClick={() => handleMoreSelect(item.route)}
              className="flex items-center gap-3 rounded-lg px-3 py-3 text-left text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <span className="text-lg">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      </BottomSheet>
    </div>
  )
}