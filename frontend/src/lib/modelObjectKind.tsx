import type { ComponentPropsWithoutRef } from 'react'

export type ModelObjectKind = 'table' | 'view' | 'materialized_view' | 'other'

type TranslateFn = (key: string, fallback?: string) => string

export function normalizeModelObjectKind(value: unknown): ModelObjectKind {
  const raw = String(value || '').trim().toLowerCase().replace(/[_-]/g, ' ')
  const compact = raw.replace(/\s+/g, '')
  if (!raw) return 'table'
  if (raw.includes('materialized') && raw.includes('view')) return 'materialized_view'
  if (compact === 'materializedview' || compact === 'matview' || compact === 'mview') return 'materialized_view'
  if (raw.includes('view')) return 'view'
  if (compact === 'table' || compact === 'basetable') return 'table'
  if (
    compact === 'foreigntable' ||
    compact === 'externaltable' ||
    compact === 'temporarytable' ||
    compact === 'localtemporary' ||
    compact === 'localtemporarytable' ||
    compact === 'temptable'
  ) {
    return 'other'
  }
  if (raw.includes('table')) return 'other'
  return 'other'
}

function fallbackModelObjectKindLabel(kind: ModelObjectKind): string {
  if (kind === 'table') return 'Table'
  if (kind === 'view') return 'View'
  if (kind === 'materialized_view') return 'Materialized View'
  return 'Other'
}

export function modelObjectKindLabel(kind: ModelObjectKind, t?: TranslateFn): string {
  const fallback = fallbackModelObjectKindLabel(kind)
  if (!t) return fallback
  return t(`modeling.modelKind.${kind}`, fallback)
}

export function modelObjectKindShortLabel(kind: ModelObjectKind, t?: TranslateFn): string {
  if (kind === 'materialized_view') {
    return t ? t('modeling.modelKind.materialized_view_short', 'MView') : 'MView'
  }
  return modelObjectKindLabel(kind, t)
}

export function modelObjectKindHeaderColor(kind: ModelObjectKind): string {
  if (kind === 'view') return '#0f766e'
  if (kind === 'materialized_view') return '#b45309'
  if (kind === 'other') return '#475569'
  return '#1677ff'
}

export function modelObjectKindIconWrapClass(kind: ModelObjectKind): string {
  if (kind === 'view') return 'border-teal-200 bg-teal-50 text-teal-700 dark:border-teal-800/70 dark:bg-teal-900/30 dark:text-teal-200'
  if (kind === 'materialized_view') return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800/70 dark:bg-amber-900/30 dark:text-amber-200'
  if (kind === 'other') return 'border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'
  return 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800/70 dark:bg-blue-900/30 dark:text-blue-200'
}

export function modelObjectKindPillClass(kind: ModelObjectKind): string {
  if (kind === 'view') return 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-200'
  if (kind === 'materialized_view') return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200'
  if (kind === 'other') return 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200'
  return 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200'
}

export function normalizeModelReferenceKey(value: unknown): string {
  const raw = String(value || '').trim()
  if (!raw) return ''
  return raw.replace(/["`]/g, '').toLowerCase()
}

export function candidateModelReferenceKeys(value: unknown): string[] {
  const normalized = normalizeModelReferenceKey(value)
  if (!normalized) return []
  const parts = normalized.split('.').filter(Boolean)
  if (parts.length <= 1) return [normalized]
  const keys = new Set<string>()
  for (let index = 0; index < parts.length; index += 1) {
    keys.add(parts.slice(index).join('.'))
  }
  return Array.from(keys)
}

export function ModelObjectKindIcon({ kind, className = 'h-4 w-4', ...rest }: { kind: ModelObjectKind; className?: string } & ComponentPropsWithoutRef<'svg'>) {
  if (kind === 'view') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...rest}>
        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
    )
  }
  if (kind === 'materialized_view') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...rest}>
        <path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" />
        <path d="M3 12l9 4.5 9-4.5" />
        <path d="M3 16.5 12 21l9-4.5" />
      </svg>
    )
  }
  if (kind === 'other') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...rest}>
        <path d="M12 2 4 6v12l8 4 8-4V6l-8-4Z" />
        <path d="m4 6 8 4 8-4" />
        <path d="M12 10v12" />
      </svg>
    )
  }
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...rest}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M8 4v16" />
    </svg>
  )
}
