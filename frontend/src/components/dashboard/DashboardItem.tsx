'use client'

import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface DashboardItemProps {
  item: any
  isEditable?: boolean
  onRemove?: (id: string) => void
  children: React.ReactNode
}

export function DashboardItem({ item, isEditable, onRemove, children }: DashboardItemProps) {
  const t = useI18nStore((s) => s.t)
  return (
    <div
      className={cn('flex h-full flex-col')}
      data-grid={{
        x: item.x ?? 0,
        y: item.y ?? 0,
        w: item.w ?? 4,
        h: item.h ?? 3,
        minW: item.minW ?? 2,
        minH: item.minH ?? 2,
      }}
    >
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-700 px-4 py-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <div className="dashboard-drag-handle cursor-grab active:cursor-grabbing text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 6h2v2H8V6zm6 0h2v2h-2V6zM8 11h2v2H8v-2zm6 0h2v2h-2v-2zm-6 5h2v2H8v-2zm6 0h2v2h-2v-2z" />
            </svg>
          </div>
          <h3 className="truncate text-sm font-medium text-gray-700 dark:text-gray-300">
            {item.title ?? t('dashboardItem.widget', 'Widget')}
          </h3>
        </div>
        {isEditable && onRemove && (
          <button
            onClick={() => onRemove(item.id)}
            className="ml-2 text-gray-400 hover:text-error transition-colors"
            aria-label={t('dashboardItem.remove', 'Remove widget')}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>
      <div className="flex-1 overflow-auto p-4">
        {children}
      </div>
    </div>
  )
}
