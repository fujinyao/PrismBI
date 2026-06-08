'use client'

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Tag } from '@/components/ui/Tag'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface Field {
  name: string
  type: string
  display_name?: string | null
  description?: string | null
  expression?: string | null
  isPrimaryKey?: boolean
  primaryKey?: boolean
}

interface ModelPanelNode {
  kind: 'model'
  id: string
  label?: string
  displayName?: string
  description?: string | null
  tableReference?: string
  sourceBindingId?: number | null
  type?: string
  fields?: Field[]
  calculatedFields?: Array<{
    id: string
    name?: string
    displayName?: string
    description?: string
    expression?: string
    resultType?: string
  }>
}

interface ViewPanelNode {
  kind: 'view'
  id: string
  label?: string
  displayName?: string
  description?: string | null
  modelName?: string
  fields?: Field[]
}

interface RelationPanelNode {
  kind: 'relation'
  id: string
  label?: string
  name?: string
  description?: string | null
  sourceModelId?: string
  sourceModelName?: string
  sourceColumn?: string
  targetModelId?: string
  targetModelName?: string
  targetColumn?: string
  relationType?: string
  sourceColumns?: Array<{ name: string; type: string; isCalculated?: boolean }>
  targetColumns?: Array<{ name: string; type: string; isCalculated?: boolean }>
}

interface CalculatedFieldPanelNode {
  kind: 'calculated_field'
  id: string
  label?: string
  name?: string
  displayName?: string
  description?: string | null
  modelName?: string
  expression?: string
  resultType?: string
}

export type PropertyPanelNode = ModelPanelNode | ViewPanelNode | RelationPanelNode | CalculatedFieldPanelNode

interface PropertyPanelProps {
  node: PropertyPanelNode
  mode?: 'view' | 'edit'
  onClose: () => void
  onSave: (data: any) => void
  onSelect?: (selection: { kind: 'model' | 'view' | 'relation' | 'calculated_field'; id: string }) => void
  onEdit?: () => void
  onAddCalculatedField?: (modelId: number) => void
  onEditCalculatedField?: (fieldId: number) => void
  onDeleteCalculatedField?: (fieldId: number) => void
  saving?: boolean
  datasourceBindings?: Array<{ id: number; name: string; display_name?: string }>
}

function isFieldContainer(node: PropertyPanelNode): node is ModelPanelNode | ViewPanelNode {
  return node.kind === 'model' || node.kind === 'view'
}

function nodeTitle(node: PropertyPanelNode, t: (key: string, fallback?: string) => string) {
  if (node.kind === 'relation') return t('modeling.relationship', 'Relationship')
  if (node.kind === 'view') return t('modeling.view', 'View')
  if (node.kind === 'calculated_field') return t('modeling.calculatedField', 'Calculated Field')
  return t('modeling.properties', 'Properties')
}

