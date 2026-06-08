'use client'

import { forwardRef, useState, useRef, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

export interface SelectOption<T = string> {
  label: string
  value: T
}

export interface SelectProps<T = string> {
  value: T | undefined
  options: SelectOption<T>[]
  onChange: (value: T) => void
  placeholder?: string
  searchable?: boolean
  label?: string
  error?: string
  disabled?: boolean
  className?: string
}

export function Select<T = string>({
  value,
  options,
  onChange,
  placeholder: placeholderProp,
  searchable = false,
  label,
  error,
  disabled,
  className,
}: SelectProps<T>) {
  const t = useI18nStore((s) => s.t)
  const placeholder = placeholderProp ?? t('common.select', 'Select...')
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const searchComposing = useRef(false)
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const filtered = searchable
    ? options.filter((opt) => opt.label.toLowerCase().includes(search.toLowerCase()))
    : options

  const selected = options.find((opt) => opt.value === value)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    if (open && searchable) {
      setTimeout(() => searchRef.current?.focus(), 50)
    }
    if (!open) setSearch('')
  }, [open, searchable])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    },
    [],
  )

  return (
    <div className={cn('space-y-1', className)}>
      {label && (
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
          {label}
        </label>
      )}
      <div ref={ref} className="relative" onKeyDown={handleKeyDown}>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen(!open)}
          className={cn(
            'flex w-full items-center justify-between rounded-md border px-3 py-2 text-sm transition-colors',
            'bg-white dark:bg-gray-800',
            'text-gray-900 dark:text-gray-100',
            'focus:outline-none focus:ring-2 focus:ring-primary-300',
            error
              ? 'border-error'
              : 'border-gray-300 dark:border-gray-600',
            disabled && 'cursor-not-allowed opacity-50',
          )}
        >
          <span className={selected ? '' : 'text-gray-400 dark:text-gray-500'}>
            {selected?.label ?? placeholder}
          </span>
          <svg className="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {open && (
          <div className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-gray-800">
            {searchable && (
              <div className="sticky top-0 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-2">
                <input
                  ref={searchRef}
                   onInput={(e) => { if (!searchComposing.current && !(e.nativeEvent as InputEvent).isComposing) setSearch((e.target as HTMLInputElement).value) }}
                  onCompositionStart={() => { searchComposing.current = true }}
                  onCompositionEnd={(e) => { searchComposing.current = false; setSearch((e.target as HTMLInputElement).value) }}
                  placeholder={t('common.search', 'Search...')}
                  className="w-full rounded border border-gray-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                />
              </div>
            )}
            {filtered.length === 0 ? (
              <div className="px-3 py-2 text-sm text-gray-500">{t('common.noResults', 'No results')}</div>
            ) : (
              filtered.map((opt) => (
                <button
                  key={String(opt.value)}
                  type="button"
                  className={cn(
                    'w-full px-3 py-2 text-left text-sm transition-colors hover:bg-gray-100 dark:hover:bg-gray-700',
                    opt.value === value && 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400',
                  )}
                  onClick={() => {
                    onChange(opt.value)
                    setOpen(false)
                  }}
                >
                  {opt.label}
                </button>
              ))
            )}
          </div>
        )}
      </div>
      {error && <p className="text-sm text-error">{error}</p>}
    </div>
  )
}
