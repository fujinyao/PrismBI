'use client'

import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'
import {
  ModelObjectKindIcon,
  modelObjectKindIconWrapClass,
  modelObjectKindLabel,
  modelObjectKindPillClass,
  modelObjectKindShortLabel,
  normalizeModelObjectKind,
  type ModelObjectKind,
} from '@/lib/modelObjectKind'

export const ModelNode = memo(function ModelNode({ data, selected }: any) {
  const t = useI18nStore((s) => s.t)
  const {
    label,
    description,
    fields,
    color = '#1677ff',
    relationLinked = false,
    nodeKind = 'model',
    modelObjectKind,
  } = data
  const normalizedModelObjectKind: ModelObjectKind | null =
    nodeKind === 'model' ? normalizeModelObjectKind(modelObjectKind) : null
  const modelTypeText = normalizedModelObjectKind ? modelObjectKindLabel(normalizedModelObjectKind, t) : ''

  return (
    <div
      className={cn(
        'rounded-lg border-2 bg-white shadow-sm transition-shadow dark:bg-gray-800',
        selected
          ? 'border-primary shadow-lg ring-2 ring-primary-200 dark:ring-primary-800'
          : relationLinked
            ? 'border-orange-300 shadow-md ring-1 ring-orange-200 dark:border-orange-700 dark:ring-orange-900/60'
          : 'border-gray-200 dark:border-gray-700',
      )}
      style={{ minWidth: 200, maxWidth: 320 }}
    >
      <Handle id="left" type="target" position={Position.Left} className="!border-2 !border-primary !bg-white" />

      <div
        className="rounded-t-md px-3 py-2 text-sm font-semibold text-white"
        style={{ backgroundColor: color }}
      >
        <div className="flex min-w-0 items-center gap-2">
          {normalizedModelObjectKind ? (
            <span
              className={cn(
                'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border bg-white/90 text-[inherit]',
                modelObjectKindIconWrapClass(normalizedModelObjectKind),
              )}
              title={modelTypeText}
            >
              <ModelObjectKindIcon kind={normalizedModelObjectKind} className="h-3.5 w-3.5" />
            </span>
          ) : nodeKind === 'view' ? (
            <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-white/60 bg-white/25" title={t('modeling.view', 'View')}>
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" />
                <circle cx="12" cy="12" r="2.5" />
              </svg>
            </span>
          ) : null}
          <span className="min-w-0 flex-1 truncate">{label}</span>
          {normalizedModelObjectKind && (
            <span
              className={cn(
                'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
                modelObjectKindPillClass(normalizedModelObjectKind),
              )}
              title={modelTypeText}
            >
              {modelObjectKindShortLabel(normalizedModelObjectKind, t)}
            </span>
          )}
        </div>
        {description && <div className="mt-0.5 truncate text-[11px] font-normal text-white/80">{description}</div>}
      </div>

      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {fields.map((field: any, i: number) => (
          <div
            key={field.name ?? i}
            className="flex items-center justify-between px-3 py-1.5 text-xs"
          >
            <div className="flex items-center gap-1.5">
              {field.primaryKey && (
                <span className="text-yellow-500" title={t('modeling.primaryKey', 'Primary Key')}>
                  <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                  </svg>
                </span>
              )}
              <span className="font-medium text-gray-700 dark:text-gray-200" title={field.description}>
                {field.name}
              </span>
              {field.isCalculated && (
                <span
                  className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                  title={t('modeling.calculatedField', 'Calculated Field')}
                >
                  fx
                </span>
              )}
            </div>
            <span className="ml-2 font-mono text-gray-400 dark:text-gray-500">
              {field.type}
            </span>
          </div>
        ))}
      </div>

      <Handle id="right" type="source" position={Position.Right} className="!border-2 !border-primary !bg-white" />
    </div>
  )
})
