'use client'

import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface ChartTypeSelectorProps {
  value: string
  onChange: (type: string) => void
  disabled?: boolean
}

const CHART_TYPES = [
  {
    id: 'bar',
    label: 'Bar',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5">
        <rect x="3" y="14" width="4" height="7" rx="1" />
        <rect x="10" y="10" width="4" height="11" rx="1" />
        <rect x="17" y="6" width="4" height="15" rx="1" />
      </svg>
    ),
  },
  {
    id: 'line',
    label: 'Line',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5">
        <polyline points="3,18 8,12 13,15 21,6" />
      </svg>
    ),
  },
  {
    id: 'pie',
    label: 'Pie',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z" />
        <path d="M12 12l-4-4a5.99 5.99 0 00-1.79 4.24A5.99 5.99 0 0012 18V12z" />
      </svg>
    ),
  },
  {
    id: 'scatter',
    label: 'Scatter',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5">
        <circle cx="6" cy="6" r="2" />
        <circle cx="18" cy="8" r="2" />
        <circle cx="10" cy="16" r="2" />
        <circle cx="16" cy="18" r="2" />
        <circle cx="14" cy="10" r="1.5" />
        <circle cx="7" cy="15" r="1.5" />
      </svg>
    ),
  },
  {
    id: 'area',
    label: 'Area',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5">
        <path d="M3 18l5-8 6 4 7-9v13H3z" />
      </svg>
    ),
  },
  {
    id: 'heatmap',
    label: 'Heatmap',
    icon: (
      <svg viewBox="0 0 24 24" className="h-5 w-5">
        <rect x="2" y="2" width="5" height="5" rx="1" fill="#1677ff" />
        <rect x="9" y="2" width="5" height="5" rx="1" fill="#4096ff" />
        <rect x="16" y="2" width="5" height="5" rx="1" fill="#91caff" />
        <rect x="2" y="9" width="5" height="5" rx="1" fill="#4096ff" />
        <rect x="9" y="9" width="5" height="5" rx="1" fill="#91caff" />
        <rect x="16" y="9" width="5" height="5" rx="1" fill="#bae0ff" />
        <rect x="2" y="16" width="5" height="5" rx="1" fill="#91caff" />
        <rect x="9" y="16" width="5" height="5" rx="1" fill="#bae0ff" />
        <rect x="16" y="16" width="5" height="5" rx="1" fill="#e6f4ff" />
      </svg>
    ),
  },
]

export function ChartTypeSelector({ value, onChange, disabled }: ChartTypeSelectorProps) {
  const t = useI18nStore((s) => s.t)
  return (
    <div className="grid grid-cols-3 gap-2">
      {CHART_TYPES.map((chart) => {
        const isSelected = value === chart.id
        return (
          <button
            key={chart.id}
            onClick={() => onChange(chart.id)}
            disabled={disabled}
            className={cn(
              'flex flex-col items-center gap-1 rounded-lg border p-3 transition-all',
              isSelected
                ? 'border-primary bg-primary-50 text-primary dark:bg-primary-900/30'
                : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-400 dark:hover:border-gray-600 dark:hover:bg-gray-800',
              disabled && 'cursor-not-allowed opacity-50',
            )}
          >
            {chart.icon}
            <span className="text-xs font-medium">{t('chart.' + chart.id, chart.label)}</span>
          </button>
        )
      })}
    </div>
  )
}
