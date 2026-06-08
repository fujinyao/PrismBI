'use client'

import { BottomSheet } from './BottomSheet'
import { useI18nStore } from '@/stores/i18nStore'
import dynamic from 'next/dynamic'

const ChartContainer = dynamic(
  () => import('@/components/chart/ChartContainer').then((m) => ({ default: m.ChartContainer })),
  { ssr: false },
)

interface MobileChartViewerProps {
  open: boolean
  onClose: () => void
  spec: Record<string, unknown>
  data?: Record<string, unknown>[] | null
  title?: string
}

export function MobileChartViewer({ open, onClose, spec, data, title }: MobileChartViewerProps) {
  const t = useI18nStore((s) => s.t)

  const sheetTitle = title || t('chart.viewChart', 'View Chart')

  return (
    <BottomSheet open={open} onClose={onClose} title={sheetTitle}>
      <div className="min-h-[200px]">
        <ChartContainer
          spec={spec}
          data={data || []}
        />
      </div>
    </BottomSheet>
  )
}