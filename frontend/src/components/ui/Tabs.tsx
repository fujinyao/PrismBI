'use client'

import { cn } from '@/lib/utils'

interface Tab {
  key: string
  label: string
  badge?: number
  disabled?: boolean
}

interface TabsProps {
  tabs: Tab[]
  activeKey: string
  onChange: (key: string) => void
  className?: string
}

export function Tabs({ tabs, activeKey, onChange, className }: TabsProps) {
  return (
    <div className={cn('flex border-b border-gray-200 dark:border-gray-700', className)}>
      {tabs.map((tab) => (
        <button
          key={tab.key}
          disabled={tab.disabled}
          onClick={() => onChange(tab.key)}
          className={cn(
            'relative px-4 py-2.5 text-sm font-medium transition-colors',
            tab.disabled
              ? 'cursor-not-allowed text-gray-300 dark:text-gray-600'
              : activeKey === tab.key
                ? 'text-primary after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
          )}
        >
          <span className="inline-flex items-center gap-1.5">
            {tab.label}
            {tab.badge !== undefined && (
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary-100 px-1.5 text-xs text-primary-700">
                {tab.badge}
              </span>
            )}
          </span>
        </button>
      ))}
    </div>
  )
}
