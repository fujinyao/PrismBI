'use client'

import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

export interface TaskStep {
  key: string
  title: string
  status: 'pending' | 'running' | 'finished' | 'failed'
  detail?: string | null
}

interface TaskProgressProps {
  steps: TaskStep[]
  className?: string
}

const STATUS_CONFIG = {
  pending: { icon: '◯', color: 'text-gray-300 dark:text-gray-600', bg: '' },
  running: { icon: '◎', color: 'text-primary-600 dark:text-primary-400', bg: 'bg-primary-50 dark:bg-primary-900/20' },
  finished: { icon: '✓', color: 'text-green-600 dark:text-green-400', bg: 'bg-green-50 dark:bg-green-900/20' },
  failed: { icon: '✕', color: 'text-red-600 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-900/20' },
}

export function TaskProgress({ steps, className }: TaskProgressProps) {
  const t = useI18nStore((s) => s.t)

  if (!steps.length) return null

  return (
    <div className={cn('space-y-1', className)}>
      {steps.map((step, index) => {
        const config = STATUS_CONFIG[step.status]
        return (
          <div
            key={step.key}
            className={cn(
              'flex items-start gap-2 rounded-md px-3 py-2 text-sm transition-colors',
              config.bg,
            )}
          >
            <span className={cn('mt-0.5 flex-shrink-0 text-sm font-bold', config.color)}>
              {config.icon}
            </span>
            <div className="min-w-0 flex-1">
              <p className={cn(
                'font-medium',
                step.status === 'pending' ? 'text-gray-400 dark:text-gray-500' : 'text-gray-900 dark:text-gray-100',
              )}>
                {step.title}
              </p>
              {step.detail && step.status !== 'pending' && (
                <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 line-clamp-2">{step.detail}</p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}