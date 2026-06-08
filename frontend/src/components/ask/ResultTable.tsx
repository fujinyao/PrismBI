'use client'

import { useState, useMemo, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { truncate } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'
import { useVirtualScroll } from '@/hooks/useVirtualScroll'

const VIRTUAL_SCROLL_THRESHOLD = 100
const ROW_HEIGHT = 40

interface ColumnDef {
  key: string
  label: string
  type?: string
}

interface ResultTableProps {
  columns: ColumnDef[] | string[]
  rows: Record<string, unknown>[]
  pageSize?: number
  loading?: boolean
}

export function ResultTable({
  columns,
  rows,
  pageSize = 50,
  loading = false,
}: ResultTableProps) {
  const t = useI18nStore((s) => s.t)
  const [page, setPage] = useState(0)
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const cols: ColumnDef[] = useMemo(
    () =>
      columns.map((c) => (typeof c === 'string' ? { key: c, label: c } : c)),
    [columns],
  )

  const sortedRows = useMemo(() => {
    if (!sortKey) return rows
    return [...rows].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av == null) return 1
      if (bv == null) return -1
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av
      }
      const cmp = String(av).localeCompare(String(bv))
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [rows, sortKey, sortDir])

  const useVirtual = sortedRows.length > VIRTUAL_SCROLL_THRESHOLD
  const { virtualizer, totalSize, virtualItems, containerRef } = useVirtualScroll({
    count: sortedRows.length,
    estimateSize: ROW_HEIGHT,
    overscan: 10,
  })

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize))
  const safePage = Math.min(page, totalPages - 1)
  const pageRows = useVirtual ? sortedRows : sortedRows.slice(safePage * pageSize, (safePage + 1) * pageSize)

  const handleSort = useCallback(
    (key: string) => {
      if (sortKey === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
      } else {
        setSortKey(key)
        setSortDir('asc')
      }
    },
    [sortKey],
  )

  const handleExportCSV = useCallback(() => {
    const escapeCsvCell = (value: unknown): string => {
      const str = value === null || value === undefined ? '' : String(value)
      if (str.startsWith('=') || str.startsWith('+') || str.startsWith('-') || str.startsWith('@') || str.startsWith('\t') || str.startsWith('\r') || str.startsWith('\n')) {
        return `'${JSON.stringify(str).slice(1, -1)}`
      }
      return JSON.stringify(str).slice(1, -1)
    }
    const header = cols.map((c) => c.label).join(',')
    const data = rows
      .map((r) => cols.map((c) => escapeCsvCell(r[c.key])).join(','))
      .join('\n')
    const blob = new Blob([`${header}\n${data}`], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'query_results.csv'
    a.click()
    URL.revokeObjectURL(url)
  }, [cols, rows])

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-200 p-8 dark:border-gray-700">
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-6 animate-pulse rounded bg-gray-100 dark:bg-gray-800" />
          ))}
        </div>
      </div>
    )
  }

  if (cols.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 p-8 text-center text-sm text-gray-400 dark:border-gray-700">
        {t('result.noResults', 'No results')}
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="flex items-center justify-between bg-gray-50 px-3 py-2 dark:bg-gray-800">
        <span className="text-xs text-gray-500">
          {t('result.rows', `${sortedRows.length} ${sortedRows.length === 1 ? 'row' : 'rows'}`)}
        </span>
        <Button variant="ghost" size="sm" onClick={handleExportCSV}>
          {t('result.exportCSV', 'Export CSV')}
        </Button>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>
              {cols.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="cursor-pointer whitespace-nowrap px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                >
                  <div className="flex items-center gap-1">
                    {col.label}
                    {sortKey === col.key && (
                      <span className="text-primary">{sortDir === 'asc' ? '\u2191' : '\u2193'}</span>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
        </table>
      </div>

      {useVirtual ? (
        <div
          ref={containerRef}
          className="overflow-y-auto"
          style={{ maxHeight: `${Math.min(600, sortedRows.length * ROW_HEIGHT)}px` }}
        >
          <div style={{ height: totalSize, position: 'relative' }}>
            {virtualItems.map((virtualRow) => {
              const row = sortedRows[virtualRow.index]
              if (!row) return null
              return (
                <div
                  key={virtualRow.index}
                  className="absolute left-0 flex w-full border-b border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:hover:bg-gray-800"
                  style={{
                    height: `${virtualRow.size}px`,
                    top: virtualRow.start,
                  }}
                >
                  <div className="flex min-w-full">
                    {cols.map((col) => (
                      <div
                        key={col.key}
                        className="max-w-[250px] truncate whitespace-nowrap px-3 py-2 text-sm text-gray-700 dark:text-gray-300"
                        title={String(row[col.key] ?? '')}
                      >
                        {truncate(String(row[col.key] ?? '-'), 120)}
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
              {pageRows.length === 0 ? (
                <tr>
                  <td
                    colSpan={cols.length}
                    className="px-3 py-8 text-center text-sm text-gray-400"
                  >
                    {t('common.noData', 'No data')}
                  </td>
                </tr>
              ) : (
                pageRows.map((row, i) => (
                  <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                    {cols.map((col) => (
                      <td
                        key={col.key}
                        className="max-w-[250px] truncate whitespace-nowrap px-3 py-2 text-sm text-gray-700 dark:text-gray-300"
                        title={String(row[col.key] ?? '')}
                      >
                        {truncate(String(row[col.key] ?? '-'), 120)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {!useVirtual && totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800">
          <Button
            variant="ghost"
            size="sm"
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            {t('result.previous', 'Previous')}
          </Button>
          <span className="text-xs text-gray-500">
            {t('result.pageOf', `Page ${safePage + 1} of ${totalPages}`)}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={safePage >= totalPages - 1}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
          >
            {t('common.next', 'Next')}
          </Button>
        </div>
      )}
    </div>
  )
}
