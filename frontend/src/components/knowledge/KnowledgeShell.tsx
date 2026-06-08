'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface KnowledgeShellProps {
  children: React.ReactNode
}

const items = [
  { href: '/knowledge/instructions', labelKey: 'knowledge.nav.instructions', descKey: 'knowledge.nav.instructionsDesc' },
  { href: '/knowledge/question-sql-pairs', labelKey: 'knowledge.nav.sqlPairs', descKey: 'knowledge.nav.sqlPairsDesc' },
]

export function KnowledgeShell({ children }: KnowledgeShellProps) {
  const pathname = usePathname()
  const t = useI18nStore((s) => s.t)

  return (
    <div className="flex min-h-full gap-[5px]">
      <aside className="hidden w-64 shrink-0 rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900 lg:block">
        <div className="px-2 py-1.5">
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t('nav.knowledge', 'Knowledge')}</p>
        </div>
        <nav className="mt-2 space-y-1">
          {items.map((item) => {
            const active = pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'block rounded-lg px-3 py-2.5 transition-colors',
                  active
                    ? 'bg-primary-50 text-primary dark:bg-primary-900/20 dark:text-primary-300'
                    : 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800',
                )}
              >
                <span className="block text-sm font-medium">{t(item.labelKey)}</span>
                <span className="mt-0.5 block text-xs text-gray-400">{t(item.descKey)}</span>
              </Link>
            )
          })}
        </nav>
      </aside>
      <section className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">{children}</section>
    </div>
  )
}
