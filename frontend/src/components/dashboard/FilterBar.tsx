'use client'

import { Button } from '@/components/ui/Button'
import { Tag } from '@/components/ui/Tag'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface Filter {
  id: string
  field: string
  operator: string
  value: string
}

interface FilterBarProps {
  filters: Filter[]
  onRemove: (id: string) => void
  onAdd: () => void
}

export function FilterBar({ filters, onRemove, onAdd }: FilterBarProps) {
  const t = useI18nStore((s) => s.t)
  return (
    <div
      className={cn(
        'flex items-center gap-2 overflow-x-auto rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800',
      )}
    >
      <span className="shrink-0 text-sm font-medium text-gray-500 dark:text-gray-400">{t('filterBar.title', 'Filters')}</span>

      {filters.length === 0 && (
        <span className="text-sm text-gray-400 dark:text-gray-500">{t('filterBar.noFilters', 'No filters applied')}</span>
      )}

      {filters.map((filter) => {
        const label = `${filter.field} ${filter.operator} ${filter.value}`
        return (
          <Tag key={filter.id} variant="info" onClose={() => onRemove(filter.id)}>
            {label}
          </Tag>
        )
      })}

      <Button variant="ghost" size="sm" onClick={onAdd}>
        <svg className="mr-1 h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
        {t('filterBar.add', 'Add Filter')}
      </Button>
    </div>
  )
}
