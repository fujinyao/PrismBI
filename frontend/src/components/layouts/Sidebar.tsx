'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { BrandLogo } from '@/components/brand/BrandLogo'
import { useBrandingStore } from '@/stores/brandingStore'

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}

function Icons({ name }: { name: string }) {
  const cls = 'h-5 w-5 shrink-0'
  switch (name) {
    case '/home':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" /></svg>
    case '/home/dashboard':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" /></svg>
    case '/modeling':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" /></svg>
    case '/knowledge/instructions':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>
    case '/api-management/history':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg>
    case '/settings':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
    case '/admin/users':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
    case '/admin/backup':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
    case '/admin/security-policies':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-2.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
    case '/admin/sso':
      return <svg className={cls} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" /></svg>
    default:
      return null
  }
}

const navKeyMap: Record<string, string> = {
  '/home': 'nav.home',
  '/home/dashboard': 'nav.dashboard',
  '/modeling': 'nav.modeling',
  '/knowledge/instructions': 'nav.knowledge',
  '/api-management/history': 'nav.apiManagement.history',
}

const bottomKeyMap: Record<string, string> = {
  '/settings': 'nav.settings',
  '/admin/users': 'nav.admin',
  '/admin/security-policies': 'nav.admin.security',
  '/admin/sso': 'nav.admin.sso',
  '/admin/backup': 'nav.admin.backup',
}

function isActivePath(pathname: string, href: string) {
  if (href === '/home') return pathname === '/home' || /^\/home\/(?:\d+|temp-)/.test(pathname)
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname()
  const t = useI18nStore((s) => s.t)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const appName = useBrandingStore((s) => s.appName)
  const appDescription = useBrandingStore((s) => s.appDescription)
  const bottomEntries = Object.entries(bottomKeyMap).filter(([href]) => {
    if (href.startsWith('/admin')) return hasPermission('admin', 'read') || hasPermission('admin', 'manage')
    return true
  })

  return (
    <aside
      className={cn(
        'flex flex-col border-r border-gray-200 bg-white transition-all dark:border-gray-700 dark:bg-gray-900',
        collapsed ? 'w-16' : 'w-64',
      )}
    >
      <div className={cn('flex h-14 items-center border-b border-gray-200 dark:border-gray-700', collapsed ? 'justify-center px-0' : 'px-3')}>
        {!collapsed && (
          <div className="flex min-w-0 flex-1 items-center gap-2.5 overflow-hidden">
            <BrandLogo className="h-9 w-9 shrink-0" />
            <div className="min-w-0 leading-tight">
              <p className="truncate text-base font-bold text-gray-900 dark:text-gray-100">{appName}</p>
              <p className="truncate text-xs text-gray-500 dark:text-gray-400">{appDescription}</p>
            </div>
          </div>
        )}
        {collapsed ? (
          <button
            onClick={onToggle}
            className="group flex h-10 w-10 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            aria-label={t('common.toggleSidebar', 'Toggle sidebar')}
          >
            <BrandLogo className="h-8 w-8 group-hover:hidden" />
            <svg className="hidden h-5 w-5 rotate-180 group-hover:block" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        ) : (
          <button
            onClick={onToggle}
            className="ml-auto rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            aria-label={t('common.toggleSidebar', 'Toggle sidebar')}
          >
            <svg className="h-5 w-5 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label={t('common.mainNavigation', 'Main navigation')}>
        {Object.entries(navKeyMap).map(([href, key]) => {
          const isActive = isActivePath(pathname, href)
          const label = t(key)
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'mb-1 flex items-center rounded-lg text-sm transition-colors',
                collapsed ? 'h-10 justify-center px-0' : 'gap-3 px-4 py-2.5',
                isActive
                  ? 'bg-primary-50 font-medium text-primary dark:bg-primary-900/20 dark:text-primary-400'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200',
              )}
              title={collapsed ? label : undefined}
            >
              <Icons name={href} />
              {!collapsed && <span>{label}</span>}
            </Link>
          )
        })}
      </nav>

      <div className="border-t border-gray-200 px-3 py-3 dark:border-gray-700">
        {bottomEntries.map(([href, key]) => {
          const isActive = isActivePath(pathname, href)
          const label = t(key)
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'mb-1 flex items-center rounded-lg text-sm transition-colors',
                collapsed ? 'h-10 justify-center px-0' : 'gap-3 px-4 py-2.5',
                isActive
                  ? 'bg-primary-50 font-medium text-primary dark:bg-primary-900/20 dark:text-primary-400'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200',
              )}
              title={collapsed ? label : undefined}
            >
              <Icons name={href} />
              {!collapsed && <span>{label}</span>}
            </Link>
          )
        })}
      </div>
    </aside>
  )
}
