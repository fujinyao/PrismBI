'use client'

import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface TagProps {
  children: React.ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info'
  size?: 'sm' | 'md'
  className?: string
  onClose?: () => void
}

const variantStyles = {
  default: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
  success: 'bg-success-50 text-success-700 dark:bg-success-900/30 dark:text-success-400',
  warning: 'bg-warning-50 text-warning-700 dark:bg-warning-900/30 dark:text-warning-400',
  error: 'bg-error-50 text-error-700 dark:bg-error-900/30 dark:text-error-400',
  info: 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400',
}

const sizeStyles = {
  sm: 'px-1.5 py-0.5 text-xs',
  md: 'px-2.5 py-1 text-sm',
}

export function Tag({ children, variant = 'default', size = 'sm', className, onClose }: TagProps) {
  const t = useI18nStore((s) => s.t)
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full font-medium',
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
    >
      {children}
      {onClose && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            onClose()
          }}
          className="ml-0.5 hover:opacity-70"
          aria-label={t('common.remove', 'Remove')}
        >
          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </span>
  )
}
