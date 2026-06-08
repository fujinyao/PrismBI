'use client'

import { useMemo } from 'react'
import dynamic from 'next/dynamic'
import type { VisualizationSpec } from 'vega-embed'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

const VegaEmbed = dynamic(() => import('react-vega').then((m) => m.VegaEmbed as any), { ssr: false }) as any

interface ChartContainerProps {
  spec: any
  data: any[]
  loading?: boolean
  error?: string
}

const SUPPORTED_TYPES = ['bar', 'line', 'arc', 'rect', 'point', 'scatter', 'area', 'heatmap', 'pie'] as const
const UNSAFE_SPEC_KEYS = new Set([
  'expr',
  'expression',
  'signal',
  'signals',
  'usermeta',
])

function sanitizeSpecValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeSpecValue)
  if (!value || typeof value !== 'object') return value

  const result: Record<string, unknown> = {}
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (UNSAFE_SPEC_KEYS.has(key)) continue
    if (key === 'data') continue
    if (key === 'url') continue
    result[key] = sanitizeSpecValue(child)
  }
  return result
}

function sanitizeUserSpec(userSpec: any) {
  const sanitized = sanitizeSpecValue(userSpec) as Record<string, unknown>
  return sanitized && typeof sanitized === 'object' && !Array.isArray(sanitized) ? sanitized : {}
}

function buildVegaLiteSpec(userSpec: any, data: any[]): VisualizationSpec {
  const hasData = data && data.length > 0
  const safeSpec = sanitizeUserSpec(userSpec)
  const userMark = (safeSpec.mark as any)?.type ?? safeSpec.mark
  const normalizedMark = userMark === 'pie'
    ? { type: 'arc', tooltip: true }
    : userMark === 'heatmap'
      ? { type: 'rect', tooltip: true }
      : safeSpec.mark

  const baseSpec: VisualizationSpec = {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    background: 'transparent',
    width: 'container',
    height: 'container',
    padding: 8,
    autosize: { type: 'fit', contains: 'padding' },
    ...safeSpec,
    mark: normalizedMark as any,
    data: hasData ? { values: data.slice(0, 10000) } : { values: [] },
    config: {
      axis: {
        labelColor: '#6b7280',
        titleColor: '#374151',
        gridColor: '#e5e7eb',
        labelFontSize: 11,
        titleFontSize: 12,
      },
      legend: {
        labelColor: '#6b7280',
        titleColor: '#374151',
      },
      ...((safeSpec.config as Record<string, unknown>) ?? {}),
    },
  }

  return baseSpec
}

export function ChartContainer({ spec, data, loading = false, error }: ChartContainerProps) {
  const t = useI18nStore((s) => s.t)
  const vegaSpec = useMemo(() => buildVegaLiteSpec(spec ?? {}, data ?? []), [spec, data])

  if (error) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-error-200 bg-error-50 p-4 dark:border-error-900/30 dark:bg-error-900/10">
        <p className="text-sm text-error-600 dark:text-error-400">{error}</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-gray-200 dark:border-gray-700">
        <div className="flex flex-col items-center gap-2">
          <svg
            className="h-6 w-6 animate-spin text-primary"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span className="text-xs text-gray-400">{t('chart.rendering', 'Rendering chart...')}</span>
        </div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-gray-300 dark:border-gray-600">
        <span className="text-sm text-gray-400">{t('chart.noData', 'No data to display')}</span>
      </div>
    )
  }

  const markType = spec?.mark?.type ?? spec?.mark ?? 'bar'
  const normalizedMarkType = markType === 'pie' ? 'arc' : markType === 'heatmap' ? 'rect' : markType

  if (!SUPPORTED_TYPES.includes(normalizedMarkType as any)) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-gray-300 dark:border-gray-600">
        <span className="text-sm text-gray-400">
          {t('chart.unsupported', `Unsupported chart type: ${String(markType)}`)}
        </span>
      </div>
    )
  }

  return (
    <div className={cn('h-full min-h-64 w-full')}>
      <VegaEmbed
        spec={vegaSpec}
        options={{ actions: false }}
        className="h-full w-full"
        style={{ width: '100%', height: '100%', minHeight: 256 }}
      />
    </div>
  )
}
