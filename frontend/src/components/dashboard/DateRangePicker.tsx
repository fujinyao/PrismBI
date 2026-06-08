'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface DateRangePickerProps {
  startDate: string
  endDate: string
  onChange: (start: string, end: string) => void
}

function toDateInputValue(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function applyPreset(days: number | null): { start: string; end: string } {
  const end = new Date()
  if (days === null) {
    const start = new Date(end.getFullYear(), end.getMonth(), 1)
    return { start: toDateInputValue(start), end: toDateInputValue(end) }
  }
  const start = new Date(end)
  start.setDate(start.getDate() - days)
  return { start: toDateInputValue(start), end: toDateInputValue(end) }
}

export function DateRangePicker({ startDate, endDate, onChange }: DateRangePickerProps) {
  const t = useI18nStore((s) => s.t)
  const [activePreset, setActivePreset] = useState<string | null>(null)

  const presets = [
    { id: 'today', label: t('dateRange.today', 'Today'), days: 0 },
    { id: 'last7', label: t('dateRange.last7', 'Last 7 days'), days: 7 },
    { id: 'last30', label: t('dateRange.last30', 'Last 30 days'), days: 30 },
    { id: 'thisMonth', label: t('dateRange.thisMonth', 'This month'), days: null },
  ] as const

  const handlePreset = (id: string, days: number | null) => {
    const { start, end } = applyPreset(days)
    setActivePreset(id)
    onChange(start, end)
  }

  const handleCustom = () => {
    setActivePreset('custom')
  }

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white p-1 dark:border-gray-700 dark:bg-gray-800">
        {presets.map((preset) => (
          <button
            key={preset.id}
            onClick={() => handlePreset(preset.id, preset.days)}
            className={cn(
              'rounded px-2.5 py-1 text-xs font-medium transition-colors',
              activePreset === preset.id
                ? 'bg-primary text-white'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700',
            )}
          >
            {preset.label}
          </button>
        ))}
        <button
          onClick={handleCustom}
          className={cn(
            'rounded px-2.5 py-1 text-xs font-medium transition-colors',
            activePreset === 'custom'
              ? 'bg-primary text-white'
              : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700',
          )}
        >
          {t('dateRange.custom', 'Custom')}
        </button>
      </div>

      <div className="flex items-center gap-2">
        <input
          type="date"
          value={startDate}
          onChange={(e) => {
            setActivePreset('custom')
            onChange(e.target.value, endDate)
          }}
          className="block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
        />
        <span className="text-gray-400">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
          </svg>
        </span>
        <input
          type="date"
          value={endDate}
          onChange={(e) => {
            setActivePreset('custom')
            onChange(startDate, e.target.value)
          }}
          className="block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
        />
      </div>
    </div>
  )
}
