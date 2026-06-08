'use client'

import { useState, useCallback } from 'react'
import { Tag } from '@/components/ui/Tag'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface Field {
  name: string
  type: string
  isPrimaryKey?: boolean
  nullable?: boolean
}

interface FieldListProps {
  fields: Field[]
  onReorder?: (fields: Field[]) => void
}

export function FieldList({ fields, onReorder }: FieldListProps) {
  const t = useI18nStore((s) => s.t)
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [overIndex, setOverIndex] = useState<number | null>(null)

  const handleDragStart = useCallback((index: number) => {
    setDragIndex(index)
  }, [])

  const handleDragOver = useCallback(
    (e: React.DragEvent, index: number) => {
      e.preventDefault()
      setOverIndex(index)
    },
    [],
  )

  const handleDrop = useCallback(
    (e: React.DragEvent, dropIndex: number) => {
      e.preventDefault()
      if (dragIndex === null || dragIndex === dropIndex || !onReorder) return
      const reordered = [...fields]
      const [moved] = reordered.splice(dragIndex, 1)
      if (!moved) return
      reordered.splice(dropIndex, 0, moved)
      onReorder(reordered)
      setDragIndex(null)
      setOverIndex(null)
    },
    [dragIndex, fields, onReorder],
  )

  const handleDragEnd = useCallback(() => {
    setDragIndex(null)
    setOverIndex(null)
  }, [])

  if (fields.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-gray-300 py-8 dark:border-gray-600">
        <span className="text-sm text-gray-400">{t('modeling.noFields', 'No fields defined')}</span>
      </div>
    )
  }

  return (
    <div className="divide-y divide-gray-100 dark:divide-gray-700">
      {fields.map((field, index) => (
        <div
          key={field.name ?? index}
          draggable
          onDragStart={() => handleDragStart(index)}
          onDragOver={(e) => handleDragOver(e, index)}
          onDrop={(e) => handleDrop(e, index)}
          onDragEnd={handleDragEnd}
          className={cn(
            'flex items-center gap-3 px-3 py-2 transition-colors',
            dragIndex === index && 'opacity-50',
            overIndex === index && 'border-t-2 border-primary',
            'hover:bg-gray-50 dark:hover:bg-gray-800',
          )}
        >
          <span
            className="cursor-grab text-gray-400 hover:text-gray-600 active:cursor-grabbing dark:hover:text-gray-300"
            title={t('modeling.dragToReorder', 'Drag to reorder')}
          >
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 6h2v2H8V6zm6 0h2v2h-2V6zM8 11h2v2H8v-2zm6 0h2v2h-2v-2zm-6 5h2v2H8v-2zm6 0h2v2h-2v-2z" />
            </svg>
          </span>

          <div className="flex flex-1 items-center gap-2 min-w-0">
            <div className="flex items-center gap-1.5">
              {field.isPrimaryKey && (
                <span className="text-yellow-500" title={t('modeling.primaryKey', 'Primary Key')}>
                  <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                  </svg>
                </span>
              )}
              <span className="truncate text-sm font-medium text-gray-700 dark:text-gray-200">
                {field.name}
              </span>
            </div>
            <Tag variant="default" size="sm">{field.type}</Tag>
            {field.nullable && (
              <span className="shrink-0 text-xs text-gray-400 dark:text-gray-500" title={t('modeling.nullable', 'Nullable')}>
                NULL
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
