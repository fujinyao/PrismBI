'use client'

import { useState, useCallback, useEffect, useMemo } from 'react'
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

interface TreeItem {
  id: string
  label: string
  type: TreeItemType
  modelObjectKind?: ModelObjectKind
  children?: TreeItem[]
}

type TreeItemType = 'model' | 'view' | 'relation' | 'calculated_field'

interface ModelTreeProps {
  models: any[]
  views: any[]
  relations?: any[]
  linkedModelIds?: string[]
  modelKindsById?: Record<string, ModelObjectKind>
  onSelect: (id: string, type: TreeItemType) => void
  onEdit?: (id: string, type: TreeItemType) => void
  onDelete?: (id: string, type: TreeItemType) => void
  onAddModel?: () => void
  onAddView?: () => void
  onAddRelation?: () => void
  onRefreshModels?: () => void
  refreshing?: boolean
  selectedId?: string
  selectedType?: TreeItemType
}

export function ModelTree({
  models,
  views,
  relations = [],
  linkedModelIds = [],
  modelKindsById = {},
  onSelect,
  onEdit,
  onDelete,
  onAddModel,
  onAddView,
  onAddRelation,
  onRefreshModels,
  refreshing,
  selectedId,
  selectedType,
}: ModelTreeProps) {
  const t = useI18nStore((s) => s.t)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; id: string; type: TreeItemType } | null>(null)
  const initialCollapsedGroups = useMemo(() => ({
    'models-group': models.length === 0,
    'relations-group': relations.length === 0,
    'views-group': views.length === 0,
  }), [models.length, relations.length, views.length])
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(initialCollapsedGroups)
  const linkedModelIdSet = useMemo(() => new Set(linkedModelIds.map((id) => String(id))), [linkedModelIds])

  useEffect(() => {
    setCollapsedGroups((prev) => ({
      ...prev,
      ...(models.length === 0 && prev['models-group'] === undefined ? { 'models-group': true } : {}),
      ...(relations.length === 0 && prev['relations-group'] === undefined ? { 'relations-group': true } : {}),
      ...(views.length === 0 && prev['views-group'] === undefined ? { 'views-group': true } : {}),
    }))
  }, [models.length, relations.length, views.length])

  useEffect(() => {
    if (!selectedId || !selectedType) return
    const targetGroupId =
      selectedType === 'model'
        ? 'models-group'
        : selectedType === 'relation'
          ? 'relations-group'
          : selectedType === 'view'
            ? 'views-group'
            : null
    if (!targetGroupId) return
    setCollapsedGroups((prev) =>
      prev[targetGroupId]
        ? { ...prev, [targetGroupId]: false }
        : prev,
    )
  }, [selectedId, selectedType])

  const handleContextMenu = useCallback((e: React.MouseEvent, item: TreeItem, depth: number) => {
    if (depth === 0) return
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY, id: item.id, type: item.type })
  }, [])

  const handleRename = useCallback(() => {
    if (contextMenu) onEdit?.(contextMenu.id, contextMenu.type)
    setContextMenu(null)
  }, [contextMenu, onEdit])

  const handleDelete = useCallback(() => {
    if (contextMenu) onDelete?.(contextMenu.id, contextMenu.type)
    setContextMenu(null)
  }, [contextMenu, onDelete])

  const items: TreeItem[] = [
    {
      id: 'models-group',
      label: t('modeling.models', 'Models'),
      type: 'model',
      children: models.map((m, i) => ({
        id: String(m.id ?? `model-${i}`),
        label: m.name ?? m.label ?? `Model ${i + 1}`,
        type: 'model' as const,
        modelObjectKind: normalizeModelObjectKind(modelKindsById[String(m.id)] ?? m.model_type ?? m.table_type),
      })),
    },
    {
      id: 'relations-group',
      label: t('modeling.relationships', 'Relationships'),
      type: 'relation',
      children: relations.map((relation, i) => {
        const sourceModelId = relation.source_model_id ?? relation.sourceModelId
        const targetModelId = relation.target_model_id ?? relation.targetModelId
        const sourceModel = models.find((model) => String(model.id) === String(sourceModelId))
        const targetModel = models.find((model) => String(model.id) === String(targetModelId))
        const sourceModelLabel = sourceModel?.display_name ?? sourceModel?.name ?? sourceModelId
        const targetModelLabel = targetModel?.display_name ?? targetModel?.name ?? targetModelId
        return {
          id: String(relation.id ?? `relation-${i}`),
          label:
            relation.name ??
            `${sourceModelLabel}.${relation.source_column ?? relation.sourceField ?? ''} -> ${targetModelLabel}.${relation.target_column ?? relation.targetField ?? ''}`,
          type: 'relation' as const,
        }
      }),
    },
    {
      id: 'views-group',
      label: t('modeling.views', 'Views'),
      type: 'view',
      children: views.map((v, i) => ({
        id: String(v.id ?? `view-${i}`),
        label: v.name ?? v.label ?? `View ${i + 1}`,
        type: 'view' as const,
      })),
    },
  ]

  const renderLeaf = (item: TreeItem) => {
    const isSelected = item.id === selectedId && item.type === selectedType
    const isRelationLinkedModel = selectedType === 'relation' && item.type === 'model' && linkedModelIdSet.has(item.id)
    const modelObjectKind = item.type === 'model' ? normalizeModelObjectKind(item.modelObjectKind) : null

    return (
      <button
        key={item.id}
        type="button"
        draggable
        onClick={() => onSelect(item.id, item.type)}
        onDoubleClick={() => onEdit?.(item.id, item.type)}
        onContextMenu={(event) => handleContextMenu(event, item, 1)}
        onDragStart={(event) => {
          event.dataTransfer.setData('text/plain', item.id)
          event.dataTransfer.effectAllowed = 'move'
        }}
        className={cn(
          'group flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors',
          isSelected
            ? 'bg-primary-50 text-primary dark:bg-primary-900/20 dark:text-primary-300'
            : isRelationLinkedModel
              ? 'bg-orange-50 text-orange-700 dark:bg-orange-900/20 dark:text-orange-200'
            : 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800',
        )}
      >
        {item.type === 'model' && modelObjectKind && (
          <span
            className={cn(
              'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border',
              modelObjectKindIconWrapClass(modelObjectKind),
            )}
            title={modelObjectKindLabel(modelObjectKind, t)}
          >
            <ModelObjectKindIcon kind={modelObjectKind} className="h-3.5 w-3.5" />
          </span>
        )}
        {item.type === 'view' && (
          <svg className="h-3.5 w-3.5 shrink-0 text-success" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z" />
          </svg>
        )}
        {item.type === 'relation' && (
          <svg className="h-3.5 w-3.5 shrink-0 text-orange-500" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 4a3 3 0 1 1-.001 6.001A3 3 0 0 1 8 4Zm8 10a3 3 0 1 1-.001 6.001A3 3 0 0 1 16 14ZM9.9 8.5l4.2 7 .85-.5-4.2-7-.85.5Z" />
          </svg>
        )}
        {item.type === 'calculated_field' && (
          <svg className="h-3.5 w-3.5 shrink-0 text-purple-500" viewBox="0 0 24 24" fill="currentColor">
            <path d="M7 5h10v2H9.41l2.3 2.29a1 1 0 0 1 0 1.42L9.41 13H17v2H7a1 1 0 0 1-.71-1.71L9.59 10 6.29 6.71A1 1 0 0 1 7 5Zm0 12h10v2H7v-2Z" />
          </svg>
        )}
        <span className="min-w-0 flex-1 truncate">{item.label}</span>
        {item.type === 'model' && modelObjectKind && (
          <span
            className={cn(
              'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
              modelObjectKindPillClass(modelObjectKind),
            )}
            title={modelObjectKindLabel(modelObjectKind, t)}
          >
            {modelObjectKindShortLabel(modelObjectKind, t)}
          </span>
        )}
      </button>
    )
  }

  const renderGroup = (item: TreeItem) => {
    const children = item.children ?? []
    const isCollapsed = Boolean(collapsedGroups[item.id])

    return (
      <section key={item.id} className={cn('flex min-h-0 flex-col rounded-lg border border-gray-100 bg-white/60 dark:border-gray-800 dark:bg-gray-900/40', isCollapsed ? 'flex-none' : 'flex-1')}>
        <div
          className={cn(
            'flex shrink-0 items-center gap-2 px-3 py-2 text-sm font-semibold text-gray-700 dark:text-gray-200',
          )}
        >
          <button
            type="button"
            onClick={() => setCollapsedGroups((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}
            className="rounded p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            aria-label={isCollapsed ? t('common.expand', 'Expand') : t('common.collapse', 'Collapse')}
          >
            <svg className={cn('h-3.5 w-3.5 transition-transform', isCollapsed && '-rotate-90')} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <span className="min-w-0 flex-1 truncate">{item.label}</span>
          <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-500 dark:bg-gray-800 dark:text-gray-400">{children.length}</span>
          {item.id === 'models-group' && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  onRefreshModels?.()
                }}
                className="rounded px-1.5 py-0.5 text-xs font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                title={t('modeling.refreshModels', 'Refresh models')}
              >
                {refreshing ? '...' : '↻'}
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  onAddModel?.()
                }}
                className="rounded px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary-50 dark:hover:bg-primary-900/30"
              >
                {t('common.new', 'New')}
              </button>
            </div>
          )}
          {item.id === 'relations-group' && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onAddRelation?.()
              }}
              className="rounded px-2 py-0.5 text-xs font-medium text-orange-600 hover:bg-orange-50 dark:text-orange-300 dark:hover:bg-orange-900/30"
            >
              {t('common.new', 'New')}
            </button>
          )}
          {item.id === 'views-group' && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onAddView?.()
              }}
              className="rounded px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary-50 dark:hover:bg-primary-900/30"
            >
              {t('common.new', 'New')}
            </button>
          )}
        </div>
        {!isCollapsed && (
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {children.length === 0 ? (
              <div className="px-3 py-4 text-center text-xs text-gray-400">{t('common.noData', 'No data')}</div>
            ) : (
              <div className="space-y-1">{children.map((child) => renderLeaf(child))}</div>
            )}
          </div>
        )}
      </section>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col p-3">
      <div className="shrink-0 px-3 py-2">
        <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t('modeling.dataModel', 'Data Model')}</p>
      </div>
      <div className="mt-3 flex min-h-0 flex-1 flex-col gap-3">
        {items.map((item) => renderGroup(item))}
      </div>

      {contextMenu && (
        <>
          <div className="fixed inset-0 z-50" onClick={() => setContextMenu(null)} />
          <div
            className="fixed z-50 min-w-[140px] rounded-md border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              onClick={() => {
                if (contextMenu) onSelect(contextMenu.id, contextMenu.type)
                setContextMenu(null)
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              {t('modeling.viewDetails', 'View details')}
            </button>
            <button
              onClick={handleRename}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              {t('modeling.editMetadata', 'Edit metadata')}
            </button>
            <button
              onClick={handleDelete}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-error hover:bg-error-50 dark:hover:bg-error-900/30"
            >
              {t('modeling.delete', 'Delete')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
