'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

export interface DashboardNavItem {
  id: number
  name: string
  display_name?: string
  item_count?: number
}

interface DashboardSidebarProps {
  dashboards: DashboardNavItem[]
  selectedDashboardId?: number
  canCreate?: boolean
  onCreate?: () => void
  onSelect?: (id: number) => void
  onRename?: (id: number, name: string) => void
  onDelete?: (id: number) => void
}

export function DashboardSidebar({ dashboards, selectedDashboardId, canCreate, onCreate, onSelect, onRename, onDelete }: DashboardSidebarProps) {
  const t = useI18nStore((s) => s.t)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')

  const startRename = (dashboard: DashboardNavItem) => {
    setEditingId(dashboard.id)
    setEditingName(dashboard.display_name || dashboard.name)
  }

  const submitRename = () => {
    if (editingId && editingName.trim()) {
      onRename?.(editingId, editingName.trim())
    }
    setEditingId(null)
    setEditingName('')
  }

  return (
    <aside className="hidden w-72 shrink-0 rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900 lg:block">
      <div className="flex h-full flex-col">
        <div className="px-2 py-1.5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t('nav.dashboard', 'Dashboards')}</p>
            </div>
          {canCreate && (
            <button
              onClick={onCreate}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-primary-50 dark:hover:bg-primary-900/20"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              {t('common.new', 'New')}
            </button>
          )}
          </div>
        </div>

        <div className="mt-2 min-h-0 flex-1 overflow-y-auto px-1">
          {dashboards.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-gray-400">
              {t('dashboard.noDashboards', 'No dashboards yet')}
            </div>
          ) : (
            <div className="space-y-1">
              {dashboards.map((dashboard) => {
                const active = selectedDashboardId === dashboard.id
                return (
                  <div
                    key={dashboard.id}
                    className={cn(
                      'group flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm transition-colors',
                      active
                        ? 'bg-primary-50 text-primary dark:bg-primary-900/20 dark:text-primary-300'
                        : 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800',
                    )}
                  >
                    <button
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      onClick={() => onSelect?.(dashboard.id)}
                    >
                      <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-6m4 6V7m4 10v-3M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                      </svg>
                      {editingId === dashboard.id ? (
                        <input
                          value={editingName}
                          onChange={(event) => setEditingName(event.target.value)}
                          onBlur={submitRename}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') submitRename()
                            if (event.key === 'Escape') setEditingId(null)
                          }}
                          className="min-w-0 flex-1 rounded border border-primary-200 bg-white px-1.5 py-1 text-sm text-gray-900 outline-none dark:border-primary-700 dark:bg-gray-800 dark:text-gray-100"
                          autoFocus
                          onClick={(event) => event.stopPropagation()}
                        />
                      ) : (
                        <span className="min-w-0 flex-1 truncate">{dashboard.display_name || dashboard.name}</span>
                      )}
                      {editingId !== dashboard.id && (
                        <span className="shrink-0 text-[11px] text-gray-400">{dashboard.item_count ?? 0}</span>
                      )}
                    </button>
                    {editingId !== dashboard.id && (
                      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                        {onRename && (
                          <button
                            onClick={() => startRename(dashboard)}
                            className="rounded p-1 text-gray-400 hover:bg-white hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
                            aria-label={t('common.rename', 'Rename')}
                          >
                            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5M18.5 2.5a2.121 2.121 0 113 3L12 15l-4 1 1-4 9.5-9.5z" />
                            </svg>
                          </button>
                        )}
                        {onDelete && (
                          <button
                            onClick={() => onDelete(dashboard.id)}
                            className="rounded p-1 text-gray-400 hover:bg-white hover:text-error dark:hover:bg-gray-700"
                            aria-label={t('common.delete', 'Delete')}
                          >
                            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
