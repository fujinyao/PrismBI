'use client'

import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { ChartContainer } from './ChartContainer'
import { ChartTypeSelector } from './ChartTypeSelector'
import { useI18nStore } from '@/stores/i18nStore'

interface FieldOption {
  key: string
  label: string
  type: 'dimension' | 'measure' | 'temporal'
}

interface ChartEditorProps {
  fields: FieldOption[]
  spec: any
  data: any[]
  onChange: (spec: any) => void
  showPreview?: boolean
}

export function ChartEditor({ fields, spec, data, onChange, showPreview = true }: ChartEditorProps) {
  const t = useI18nStore((s) => s.t)
  const dimensions = useMemo(() => fields.filter((f) => f.type === 'dimension'), [fields])
  const measures = useMemo(() => fields.filter((f) => f.type === 'measure'), [fields])
  const temporals = useMemo(() => fields.filter((f) => f.type === 'temporal'), [fields])

  const markType = spec?.mark?.type ?? spec?.mark ?? 'bar'
  const encoding = spec?.encoding ?? {}

  const updateEncoding = (channel: string, fieldKey: string | null) => {
    const newEncoding = { ...encoding }
    if (fieldKey) {
      const field = fields.find((f) => f.key === fieldKey)
      if (!field) return
      newEncoding[channel] = {
        field: field.key,
        type: field.type === 'temporal' ? 'temporal' : field.type === 'measure' ? 'quantitative' : 'nominal',
        title: field.label,
      }
    } else {
      delete newEncoding[channel]
    }
    onChange({ ...spec, encoding: newEncoding })
  }

  const updateMarkType = (type: string) => {
    const markType = type === 'scatter' ? 'point' : type === 'pie' ? 'arc' : type === 'heatmap' ? 'rect' : type
    const xField = encoding.x
    const yField = encoding.y
    const colorField = encoding.color
    const sizeField = encoding.size
    let nextEncoding = { ...encoding }
    if (type === 'pie') {
      nextEncoding = {
        theta: yField ?? sizeField,
        color: colorField ?? xField,
        tooltip: [colorField ?? xField, yField ?? sizeField].filter(Boolean),
      }
    } else if (type === 'heatmap') {
      nextEncoding = {
        x: xField ?? colorField,
        y: colorField ?? xField,
        color: yField ?? sizeField,
        tooltip: [xField ?? colorField, colorField ?? xField, yField ?? sizeField].filter(Boolean),
      }
    } else if (markType === 'point') {
      nextEncoding = {
        x: yField ?? xField,
        y: sizeField ?? Object.values(encoding).find((value: any) => value?.type === 'quantitative' && value?.field !== (yField as any)?.field) ?? yField,
        color: colorField ?? xField,
        tooltip: Object.values(encoding).filter((value: any) => value?.field),
      }
    }
    onChange({
      ...spec,
      mark: {
        ...(typeof spec?.mark === 'object' ? spec.mark : {}),
        type: markType,
      },
      encoding: nextEncoding,
    })
  }

  const updateEncodingProperty = (channel: string, key: string, value: string | null) => {
    const current = encoding[channel]
    if (!current) return
    const next = { ...current }
    if (value === null || value === '') {
      delete next[key]
    } else {
      next[key] = value
    }
    onChange({ ...spec, encoding: { ...encoding, [channel]: next } })
  }

  const updateTitle = (title: string) => {
    onChange({ ...spec, title: title || undefined })
  }

  const renderFieldSelector = (
    channel: string,
    label: string,
    options: FieldOption[],
    currentValue?: string,
  ) => {
    const currentField = encoding[channel]?.field as string | undefined

    return (
      <div className="space-y-1">
        <label className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</label>
        <select
          value={currentField ?? ''}
          onChange={(e) => updateEncoding(channel, e.target.value || null)}
          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-primary focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
        >
          <option value="">{t('chart.none', '-- None --')}</option>
          {options.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {showPreview && <ChartContainer spec={spec} data={data} />}

      <div className="space-y-3 rounded-lg border border-gray-200 p-4 dark:border-gray-700 dark:bg-gray-950/60">
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100">
          {t('chart.configuration', 'Chart Configuration')}
        </h4>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
            {t('chart.type', 'Chart Type')}
          </label>
          <ChartTypeSelector value={markType === 'point' ? 'scatter' : markType === 'arc' ? 'pie' : markType === 'rect' ? 'heatmap' : markType} onChange={updateMarkType} />
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">{t('chart.title', 'Chart Title')}</label>
          <input
            value={spec?.title ?? ''}
            onChange={(event) => updateTitle(event.target.value)}
            placeholder={t('chart.titlePlaceholder', 'Optional chart title')}
            className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-primary focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          {renderFieldSelector(markType === 'arc' ? 'color' : 'x', markType === 'arc' ? t('chart.categoryField', 'Category') : t('chart.xField', 'X Axis'), [...dimensions, ...temporals], encoding?.x?.field as string)}
          {renderFieldSelector(markType === 'arc' ? 'theta' : 'y', markType === 'arc' ? t('chart.valueField', 'Value') : t('chart.yField', 'Y Axis'), measures, encoding?.y?.field as string)}
        </div>

        <div className="grid grid-cols-2 gap-3">
          {renderFieldSelector(markType === 'rect' ? 'y' : 'color', markType === 'rect' ? t('chart.yCategory', 'Y Category') : t('chart.colorField', 'Color'), [...dimensions, ...temporals], encoding?.color?.field as string)}
          {renderFieldSelector(markType === 'rect' ? 'color' : 'size', markType === 'rect' ? t('chart.colorValue', 'Color Value') : t('chart.sizeField', 'Size'), measures, encoding?.size?.field as string)}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-gray-500 dark:text-gray-400">{t('chart.aggregate', 'Aggregation')}</label>
            <select
              value={encoding?.y?.aggregate ?? encoding?.theta?.aggregate ?? ''}
              onChange={(event) => {
                updateEncodingProperty('y', 'aggregate', event.target.value || null)
                updateEncodingProperty('theta', 'aggregate', event.target.value || null)
              }}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-primary focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              <option value="">{t('chart.aggregate.none', 'None')}</option>
              <option value="sum">{t('chart.aggregate.sum', 'Sum')}</option>
              <option value="mean">{t('chart.aggregate.mean', 'Average')}</option>
              <option value="count">{t('chart.aggregate.count', 'Count')}</option>
              <option value="max">{t('chart.aggregate.max', 'Max')}</option>
              <option value="min">{t('chart.aggregate.min', 'Min')}</option>
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-gray-500 dark:text-gray-400">{t('chart.sort', 'Category Sort')}</label>
            <select
              value={encoding?.x?.sort ?? ''}
              onChange={(event) => updateEncodingProperty('x', 'sort', event.target.value || null)}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-primary focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              <option value="">{t('chart.sort.default', 'Default')}</option>
              <option value="-y">{t('chart.sort.desc', 'Metric descending')}</option>
              <option value="x">{t('chart.sort.asc', 'Category ascending')}</option>
            </select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-gray-500 dark:text-gray-400">{t('chart.categoryAxis', 'Category Labels')}</label>
            <select
              value={String(encoding?.x?.axis?.labelAngle ?? '')}
              onChange={(event) => {
                const value = event.target.value
                const x = encoding.x ?? {}
                onChange({ ...spec, encoding: { ...encoding, x: { ...x, axis: { ...(x.axis ?? {}), labelAngle: value === '' ? undefined : Number(value) } } } })
              }}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-primary focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              <option value="">{t('chart.labels.auto', 'Auto')}</option>
              <option value="0">{t('chart.labels.horizontal', 'Horizontal')}</option>
              <option value="-35">{t('chart.labels.tilted', 'Tilted')}</option>
              <option value="-90">{t('chart.labels.vertical', 'Vertical')}</option>
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-gray-500 dark:text-gray-400">{t('chart.tooltip', 'Tooltip')}</label>
            <select
              value={encoding?.tooltip ? 'on' : 'off'}
              onChange={(event) => onChange({ ...spec, encoding: { ...encoding, tooltip: event.target.value === 'on' ? Object.values(encoding).filter((value: any) => value?.field) : undefined } })}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-primary focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              <option value="on">{t('chart.tooltip.on', 'Show')}</option>
              <option value="off">{t('chart.tooltip.off', 'Hide')}</option>
            </select>
          </div>
        </div>
      </div>
    </div>
  )
}