export function PropertyPanel({
  node,
  mode = 'view',
  onClose,
  onSave,
  onSelect,
  onEdit,
  onAddCalculatedField,
  onEditCalculatedField,
  onDeleteCalculatedField,
  saving,
  datasourceBindings,
}: PropertyPanelProps) {
  const t = useI18nStore((s) => s.t)
  const [name, setName] = useState(node.kind === 'relation' || node.kind === 'calculated_field' ? node.name ?? node.label ?? '' : node.label ?? '')
  const [displayName, setDisplayName] = useState(isFieldContainer(node) || node.kind === 'calculated_field' ? node.displayName ?? '' : '')
  const [description, setDescription] = useState(node.description ?? '')
  const [fields, setFields] = useState<Field[]>(isFieldContainer(node) ? node.fields ?? [] : [])
  const [sourceColumn, setSourceColumn] = useState(node.kind === 'relation' ? node.sourceColumn ?? '' : '')
  const [targetColumn, setTargetColumn] = useState(node.kind === 'relation' ? node.targetColumn ?? '' : '')
  const [relationType, setRelationType] = useState(node.kind === 'relation' ? node.relationType ?? 'MANY_TO_ONE' : 'MANY_TO_ONE')
  const [expression, setExpression] = useState(node.kind === 'calculated_field' ? node.expression ?? '' : '')
  const [resultType, setResultType] = useState(node.kind === 'calculated_field' ? node.resultType ?? '' : '')
  const [sourceBindingId, setSourceBindingId] = useState<number | null | undefined>(node.kind === 'model' ? node.sourceBindingId : undefined)

  const prevNodeIdRef = useRef<string | number | null>(null)
  useEffect(() => {
    const nodeId = (node as any).id ?? null
    if (nodeId !== prevNodeIdRef.current) {
      prevNodeIdRef.current = nodeId
      setName(node.kind === 'relation' || node.kind === 'calculated_field' ? node.name ?? node.label ?? '' : node.label ?? '')
      setDisplayName(isFieldContainer(node) || node.kind === 'calculated_field' ? node.displayName ?? '' : '')
      setDescription(node.description ?? '')
      setFields(isFieldContainer(node) ? node.fields ?? [] : [])
      setSourceColumn(node.kind === 'relation' ? node.sourceColumn ?? '' : '')
      setTargetColumn(node.kind === 'relation' ? node.targetColumn ?? '' : '')
      setRelationType(node.kind === 'relation' ? node.relationType ?? 'MANY_TO_ONE' : 'MANY_TO_ONE')
      setExpression(node.kind === 'calculated_field' ? node.expression ?? '' : '')
      setResultType(node.kind === 'calculated_field' ? node.resultType ?? '' : '')
      setSourceBindingId(node.kind === 'model' ? node.sourceBindingId : undefined)
    }
  }, [node])

  const editable = mode === 'edit'

  const togglePrimaryKey = (index: number) => {
    if (!editable) return
    setFields((prev) =>
      prev.map((field, i) =>
        i === index ? { ...field, isPrimaryKey: !Boolean(field.isPrimaryKey || field.primaryKey) } : field,
      ),
    )
  }

  const handleSave = () => {
    if (node.kind === 'relation') {
      onSave({
        id: node.id,
        kind: node.kind,
        name,
        description,
        source_column: sourceColumn,
        target_column: targetColumn,
        relation_type: relationType,
      })
      return
    }

    if (node.kind === 'calculated_field') {
      onSave({
        id: node.id,
        kind: node.kind,
        name,
        display_name: displayName,
        description,
        expression,
        result_type: resultType,
      })
      return
    }

    onSave({
      id: node.id,
      kind: node.kind,
      name,
      display_name: displayName,
      description,
      fields,
      ...(node.kind === 'model' ? { source_binding_id: sourceBindingId } : {}),
    })
  }

  return (
    <div className="flex h-full flex-col rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3 dark:border-gray-700">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
            {nodeTitle(node, t)}
          </h3>
          <p className="text-xs text-gray-400">
            {editable ? t('modeling.editMetadata', 'Edit metadata') : t('modeling.viewMetadata', 'View metadata')}
          </p>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
          aria-label={t('modeling.closePanel', 'Close panel')}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        <Input label={t('modeling.name', 'Name')} value={name} onChange={(e) => setName(e.target.value)} disabled={!editable} />

        {(isFieldContainer(node) || node.kind === 'calculated_field') && (
          <Input
            label={t('modeling.displayName', 'Display Name')}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            disabled={!editable}
          />
        )}

        <div>
          <label className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-300">
            {t('modeling.description', 'Description')}
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={!editable}
            rows={4}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
          />
        </div>

        {node.kind === 'model' && node.tableReference && (
          <div>
            <span className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.tableReference', 'Table Reference')}</span>
            <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
              {node.tableReference}
            </div>
          </div>
        )}

        {node.kind === 'model' && datasourceBindings && datasourceBindings.length > 0 && (
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.datasource', 'Data Source')}
            </label>
            <select
              value={sourceBindingId ?? ''}
              onChange={(e) => setSourceBindingId(e.target.value ? Number(e.target.value) : null)}
              disabled={!editable}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
            >
              <option value="">{t('modeling.noDatasource', 'No data source assigned')}</option>
              {datasourceBindings.map((ds) => (
                <option key={ds.id} value={ds.id}>
                  {ds.display_name || ds.name}
                </option>
              ))}
            </select>
          </div>
        )}

        {node.kind === 'view' && node.modelName && (
          <div>
            <span className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.sourceModel', 'Source Model')}</span>
            <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
              {node.modelName}
            </div>
          </div>
        )}

        {node.kind === 'model' && (
          <div>
            <span className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.type', 'Type')}</span>
            <div className="mt-1">
              <Tag variant="info">{node.type ?? t('modeling.model', 'Model')}</Tag>
            </div>
          </div>
        )}

        {node.kind === 'model' && (
          <div className="rounded-lg border border-gray-200 bg-gray-50/80 p-3 dark:border-gray-700 dark:bg-gray-900/60">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.calculatedFields', 'Calculated Fields')}</span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  const modelId = Number(node.id)
                  if (Number.isFinite(modelId)) {
                    onAddCalculatedField?.(modelId)
                  }
                }}
              >
                {t('common.add', 'Add')}
              </Button>
            </div>
            {node.calculatedFields && node.calculatedFields.length > 0 ? (
              <div className="space-y-2">
                {node.calculatedFields.map((field) => (
                  <div key={field.id} className="rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                    {Number.isFinite(Number(field.id)) && (
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-sm text-gray-800 dark:text-gray-100">{field.displayName || field.name}</div>
                        {field.resultType && <div className="text-xs text-gray-500 dark:text-gray-400">{field.resultType}</div>}
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          className="rounded px-2 py-0.5 text-xs text-primary hover:bg-primary-50 dark:hover:bg-primary-900/30"
                          onClick={() => onEditCalculatedField?.(Number(field.id))}
                        >
                          {t('common.edit', 'Edit')}
                        </button>
                        <button
                          type="button"
                          className="rounded px-2 py-0.5 text-xs text-error hover:bg-error-50 dark:hover:bg-error-900/30"
                          onClick={() => onDeleteCalculatedField?.(Number(field.id))}
                        >
                          {t('modeling.delete', 'Delete')}
                        </button>
                      </div>
                    </div>
                    )}
                    {field.expression && (
                      <p className="mt-2 line-clamp-2 break-words font-mono text-xs text-gray-500 dark:text-gray-400">{field.expression}</p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400 dark:text-gray-500">{t('modeling.noCalculatedFields', 'No calculated fields yet')}</p>
            )}
          </div>
        )}

        {isFieldContainer(node) && (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.fields', 'Fields')}</span>
              <span className="text-xs text-gray-400">{t('modeling.fieldsCount', '{count} fields').replace('{count}', String(fields.length))}</span>
            </div>

            {fields.length === 0 ? (
              <p className="text-sm text-gray-400 dark:text-gray-500">{t('modeling.noFields', 'No fields defined')}</p>
            ) : (
              <div className="space-y-3">
                {fields.map((field, i) => {
                  const primaryKey = Boolean(field.isPrimaryKey || field.primaryKey)
                  return (
                    <div key={`${field.name}-${i}`} className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900">
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="break-all font-mono text-sm font-medium text-gray-800 dark:text-gray-100">
                            {field.name}
                          </div>
                          {field.display_name && field.display_name !== field.name && (
                            <div className="mt-0.5 break-words text-xs text-gray-500 dark:text-gray-400">{field.display_name}</div>
                          )}
                        </div>
                        <Tag variant="default" size="sm">{field.type}</Tag>
                        <button
                          onClick={() => togglePrimaryKey(i)}
                          disabled={!editable || node.kind === 'view'}
                          className={cn(
                            'rounded p-1 transition-colors',
                            primaryKey
                              ? 'text-yellow-500 hover:text-yellow-600'
                              : 'text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400',
                            (!editable || node.kind === 'view') && 'cursor-default hover:text-inherit',
                          )}
                          title={primaryKey ? t('modeling.primaryKey', 'Primary Key') : t('modeling.setAsPrimaryKey', 'Set as primary key')}
                        >
                          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                          </svg>
                        </button>
                      </div>

                      <div className="mt-3">
                        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">
                          {t('modeling.description', 'Description')}
                        </label>
                        {editable ? (
                          <textarea
                            value={field.description ?? ''}
                            onChange={(event) => {
                              const value = event.target.value
                              setFields((prev) => prev.map((item, index) => (index === i ? { ...item, description: value } : item)))
                            }}
                            rows={3}
                            className="block min-h-20 w-full resize-y rounded border border-gray-200 bg-white px-2.5 py-2 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
                          />
                        ) : (
                          <p className="whitespace-pre-wrap break-words text-sm text-gray-600 dark:text-gray-300">
                            {field.description || '-'}
                          </p>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {node.kind === 'relation' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.sourceModel', 'Source Model')}</span>
                {onSelect && node.sourceModelId ? (
                  <button
                    type="button"
                    onClick={() => onSelect({ kind: 'model', id: String(node.sourceModelId) })}
                    className="mt-1 w-full break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
                    title={t('modeling.viewDetails', 'View details')}
                  >
                    {node.sourceModelName}
                  </button>
                ) : (
                  <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                    {node.sourceModelName}
                  </div>
                )}
              </div>
              <div>
                <span className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.targetModel', 'Target Model')}</span>
                {onSelect && node.targetModelId ? (
                  <button
                    type="button"
                    onClick={() => onSelect({ kind: 'model', id: String(node.targetModelId) })}
                    className="mt-1 w-full break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
                    title={t('modeling.viewDetails', 'View details')}
                  >
                    {node.targetModelName}
                  </button>
                ) : (
                  <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                    {node.targetModelName}
                  </div>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.sourceColumn', 'Source Column')}</label>
              {editable ? (
                <select
                  value={sourceColumn}
                  onChange={(e) => setSourceColumn(e.target.value)}
                  disabled={(node.sourceColumns ?? []).length === 0}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  {(node.sourceColumns ?? []).map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}{column.isCalculated ? ' (fx)' : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                  {sourceColumn}
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.targetColumn', 'Target Column')}</label>
              {editable ? (
                <select
                  value={targetColumn}
                  onChange={(e) => setTargetColumn(e.target.value)}
                  disabled={(node.targetColumns ?? []).length === 0}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  {(node.targetColumns ?? []).map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}{column.isCalculated ? ' (fx)' : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                  {targetColumn}
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.relationType', 'Relation Type')}</label>
              <select
                value={relationType}
                onChange={(e) => setRelationType(e.target.value)}
                disabled={!editable}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
              >
                <option value="ONE_TO_ONE">ONE_TO_ONE</option>
                <option value="ONE_TO_MANY">ONE_TO_MANY</option>
                <option value="MANY_TO_ONE">MANY_TO_ONE</option>
              </select>
            </div>
          </>
        )}

        {node.kind === 'calculated_field' && (
          <>
            {node.modelName && (
              <div>
                <span className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.sourceModel', 'Source Model')}</span>
                <div className="mt-1 break-all rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
                  {node.modelName}
                </div>
              </div>
            )}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-300">
                {t('modeling.expression', 'Expression')}
              </label>
              <textarea
                value={expression}
                onChange={(e) => setExpression(e.target.value)}
                disabled={!editable}
                rows={5}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
              />
            </div>
            <Input label={t('modeling.resultType', 'Result Type')} value={resultType} onChange={(e) => setResultType(e.target.value)} disabled={!editable} />
          </>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-5 py-3 dark:border-gray-700">
        <Button variant="ghost" size="sm" onClick={onClose}>
          {editable ? t('modeling.cancel', 'Cancel') : t('common.close', 'Close')}
        </Button>
        {!editable && onEdit && (
          <Button variant="secondary" size="sm" onClick={onEdit}>
            {t('common.edit', 'Edit')}
          </Button>
        )}
        {editable && (
          <Button variant="primary" size="sm" onClick={handleSave} loading={saving} disabled={!name.trim()}>
            {t('modeling.save', 'Save')}
          </Button>
        )}
      </div>
    </div>
  )
}
