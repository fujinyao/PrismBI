'use client'

import { useMemo } from 'react'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface DashboardGridProps {
  items: any[]
  layouts: any
  onLayoutChange: (layout: any) => void
  onItemClick?: (id: string) => void
  isEditable?: boolean
}

interface LayoutItem {
  i: string
  x: number
  y: number
  w: number
  h: number
  minW?: number
  minH?: number
}

function DashboardGridInner({
  items,
  layouts,
  onLayoutChange,
  onItemClick,
  isEditable,
}: DashboardGridProps) {
  const layout = useMemo(() => {
    if (layouts?.lg) return layouts
    return {
      lg: items.map((item, i) => ({
        i: item.id ?? String(i),
        x: (i % 3) * 4,
        y: Math.floor(i / 3) * 3,
        w: 4,
        h: 3,
        minW: 2,
        minH: 2,
      })),
    }
  }, [items, layouts])

  if (typeof window === 'undefined') return null

  const ResponsiveReactGridLayout =
    require('react-grid-layout').ResponsiveReactGridLayout

  return (
    <ResponsiveReactGridLayout
      className="layout"
      layouts={{ lg: layout.lg ?? layout }}
      breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
      cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
      rowHeight={120}
      isDraggable={isEditable}
      isResizable={isEditable}
      onLayoutChange={(l: LayoutItem[]) =>
        onLayoutChange({ ...layout, lg: l })
      }
      draggableHandle=".dashboard-drag-handle"
    >
      {items.map((item) => (
        <div
          key={item.id}
          className={cn(
            'rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden',
            onItemClick && 'cursor-pointer',
          )}
          onClick={() => onItemClick?.(item.id)}
        >
          {item.content}
        </div>
      ))}
    </ResponsiveReactGridLayout>
  )
}

export function DashboardGrid(props: DashboardGridProps) {
  const { items, loading, layouts, onLayoutChange, onItemClick, isEditable } = props as DashboardGridProps & { loading?: boolean }
  const t = useI18nStore((s) => s.t)

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    )
  }

  if (!items || items.length === 0) {
    return (
      <EmptyState
        title={t('dashboard.empty', 'Empty dashboard')}
        description={t('dashboard.emptyDesc', 'Add widgets to build your dashboard')}
      />
    )
  }

  return (
    <DashboardGridInner
      items={items}
      layouts={layouts}
      onLayoutChange={onLayoutChange}
      onItemClick={onItemClick}
      isEditable={isEditable}
    />
  )
}
