'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface Issue {
  type: 'error' | 'warning'
  message: string
  location?: string
}

interface ValidationPanelProps {
  issues: Issue[]
  onClear: () => void
  onNavigate?: (location: string) => void
}

export function ValidationPanel({ issues, onClear, onNavigate }: ValidationPanelProps) {
  const t = useI18nStore((s) => s.t)
  const [activeTab, setActiveTab] = useState<string>('all')

  const tabs = [
    { key: 'all', label: t('modeling.validation.all', 'All') },
    { key: 'error', label: t('modeling.validation.errors', 'Errors') },
    { key: 'warning', label: t('modeling.validation.warnings', 'Warnings') },
  ] as const

  const filteredIssues =
    activeTab === 'all' ? issues : issues.filter((i) => i.type === activeTab)

  const errorCount = issues.filter((i) => i.type === 'error').length
  const warningCount = issues.filter((i) => i.type === 'warning').length

  return (
    <div className="flex flex-col border-t border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2 dark:border-gray-700">
        <div className="flex items-center gap-1">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                'rounded px-2.5 py-1 text-xs font-medium transition-colors',
                activeTab === tab.key
                  ? 'bg-primary text-white'
                  : 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700',
              )}
            >
              {tab.label}
              {tab.key === 'all' && issues.length > 0 && (
                <span className="ml-1 rounded-full bg-gray-200 px-1.5 text-xs dark:bg-gray-600">
                  {issues.length}
                </span>
              )}
              {tab.key === 'error' && errorCount > 0 && (
                <span className="ml-1 rounded-full bg-error-100 px-1.5 text-xs text-error-700 dark:bg-error-900/30 dark:text-error-400">
                  {errorCount}
                </span>
              )}
              {tab.key === 'warning' && warningCount > 0 && (
                <span className="ml-1 rounded-full bg-warning-100 px-1.5 text-xs text-warning-700 dark:bg-warning-900/30 dark:text-warning-400">
                  {warningCount}
                </span>
              )}
            </button>
          ))}
        </div>

        {issues.length > 0 && (
          <Button variant="ghost" size="sm" onClick={onClear}>
            {t('modeling.validation.clearAll', 'Clear all')}
          </Button>
        )}
      </div>

      <div className="max-h-48 overflow-y-auto">
        {filteredIssues.length === 0 ? (
          <div className="flex items-center justify-center py-6">
            <svg className="mr-2 h-5 w-5 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            <span className="text-sm text-gray-500 dark:text-gray-400">{t('modeling.validation.noIssues', 'No issues found')}</span>
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700">
            {filteredIssues.map((issue, i) => (
              <div key={i} className="flex items-start gap-3 px-4 py-2.5">
                {issue.type === 'error' ? (
                  <svg className="mt-0.5 h-4 w-4 shrink-0 text-error" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"
                    />
                  </svg>
                ) : (
                  <svg className="mt-0.5 h-4 w-4 shrink-0 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                    />
                  </svg>
                )}
                <div className="flex-1 min-w-0">
                  <p
                    className={cn(
                      'text-sm',
                      issue.type === 'error'
                        ? 'text-error-700 dark:text-error-400'
                        : 'text-warning-700 dark:text-warning-400',
                    )}
                  >
                    {issue.message}
                  </p>
                  {issue.location && (
                    <button
                      onClick={() => onNavigate?.(issue.location!)}
                      className="mt-0.5 text-xs text-primary hover:underline"
                    >
                      {issue.location}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
