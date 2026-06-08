'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query'
import { modelingApi, projectsApi } from '@/lib/api'
import { useProjectStore } from '@/stores/projectStore'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Input } from '@/components/ui/Input'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { getModelLabel, normalizeModelFields, normalizeRelationFields } from '@/lib/modeling'
import { candidateModelReferenceKeys, modelObjectKindLabel, normalizeModelObjectKind, type ModelObjectKind } from '@/lib/modelObjectKind'
import dynamic from 'next/dynamic'

type SelectionKind = 'model' | 'view' | 'relation' | 'calculated_field'

interface DiagramSelection {
  kind: SelectionKind
  id: string
}

const Canvas = dynamic(
  () => import('@/components/diagram/Canvas').then((m) => ({ default: m.Canvas })),
  { ssr: false, loading: () => <Skeleton className="h-full w-full" /> },
)

const ModelTree = dynamic(
  () => import('@/components/modeling/ModelTree').then((m) => ({ default: m.ModelTree })),
  { ssr: false },
)

const PropertyPanel = dynamic(
  () => import('@/components/modeling/PropertyPanel').then((m) => ({ default: m.PropertyPanel })),
  { ssr: false },
)

interface DiagramData {
  models: any[]
  views: any[]
  relations: any[]
  calculated_fields: any[]
}

interface ProjectDatasourceBinding {
  id?: number
  binding_id?: number
  bindingId?: number
  alias?: string
  datasource_name?: string
  datasource_type?: string
}

interface DiscoveredTable {
  name: string
  schema?: string | null
  reference?: string
  tableType?: string | null
  table_type?: string | null
  display_name?: string | null
  description?: string | null
  columns?: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]
}

const RELATION_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'MANY_TO_ONE', label: 'Many-to-One' },
  { value: 'ONE_TO_MANY', label: 'One-to-Many' },
  { value: 'ONE_TO_ONE', label: 'One-to-One' },
]

const DEFAULT_RELATION_TYPE = 'MANY_TO_ONE'

interface CalculatedFieldDraft {
  id?: number
  modelId: number | ''
  name: string
  displayName: string
  description: string
  expression: string
  resultType: string
}

interface RelationDraft {
  id?: number
  name: string
  description: string
  sourceModelId: number | ''
  sourceColumn: string
  targetModelId: number | ''
  targetColumn: string
  relationType: string
}

function resetCalculatedFieldDraft(modelId?: number): CalculatedFieldDraft {
  return {
    modelId: modelId ?? '',
    name: '',
    displayName: '',
    description: '',
    expression: '',
    resultType: '',
  }
}

function resetRelationDraft(sourceModelId?: number): RelationDraft {
  return {
    name: '',
    description: '',
    sourceModelId: sourceModelId ?? '',
    sourceColumn: '',
    targetModelId: '',
    targetColumn: '',
    relationType: DEFAULT_RELATION_TYPE,
  }
}

export default function ModelingPage() {
  const { toast } = useToast()
  const t = useI18nStore((s) => s.t)
  const queryClient = useQueryClient()
  const currentProject = useProjectStore((s) => s.currentProject)
  const projectsLoading = useProjectStore((s) => s.loading)
  const projectsLoaded = useProjectStore((s) => s.loaded)
  const fetchProjects = useProjectStore((s) => s.fetchProjects)

  const [selected, setSelected] = useState<DiagramSelection | null>(null)
  const [focusTarget, setFocusTarget] = useState<(DiagramSelection & { nonce: number }) | null>(null)
  const [propertyMode, setPropertyMode] = useState<'view' | 'edit'>('view')
  const [showPropertyPanel, setShowPropertyPanel] = useState(false)

  const [createModelOpen, setCreateModelOpen] = useState(false)
  const [createViewInfoOpen, setCreateViewInfoOpen] = useState(false)
  const [newModelBindingId, setNewModelBindingId] = useState<number | ''>('')
  const [newModelTableRef, setNewModelTableRef] = useState('')
  const [newModelColumns, setNewModelColumns] = useState<string[]>([])
  const [newModelPrimaryKey, setNewModelPrimaryKey] = useState('')

  const [calculatedFieldModalOpen, setCalculatedFieldModalOpen] = useState(false)
  const [calculatedFieldDraft, setCalculatedFieldDraft] = useState<CalculatedFieldDraft>({
    modelId: '',
    name: '',
    displayName: '',
    description: '',
    expression: '',
    resultType: '',
  })

  const [relationModalOpen, setRelationModalOpen] = useState(false)
  const [relationDraft, setRelationDraft] = useState<RelationDraft>({
    name: '',
    description: '',
    sourceModelId: '',
    sourceColumn: '',
    targetModelId: '',
    targetColumn: '',
    relationType: DEFAULT_RELATION_TYPE,
  })

  useEffect(() => {
    if (!currentProject && !projectsLoading && !projectsLoaded) {
      fetchProjects()
    }
  }, [currentProject, fetchProjects, projectsLoaded, projectsLoading])

  const projectId = currentProject?.id

  const { data: diagram, isLoading, isError, refetch } = useQuery({
    queryKey: ['diagram', projectId],
    queryFn: () => modelingApi.diagram(projectId as number) as Promise<DiagramData>,
    enabled: Boolean(projectId),
  })

  const { data: projectDatasources = [], isLoading: datasourcesLoading } = useQuery({
    queryKey: ['project-datasources', projectId],
    queryFn: () => projectsApi.datasources.list(projectId as number) as Promise<ProjectDatasourceBinding[]>,
    enabled: Boolean(projectId),
  })

  const modelKindLookupBindingIds = useMemo(
    () =>
      Array.from(
        new Set(
          (diagram?.models ?? [])
            .map((model: any) => {
              const explicitKind = model.model_type ?? model.table_type
              if (explicitKind && String(explicitKind).trim()) return null
              const bindingId = Number(model.source_binding_id ?? model.sourceBindingId)
              return Number.isFinite(bindingId) && bindingId > 0 ? bindingId : null
            })
            .filter((bindingId): bindingId is number => typeof bindingId === 'number' && Number.isFinite(bindingId) && bindingId > 0),
        ),
      ),
    [diagram?.models],
  )

  const datasourceTablesQueries = useQueries({
    queries: modelKindLookupBindingIds.map((bindingId) => ({
      queryKey: ['project-datasource-tables', projectId, bindingId, 'modeling-object-kind'],
      queryFn: async () => {
        if (!projectId) {
          return { tables: [], table_details: [] as Array<{ name: string; reference?: string; table_type?: string | null }> }
        }
        try {
          return await projectsApi.datasources.tables(projectId, bindingId)
        } catch {
          return { tables: [], table_details: [] as Array<{ name: string; reference?: string; table_type?: string | null }> }
        }
      },
      enabled: Boolean(projectId && modelKindLookupBindingIds.length > 0),
      staleTime: 5 * 60 * 1000,
    })),
  })

  const discoveredKindsByBindingReference = useMemo(() => {
    const index = new Map<string, ModelObjectKind>()
    datasourceTablesQueries.forEach((query, queryIndex) => {
      const bindingId = modelKindLookupBindingIds[queryIndex]
      if (!bindingId) return
      const rows = query.data?.table_details ?? []
      rows.forEach((row) => {
        const rowKind = normalizeModelObjectKind(row.table_type ?? (row as { tableType?: string | null }).tableType)
        const keys = candidateModelReferenceKeys(row.reference ?? row.name)
        keys.forEach((key) => {
          if (key) index.set(`${bindingId}::${key}`, rowKind)
        })
      })
    })
    return index
  }, [modelKindLookupBindingIds, datasourceTablesQueries])

  const modelKindsById = useMemo<Record<string, ModelObjectKind>>(() => {
    const kinds: Record<string, ModelObjectKind> = {}
    const models = diagram?.models ?? []
    models.forEach((model: any) => {
      const modelId = String(model.id)
      const explicitKind = model.model_type ?? model.table_type
      if (explicitKind) {
        kinds[modelId] = normalizeModelObjectKind(explicitKind)
        return
      }
      const bindingId = Number(model.source_binding_id ?? model.sourceBindingId)
      if (!Number.isFinite(bindingId) || bindingId <= 0) {
        kinds[modelId] = 'table'
        return
      }
      const candidates = candidateModelReferenceKeys(model.table_reference ?? model.name)
      const discoveredKind = candidates
        .map((candidate) => discoveredKindsByBindingReference.get(`${bindingId}::${candidate}`))
        .find((value): value is ModelObjectKind => Boolean(value))
      kinds[modelId] = normalizeModelObjectKind(discoveredKind ?? 'table')
    })
    return kinds
  }, [diagram?.models, discoveredKindsByBindingReference])

  const { data: discoveredTables, isLoading: tablesLoading } = useQuery({
    queryKey: ['project-datasource-tables', projectId, newModelBindingId],
    queryFn: () => projectsApi.datasources.tables(projectId as number, Number(newModelBindingId)),
    enabled: Boolean(projectId && createModelOpen && newModelBindingId),
  })

  const tableDetails = useMemo<DiscoveredTable[]>(
    () =>
      discoveredTables?.table_details?.map((table) => ({
        ...table,
        tableType: (table as { tableType?: string | null; table_type?: string | null }).tableType ?? table.table_type,
      })) ??
      discoveredTables?.tables?.map((name) => ({ name, reference: name, columns: [] })) ??
      [],
    [discoveredTables],
  )

  const selectedTable = useMemo(
    () => tableDetails.find((table) => (table.reference ?? table.name) === newModelTableRef),
    [newModelTableRef, tableDetails],
  )

  useEffect(() => {
    if (!createModelOpen || newModelBindingId || projectDatasources.length === 0) return
    const first = projectDatasources[0]
    const bindingId = first?.bindingId ?? first?.binding_id ?? first?.id
    if (bindingId) setNewModelBindingId(Number(bindingId))
  }, [createModelOpen, newModelBindingId, projectDatasources])

  useEffect(() => {
    setNewModelTableRef('')
    setNewModelColumns([])
    setNewModelPrimaryKey('')
  }, [newModelBindingId])

  useEffect(() => {
    setNewModelColumns([])
    setNewModelPrimaryKey('')
  }, [newModelTableRef])

  const createModelMutation = useMutation({
    mutationFn: (data: {
      name: string
      display_name?: string
      description?: string
      table_reference?: string
      model_type?: string
      source_binding_id?: number
      columns?: unknown[]
    }) => modelingApi.models.create(projectId as number, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.modelCreated', 'Model created'), 'success')
      setCreateModelOpen(false)
      setNewModelBindingId('')
      setNewModelTableRef('')
      setNewModelColumns([])
      setNewModelPrimaryKey('')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.modelCreateFailed', 'Failed to create model'), 'error'),
  })

  const openCreateViewInfo = () => setCreateViewInfoOpen(true)

  const submitCreateModel = () => {
    if (!selectedTable || !newModelBindingId) {
      toast(t('modeling.selectSourceTable', 'Please select a source table'), 'warning')
      return
    }
    if (newModelColumns.length === 0) {
      toast(t('modeling.selectColumns', 'Please select at least one column'), 'warning')
      return
    }

    const sourceColumns = selectedTable.columns ?? []
    const columns = newModelColumns.map((columnName) => {
      const column = sourceColumns.find((item) => item.name === columnName)
      return {
        name: columnName,
        type: column?.type ?? 'TEXT',
        is_primary_key: columnName === newModelPrimaryKey,
        display_name: column?.display_name ?? undefined,
        description: column?.description ?? undefined,
      }
    })
    const modelName = selectedTable.name.replace(/[^a-zA-Z0-9_]+/g, '_')

    createModelMutation.mutate({
      name: modelName,
      display_name: selectedTable.display_name || selectedTable.name,
      description: selectedTable.description || undefined,
      table_reference: selectedTable.reference ?? selectedTable.name,
      model_type: normalizeModelObjectKind(selectedTable.tableType),
      source_binding_id: Number(newModelBindingId) || undefined,
      columns,
    })
  }

  const updateModelMutation = useMutation({
    mutationFn: (data: { id: number; payload: { name?: string; display_name?: string; description?: string; columns?: unknown[] } }) =>
      modelingApi.models.update(projectId as number, data.id, data.payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.propertiesSaved', 'Properties saved'), 'success')
      setPropertyMode('view')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.propertiesSaveFailed', 'Failed to save properties'), 'error'),
  })

  const updateRelationMutation = useMutation({
    mutationFn: (data: { id: number; payload: { name?: string; description?: string; source_column?: string; target_column?: string; relation_type?: string } }) =>
      modelingApi.relations.update(projectId as number, data.id, data.payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.propertiesSaved', 'Properties saved'), 'success')
      setRelationModalOpen(false)
      setRelationDraft(resetRelationDraft())
      setPropertyMode('view')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.propertiesSaveFailed', 'Failed to save properties'), 'error'),
  })

  const updateViewMutation = useMutation({
    mutationFn: (data: { id: number; payload: { name?: string; display_name?: string; description?: string; columns?: unknown[] } }) =>
      modelingApi.views.update(projectId as number, data.id, data.payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.propertiesSaved', 'Properties saved'), 'success')
      setPropertyMode('view')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.propertiesSaveFailed', 'Failed to save properties'), 'error'),
  })

  const updateCalculatedFieldMutation = useMutation({
    mutationFn: (data: { id: number; payload: { name?: string; display_name?: string; description?: string; expression?: string; result_type?: string } }) =>
      modelingApi.calculatedFields.update(projectId as number, data.id, data.payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.propertiesSaved', 'Properties saved'), 'success')
      setCalculatedFieldModalOpen(false)
      setCalculatedFieldDraft(resetCalculatedFieldDraft())
      setPropertyMode('view')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.propertiesSaveFailed', 'Failed to save properties'), 'error'),
  })

  const createRelationMutation = useMutation({
    mutationFn: (payload: {
      name: string
      description?: string
      source_model_id: number
      source_column: string
      target_model_id: number
      target_column: string
      relation_type?: string
    }) =>
      modelingApi.relations.create(projectId as number, {
        name: payload.name,
        description: payload.description,
        source_model_id: payload.source_model_id,
        source_column: payload.source_column,
        target_model_id: payload.target_model_id,
        target_column: payload.target_column,
        relation_type: payload.relation_type,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.relationCreated', 'Relationship created'), 'success')
      setRelationModalOpen(false)
      setRelationDraft(resetRelationDraft())
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.relationCreateFailed', 'Failed to create relationship'), 'error'),
  })

  const createCalculatedFieldMutation = useMutation({
    mutationFn: (payload: {
      name: string
      display_name?: string
      description?: string
      model_id: number
      expression: string
      result_type?: string
    }) =>
      modelingApi.calculatedFields.create(projectId as number, {
        name: payload.name,
        display_name: payload.display_name,
        description: payload.description,
        model_id: payload.model_id,
        expression: payload.expression,
        result_type: payload.result_type,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.calculatedFieldCreated', 'Calculated field created'), 'success')
      setCalculatedFieldModalOpen(false)
      setCalculatedFieldDraft(resetCalculatedFieldDraft())
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.calculatedFieldCreateFailed', 'Failed to create calculated field'), 'error'),
  })

  const deleteModelMutation = useMutation({
    mutationFn: (id: number) => modelingApi.models.delete(projectId as number, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.modelDeleted', 'Model deleted'), 'success')
      setSelected(null)
      setShowPropertyPanel(false)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.modelDeleteFailed', 'Failed to delete model'), 'error'),
  })

  const deleteRelationMutation = useMutation({
    mutationFn: (id: number) => modelingApi.relations.delete(projectId as number, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.relationDeleted', 'Relationship deleted'), 'success')
      setSelected(null)
      setShowPropertyPanel(false)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.relationDeleteFailed', 'Failed to delete relationship'), 'error'),
  })

  const deleteViewMutation = useMutation({
    mutationFn: (id: number) => modelingApi.views.delete(projectId as number, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.viewDeleted', 'View deleted'), 'success')
      setSelected(null)
      setShowPropertyPanel(false)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.viewDeleteFailed', 'Failed to delete view'), 'error'),
  })

  const deleteCalculatedFieldMutation = useMutation({
    mutationFn: (id: number) => modelingApi.calculatedFields.delete(projectId as number, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      toast(t('modeling.calculatedFieldDeleted', 'Calculated field deleted'), 'success')
      if (selected?.kind === 'calculated_field') {
        setSelected(null)
        setShowPropertyPanel(false)
      }
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('modeling.calculatedFieldDeleteFailed', 'Failed to delete calculated field'), 'error'),
  })

  const modelOptions = useMemo(
    () =>
      (diagram?.models ?? []).map((model: any) => ({
        value: Number(model.id),
        label: getModelLabel(model),
      })),
    [diagram?.models],
  )

  const modelById = useMemo(() => {
    const map = new Map<number, any>()
    for (const model of diagram?.models ?? []) {
      map.set(Number(model.id), model)
    }
    return map
  }, [diagram?.models])

  const relationColumnOptions = useMemo(() => {
    const source = relationDraft.sourceModelId ? modelById.get(Number(relationDraft.sourceModelId)) : null
    const target = relationDraft.targetModelId ? modelById.get(Number(relationDraft.targetModelId)) : null
    const calculatedFields = diagram?.calculated_fields ?? []
    return {
      sourceColumns: normalizeRelationFields(
        source,
        calculatedFields.filter((field: any) => Number(field.model_id) === Number(source?.id)),
      ),
      targetColumns: normalizeRelationFields(
        target,
        calculatedFields.filter((field: any) => Number(field.model_id) === Number(target?.id)),
      ),
    }
  }, [diagram?.calculated_fields, modelById, relationDraft.sourceModelId, relationDraft.targetModelId])

  const relationLinkedModelIds = useMemo(() => {
    if (!diagram || selected?.kind !== 'relation') return []
    const relation = diagram.relations.find((item: any) => String(item.id) === String(selected.id))
    if (!relation) return []
    const sourceModelId = relation.source_model_id ?? relation.sourceModelId
    const targetModelId = relation.target_model_id ?? relation.targetModelId
    const ids = [sourceModelId, targetModelId]
      .filter((value) => value !== undefined && value !== null)
      .map((value) => String(value))
    return Array.from(new Set(ids))
  }, [diagram, selected])

  const openCreateCalculatedFieldModal = (modelId?: number) => {
    setCalculatedFieldDraft(resetCalculatedFieldDraft(modelId))
    setCalculatedFieldModalOpen(true)
  }

  const openEditCalculatedFieldModal = (fieldId: number) => {
    const field = diagram?.calculated_fields?.find((item: any) => Number(item.id) === Number(fieldId))
    if (!field) return
    setCalculatedFieldDraft({
      id: Number(field.id),
      modelId: Number(field.model_id),
      name: field.name ?? '',
      displayName: field.display_name ?? '',
      description: field.description ?? '',
      expression: field.expression ?? '',
      resultType: field.result_type ?? field.type ?? '',
    })
    setCalculatedFieldModalOpen(true)
  }

  const submitCalculatedField = () => {
    const modelId = Number(calculatedFieldDraft.modelId)
    if (!modelId) {
      toast(t('modeling.selectSourceModel', 'Please select a source model'), 'warning')
      return
    }
    if (!calculatedFieldDraft.name.trim()) {
      toast(t('modeling.nameRequired', 'Name is required'), 'warning')
      return
    }
    if (!calculatedFieldDraft.expression.trim()) {
      toast(t('modeling.expressionRequired', 'Expression is required'), 'warning')
      return
    }

    const payload = {
      name: calculatedFieldDraft.name.trim(),
      display_name: calculatedFieldDraft.displayName.trim() || undefined,
      description: calculatedFieldDraft.description.trim() || undefined,
      model_id: modelId,
      expression: calculatedFieldDraft.expression.trim(),
      result_type: calculatedFieldDraft.resultType.trim() || undefined,
    }

    if (calculatedFieldDraft.id) {
      updateCalculatedFieldMutation.mutate({
        id: Number(calculatedFieldDraft.id),
        payload: {
          name: payload.name,
          display_name: payload.display_name,
          description: payload.description,
          expression: payload.expression,
          result_type: payload.result_type,
        },
      })
      return
    }

    createCalculatedFieldMutation.mutate(payload)
  }

  const openCreateRelationModal = useCallback(
    (sourceModelId?: number, targetModelId?: number) => {
      const nextDraft = resetRelationDraft(sourceModelId)
      if (sourceModelId) {
        const sourceModel = modelById.get(Number(sourceModelId))
        const sourceColumns = normalizeRelationFields(
          sourceModel,
          (diagram?.calculated_fields ?? []).filter((field: any) => Number(field.model_id) === Number(sourceModel?.id)),
        )
        if (sourceColumns.length > 0) {
          nextDraft.sourceColumn = sourceColumns[0]?.name ?? ''
        }
      }
      if (targetModelId) {
        nextDraft.targetModelId = Number(targetModelId)
        const targetModel = modelById.get(Number(targetModelId))
        const targetColumns = normalizeRelationFields(
          targetModel,
          (diagram?.calculated_fields ?? []).filter((field: any) => Number(field.model_id) === Number(targetModel?.id)),
        )
        if (targetColumns.length > 0) {
          nextDraft.targetColumn = targetColumns[0]?.name ?? ''
        }
      }
      setRelationDraft(nextDraft)
      setRelationModalOpen(true)
    },
    [diagram?.calculated_fields, modelById],
  )

  const openEditRelationModal = (relationId: number) => {
    const relation = diagram?.relations?.find((item: any) => Number(item.id) === Number(relationId))
    if (!relation) return
    setRelationDraft({
      id: Number(relation.id),
      name: relation.name ?? '',
      description: relation.description ?? '',
      sourceModelId: Number(relation.source_model_id),
      sourceColumn: relation.source_column ?? '',
      targetModelId: Number(relation.target_model_id),
      targetColumn: relation.target_column ?? '',
      relationType: relation.relation_type ?? relation.type ?? DEFAULT_RELATION_TYPE,
    })
    setRelationModalOpen(true)
  }

  const submitRelation = () => {
    const sourceModelId = Number(relationDraft.sourceModelId)
    const targetModelId = Number(relationDraft.targetModelId)

    if (!sourceModelId || !targetModelId) {
      toast(t('modeling.selectModelsForRelation', 'Please select source and target models'), 'warning')
      return
    }
    if (!relationDraft.sourceColumn || !relationDraft.targetColumn) {
      toast(t('modeling.selectColumnsForRelation', 'Please select source and target columns'), 'warning')
      return
    }
    if (sourceModelId === targetModelId && relationDraft.sourceColumn === relationDraft.targetColumn) {
      toast(t('modeling.invalidSelfRelation', 'Please choose different columns for self relationships'), 'warning')
      return
    }

    const sourceModelName = modelById.get(sourceModelId)?.name ?? `model_${sourceModelId}`
    const targetModelName = modelById.get(targetModelId)?.name ?? `model_${targetModelId}`
    const generatedName = `${sourceModelName}_${relationDraft.sourceColumn}_to_${targetModelName}_${relationDraft.targetColumn}`

    const payload = {
      name: relationDraft.name.trim() || generatedName,
      description: relationDraft.description.trim() || undefined,
      source_model_id: sourceModelId,
      source_column: relationDraft.sourceColumn,
      target_model_id: targetModelId,
      target_column: relationDraft.targetColumn,
      relation_type: relationDraft.relationType || DEFAULT_RELATION_TYPE,
    }

    if (relationDraft.id) {
      updateRelationMutation.mutate({
        id: Number(relationDraft.id),
        payload: {
          name: payload.name,
          description: payload.description,
          source_column: payload.source_column,
          target_column: payload.target_column,
          relation_type: payload.relation_type,
        },
      })
      return
    }

    createRelationMutation.mutate(payload)
  }

  useEffect(() => {
    if (!relationModalOpen) return
    const sourceColumns = relationColumnOptions.sourceColumns
    if (sourceColumns.length > 0 && !sourceColumns.some((column) => column.name === relationDraft.sourceColumn)) {
      setRelationDraft((prev) => ({ ...prev, sourceColumn: sourceColumns[0]?.name ?? '' }))
    }
    if (sourceColumns.length === 0 && relationDraft.sourceColumn) {
      setRelationDraft((prev) => ({ ...prev, sourceColumn: '' }))
    }
  }, [relationColumnOptions.sourceColumns, relationDraft.sourceColumn, relationModalOpen])

  useEffect(() => {
    if (!relationModalOpen) return
    const targetColumns = relationColumnOptions.targetColumns
    if (targetColumns.length > 0 && !targetColumns.some((column) => column.name === relationDraft.targetColumn)) {
      setRelationDraft((prev) => ({ ...prev, targetColumn: targetColumns[0]?.name ?? '' }))
    }
    if (targetColumns.length === 0 && relationDraft.targetColumn) {
      setRelationDraft((prev) => ({ ...prev, targetColumn: '' }))
    }
  }, [relationColumnOptions.targetColumns, relationDraft.targetColumn, relationModalOpen])

  const applySelection = useCallback((selection: DiagramSelection, options?: { focus?: boolean }) => {
    const normalized = { kind: selection.kind, id: String(selection.id) }
    setSelected(normalized)
    setPropertyMode('view')
    setShowPropertyPanel(true)
    if (options?.focus) {
      setFocusTarget({ ...normalized, nonce: Date.now() })
    }
  }, [])

  const handleTreeSelect = (id: string, kind: SelectionKind = 'model') => {
    applySelection({ kind, id }, { focus: true })
  }

  const handleDiagramSelect = (selection: DiagramSelection) => {
    applySelection(selection)
  }

  const handlePropertyPanelSelect = (selection: DiagramSelection) => {
    applySelection(selection, { focus: true })
  }

  const handleDiagramEdit = (selection: DiagramSelection) => {
    if (selection.kind === 'relation') {
      openEditRelationModal(Number(selection.id))
      return
    }
    if (selection.kind === 'calculated_field') {
      openEditCalculatedFieldModal(Number(selection.id))
      return
    }
    const normalized = { kind: selection.kind, id: String(selection.id) }
    setSelected(normalized)
    setPropertyMode('edit')
    setShowPropertyPanel(true)
    if (selection.kind === 'model' || selection.kind === 'view') {
      setFocusTarget({ ...normalized, nonce: Date.now() })
    }
  }

  const handleDiagramDelete = (selection: DiagramSelection) => {
    if (selection.kind === 'model') {
      deleteModelMutation.mutate(Number(selection.id))
    }
    if (selection.kind === 'relation') {
      deleteRelationMutation.mutate(Number(selection.id))
    }
    if (selection.kind === 'view') {
      deleteViewMutation.mutate(Number(selection.id))
    }
    if (selection.kind === 'calculated_field') {
      deleteCalculatedFieldMutation.mutate(Number(selection.id))
    }
  }

  useEffect(() => {
    if (!selected || !diagram) return

    const exists =
      selected.kind === 'model'
        ? diagram.models.some((model: any) => String(model.id) === selected.id)
        : selected.kind === 'view'
          ? diagram.views.some((view: any) => String(view.id) === selected.id)
          : selected.kind === 'relation'
            ? diagram.relations.some((relation: any) => String(relation.id) === selected.id)
            : diagram.calculated_fields.some((field: any) => String(field.id) === selected.id)

    if (exists) return

    setSelected(null)
    setShowPropertyPanel(false)
    setPropertyMode('view')
  }, [diagram, selected])

  const selectedNode = useMemo(() => {
    if (!selected || !diagram) return null
    if (selected.kind === 'relation') {
      const relation = diagram.relations.find((r: any) => String(r.id) === selected.id)
      if (!relation) return null
      const sourceModelId = relation.source_model_id ?? relation.sourceModelId
      const targetModelId = relation.target_model_id ?? relation.targetModelId
      const sourceColumn = relation.source_column ?? relation.sourceField ?? ''
      const targetColumn = relation.target_column ?? relation.targetField ?? ''
      const sourceModel = diagram.models.find((m: any) => String(m.id) === String(sourceModelId))
      const targetModel = diagram.models.find((m: any) => String(m.id) === String(targetModelId))
      const sourceCalculated = diagram.calculated_fields.filter((field: any) => String(field.model_id) === String(sourceModelId))
      const targetCalculated = diagram.calculated_fields.filter((field: any) => String(field.model_id) === String(targetModelId))
      return {
        kind: 'relation' as const,
        id: String(relation.id),
        name: relation.name,
        label: relation.name ?? `${sourceColumn} → ${targetColumn}`,
        description: relation.description,
        sourceModelId: String(sourceModelId ?? ''),
        sourceModelName: sourceModel?.display_name ?? sourceModel?.name ?? String(sourceModelId ?? ''),
        sourceColumn,
        targetModelId: String(targetModelId ?? ''),
        targetModelName: targetModel?.display_name ?? targetModel?.name ?? String(targetModelId ?? ''),
        targetColumn,
        relationType: relation.relation_type ?? relation.type ?? 'MANY_TO_ONE',
        sourceColumns: normalizeRelationFields(sourceModel, sourceCalculated),
        targetColumns: normalizeRelationFields(targetModel, targetCalculated),
      }
    }

    if (selected.kind === 'view') {
      const view = diagram.views.find((v: any) => String(v.id) === selected.id)
      if (!view) return null
      const sourceModel = diagram.models.find((m: any) => String(m.id) === String(view.model_id))
      const fields = view.fields && view.fields.length > 0 ? view.fields : view.column_defs ?? view.columns ?? []
      return {
        kind: 'view' as const,
        id: String(view.id),
        label: view.name ?? view.display_name ?? view.label ?? t('modeling.untitled', 'Untitled'),
        displayName: view.display_name,
        description: view.description,
        modelName: sourceModel?.display_name ?? sourceModel?.name ?? String(view.model_id),
        fields,
      }
    }

    if (selected.kind === 'calculated_field') {
      const field = diagram.calculated_fields.find((item: any) => String(item.id) === selected.id)
      if (!field) return null
      const model = diagram.models.find((m: any) => String(m.id) === String(field.model_id))
      return {
        kind: 'calculated_field' as const,
        id: String(field.id),
        name: field.name,
        label: field.name ?? field.display_name ?? t('modeling.untitled', 'Untitled'),
        displayName: field.display_name,
        description: field.description,
        modelName: model?.display_name ?? model?.name ?? String(field.model_id),
        expression: field.expression,
        resultType: field.result_type ?? field.type,
      }
    }

    const found = diagram.models.find((m: any) => String(m.id) === selected.id)
    if (!found) return null
    const fields = normalizeModelFields(found)
    const calculatedFields = diagram.calculated_fields
      .filter((field: any) => String(field.model_id) === String(found.id))
      .map((field: any) => ({
        id: String(field.id),
        name: field.name,
        displayName: field.display_name,
        description: field.description,
        expression: field.expression,
        resultType: field.result_type,
      }))
    return {
      kind: 'model' as const,
      id: String(found.id),
      label: found.name ?? found.display_name ?? found.label ?? t('modeling.untitled', 'Untitled'),
      displayName: found.display_name,
      description: found.description,
      tableReference: found.table_reference,
      sourceBindingId: found.source_binding_id ?? null,
      type: t('modeling.typeModel', 'Model'),
      fields,
      calculatedFields,
    }
  }, [selected, diagram, t])

  const handlePropertySave = (data: any) => {
    if (data.kind === 'relation') {
      updateRelationMutation.mutate({
        id: Number(data.id),
        payload: {
          name: data.name,
          description: data.description,
          source_column: data.source_column,
          target_column: data.target_column,
          relation_type: data.relation_type,
        },
      })
      return
    }

    if (data.kind === 'view') {
      updateViewMutation.mutate({
        id: Number(data.id),
        payload: {
          name: data.name,
          display_name: data.display_name,
          description: data.description,
          columns: data.fields?.map((field: any) => ({
            name: field.name,
            type: field.type,
            is_primary_key: Boolean(field.isPrimaryKey || field.primaryKey),
            expression: field.expression,
            display_name: field.display_name,
            description: field.description,
          })),
        },
      })
      return
    }

    if (data.kind === 'calculated_field') {
      updateCalculatedFieldMutation.mutate({
        id: Number(data.id),
        payload: {
          name: data.name,
          display_name: data.display_name,
          description: data.description,
          expression: data.expression,
          result_type: data.result_type,
        },
      })
      return
    }

    updateModelMutation.mutate({
      id: Number(data.id),
      payload: {
        name: data.name,
        display_name: data.display_name,
        description: data.description,
        ...(data.source_binding_id != null ? { source_binding_id: data.source_binding_id } : {}),
        columns: data.fields?.map((field: any) => ({
          name: field.name,
          type: field.type,
          is_primary_key: Boolean(field.isPrimaryKey || field.primaryKey),
          expression: field.expression,
          display_name: field.display_name,
          description: field.description,
        })),
      },
    })
  }

  if (projectsLoading || (!projectId && !currentProject) || isLoading) {
    return (
      <div className="flex h-full gap-4">
        <Skeleton className="h-full w-[280px] shrink-0 rounded-lg" />
        <Skeleton className="h-full flex-1 rounded-lg" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          message={t('modeling.diagramLoadError', 'Failed to load diagram')}
          description={t('modeling.diagramLoadErrorDesc', 'There was an error loading the modeling diagram.')}
          action={{ label: t('common.retry', 'Retry'), onClick: () => refetch() }}
        />
      </div>
    )
  }

  const dia = diagram ?? { models: [], views: [], relations: [], calculated_fields: [] }

  return (
    <div className="flex h-full gap-6">
      <aside className="flex w-80 shrink-0 flex-col rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <div className="min-h-0 flex-1">
          <ModelTree
            models={dia.models}
            views={dia.views}
            relations={dia.relations}
            linkedModelIds={relationLinkedModelIds}
            modelKindsById={modelKindsById}
            onSelect={handleTreeSelect}
            onEdit={(id, kind) => handleDiagramEdit({ id, kind })}
            onDelete={(id, kind) => handleDiagramDelete({ id, kind })}
            onAddModel={() => setCreateModelOpen(true)}
            onAddView={openCreateViewInfo}
            onAddRelation={() => openCreateRelationModal()}
            onRefreshModels={() => refetch()}
            refreshing={isLoading}
            selectedId={selected?.id}
            selectedType={selected?.kind}
          />
        </div>
      </aside>

      <main className="relative flex-1">
        <Canvas
          models={dia.models}
          views={dia.views}
          relations={dia.relations}
          modelKindsById={modelKindsById}
          calculatedFields={dia.calculated_fields}
          selectedId={selected?.id}
          selectedKind={selected?.kind}
          linkedModelIds={relationLinkedModelIds}
          focusTarget={focusTarget}
          onSelect={handleDiagramSelect}
          onEdit={handleDiagramEdit}
          onDelete={handleDiagramDelete}
          onCreateModel={() => setCreateModelOpen(true)}
          onCreateView={openCreateViewInfo}
          onCreateCalculatedField={openCreateCalculatedFieldModal}
          onCreateRelation={openCreateRelationModal}
          onPaneClick={() => {
            setSelected(null)
            setShowPropertyPanel(false)
            setPropertyMode('view')
          }}
        />
      </main>

      {showPropertyPanel && selectedNode && (
        <aside className="w-[416px] max-w-[35vw] shrink-0">
          <PropertyPanel
            node={selectedNode}
            mode={propertyMode}
            onClose={() => {
              setShowPropertyPanel(false)
              setSelected(null)
              setPropertyMode('view')
            }}
            onEdit={() => setPropertyMode('edit')}
            onSave={handlePropertySave}
            onSelect={handlePropertyPanelSelect}
            onAddCalculatedField={(modelId) => openCreateCalculatedFieldModal(modelId)}
            onEditCalculatedField={(fieldId) => openEditCalculatedFieldModal(fieldId)}
            onDeleteCalculatedField={(fieldId) => deleteCalculatedFieldMutation.mutate(fieldId)}
            datasourceBindings={projectDatasources.map((ds: any) => ({ id: ds.id, name: ds.datasource_name ?? ds.alias ?? `Datasource ${ds.id}`, display_name: ds.alias ?? ds.datasource_name }))}
            saving={
              updateModelMutation.isPending ||
              updateRelationMutation.isPending ||
              updateViewMutation.isPending ||
              updateCalculatedFieldMutation.isPending ||
              createRelationMutation.isPending ||
              createCalculatedFieldMutation.isPending
            }
          />
        </aside>
      )}

      <Modal
        open={createModelOpen}
        onClose={() => {
          setCreateModelOpen(false)
          setNewModelBindingId('')
          setNewModelTableRef('')
          setNewModelColumns([])
          setNewModelPrimaryKey('')
        }}
        title={t('modeling.createDataModel', 'Create a data model')}
        size="xl"
      >
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.selectDatasource', 'Select datasource')}
            </label>
            <select
              value={newModelBindingId}
              onChange={(e) => setNewModelBindingId(e.target.value ? Number(e.target.value) : '')}
              disabled={datasourcesLoading}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
            >
              <option value="">{datasourcesLoading ? t('common.loading', 'Loading...') : t('modeling.selectDatasourcePlaceholder', 'Select a datasource...')}</option>
              {projectDatasources.map((binding) => {
                const bindingId = binding.bindingId ?? binding.binding_id ?? binding.id
                return (
                  <option key={bindingId} value={bindingId}>
                    {binding.alias ?? binding.datasource_name ?? `Datasource ${bindingId}`}
                  </option>
                )
              })}
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.selectTable', 'Select a table')}
            </label>
            <select
              value={newModelTableRef}
              onChange={(e) => setNewModelTableRef(e.target.value)}
              disabled={!newModelBindingId || tablesLoading}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
            >
              <option value="">{tablesLoading ? t('common.loading', 'Loading...') : t('modeling.selectTablePlaceholder', 'Select a table...')}</option>
              {tableDetails.map((table) => {
                const tableKind = normalizeModelObjectKind(table.tableType ?? table.table_type)
                return (
                  <option key={table.reference ?? table.name} value={table.reference ?? table.name}>
                    {`${table.reference ?? table.name} (${modelObjectKindLabel(tableKind, t)})`}
                  </option>
                )
              })}
            </select>
            {discoveredTables?.warning && <p className="text-xs text-warning">{discoveredTables.warning}</p>}
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.selectColumns', 'Select columns')}</span>
              <span className="text-xs text-gray-400">{newModelColumns.length}/{selectedTable?.columns?.length ?? 0}</span>
            </div>
            <div className="max-h-64 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700">
              {selectedTable?.columns?.length ? (
                selectedTable.columns.map((column) => {
                  const checked = newModelColumns.includes(column.name)
                  return (
                    <label key={column.name} className="flex cursor-pointer items-center gap-3 border-b border-gray-100 px-3 py-2 text-sm last:border-b-0 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          setNewModelColumns((prev) =>
                            event.target.checked ? [...prev, column.name] : prev.filter((name) => name !== column.name),
                          )
                          if (!event.target.checked && newModelPrimaryKey === column.name) setNewModelPrimaryKey('')
                        }}
                      />
                      <span className="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">{column.name}</span>
                      <span className="font-mono text-xs text-gray-400">{column.type}</span>
                    </label>
                  )
                })
              ) : (
                <p className="px-3 py-6 text-center text-sm text-gray-400">
                  {newModelTableRef ? t('modeling.noColumnsDiscovered', 'No columns discovered for this table.') : t('modeling.selectTableFirst', 'Select a table first.')}
                </p>
              )}
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.selectPrimaryKey', 'Select primary key')}
            </label>
            <select
              value={newModelPrimaryKey}
              onChange={(e) => setNewModelPrimaryKey(e.target.value)}
              disabled={newModelColumns.length === 0}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
            >
              <option value="">{t('modeling.noPrimaryKey', 'No primary key')}</option>
              {newModelColumns.map((column) => (
                <option key={column} value={column}>{column}</option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setCreateModelOpen(false)
                setNewModelBindingId('')
                setNewModelTableRef('')
                setNewModelColumns([])
                setNewModelPrimaryKey('')
              }}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={submitCreateModel}
              loading={createModelMutation.isPending}
              disabled={!newModelBindingId || !newModelTableRef || newModelColumns.length === 0}
            >
              {t('common.create', 'Create')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={createViewInfoOpen}
        onClose={() => setCreateViewInfoOpen(false)}
        title={t('modeling.howToCreateView', 'How to create a View?')}
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-300">
            {t('modeling.createViewFromHome', 'Views are created from a query result. Ask a question on Home, review the generated SQL, then use Save as View from the answer result.')}
          </p>
          <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300">
            {t('modeling.viewDeployHint', 'After saving a view, return to Modeling to review and deploy it with the rest of the semantic model.')}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCreateViewInfoOpen(false)}>
              {t('common.close', 'Close')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={calculatedFieldModalOpen}
        onClose={() => {
          setCalculatedFieldModalOpen(false)
          setCalculatedFieldDraft(resetCalculatedFieldDraft())
        }}
        title={calculatedFieldDraft.id ? t('modeling.editCalculatedField', 'Edit calculated field') : t('modeling.createCalculatedField', 'Create calculated field')}
        size="lg"
      >
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.sourceModel', 'Source Model')}
            </label>
            <select
              value={calculatedFieldDraft.modelId}
              onChange={(e) =>
                setCalculatedFieldDraft((prev) => ({
                  ...prev,
                  modelId: e.target.value ? Number(e.target.value) : '',
                }))
              }
              disabled={Boolean(calculatedFieldDraft.id)}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
            >
              <option value="">{t('modeling.selectSourceModel', 'Select source model')}</option>
              {modelOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <Input
            label={t('modeling.name', 'Name')}
            value={calculatedFieldDraft.name}
            onChange={(e) => setCalculatedFieldDraft((prev) => ({ ...prev, name: e.target.value }))}
          />

          <Input
            label={t('modeling.displayName', 'Display Name')}
            value={calculatedFieldDraft.displayName}
            onChange={(e) => setCalculatedFieldDraft((prev) => ({ ...prev, displayName: e.target.value }))}
          />

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.expression', 'Expression')}
            </label>
            <textarea
              value={calculatedFieldDraft.expression}
              onChange={(e) => setCalculatedFieldDraft((prev) => ({ ...prev, expression: e.target.value }))}
              rows={5}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          <Input
            label={t('modeling.resultType', 'Result Type')}
            value={calculatedFieldDraft.resultType}
            onChange={(e) => setCalculatedFieldDraft((prev) => ({ ...prev, resultType: e.target.value }))}
          />

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.description', 'Description')}
            </label>
            <textarea
              value={calculatedFieldDraft.description}
              onChange={(e) => setCalculatedFieldDraft((prev) => ({ ...prev, description: e.target.value }))}
              rows={3}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setCalculatedFieldModalOpen(false)
                setCalculatedFieldDraft(resetCalculatedFieldDraft())
              }}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={submitCalculatedField}
              loading={createCalculatedFieldMutation.isPending || updateCalculatedFieldMutation.isPending}
              disabled={!calculatedFieldDraft.name.trim() || !calculatedFieldDraft.expression.trim() || !calculatedFieldDraft.modelId}
            >
              {calculatedFieldDraft.id ? t('common.save', 'Save') : t('common.create', 'Create')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={relationModalOpen}
        onClose={() => {
          setRelationModalOpen(false)
          setRelationDraft(resetRelationDraft())
        }}
        title={relationDraft.id ? t('modeling.editRelationship', 'Edit relationship') : t('modeling.createRelationship', 'Create relationship')}
        size="lg"
      >
        <div className="space-y-4">
          <Input
            label={t('modeling.name', 'Name')}
            value={relationDraft.name}
            onChange={(e) => setRelationDraft((prev) => ({ ...prev, name: e.target.value }))}
            placeholder={t('modeling.relationshipNameOptional', 'Optional - generated automatically if empty')}
          />

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.sourceModel', 'Source Model')}</label>
              <select
                value={relationDraft.sourceModelId}
                onChange={(e) =>
                setRelationDraft((prev) => ({
                  ...prev,
                    sourceModelId: e.target.value ? Number(e.target.value) : '',
                  }))
                }
                disabled={Boolean(relationDraft.id)}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
              >
                <option value="">{t('modeling.selectSourceModel', 'Select source model')}</option>
                {modelOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.sourceColumn', 'Source Column')}</label>
              <select
                value={relationDraft.sourceColumn}
                onChange={(e) => setRelationDraft((prev) => ({ ...prev, sourceColumn: e.target.value }))}
                disabled={Boolean(relationDraft.id) || relationColumnOptions.sourceColumns.length === 0}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
              >
                {relationColumnOptions.sourceColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}{column.isCalculated ? ' (fx)' : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.targetModel', 'Target Model')}</label>
              <select
                value={relationDraft.targetModelId}
                onChange={(e) =>
                setRelationDraft((prev) => ({
                  ...prev,
                    targetModelId: e.target.value ? Number(e.target.value) : '',
                  }))
                }
                disabled={Boolean(relationDraft.id)}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
              >
                <option value="">{t('modeling.selectTargetModel', 'Select target model')}</option>
                {modelOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.targetColumn', 'Target Column')}</label>
              <select
                value={relationDraft.targetColumn}
                onChange={(e) => setRelationDraft((prev) => ({ ...prev, targetColumn: e.target.value }))}
                disabled={Boolean(relationDraft.id) || relationColumnOptions.targetColumns.length === 0}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
              >
                {relationColumnOptions.targetColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}{column.isCalculated ? ' (fx)' : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">{t('modeling.relationType', 'Relation Type')}</label>
            <select
              value={relationDraft.relationType}
              onChange={(e) => setRelationDraft((prev) => ({ ...prev, relationType: e.target.value }))}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            >
              {RELATION_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('modeling.description', 'Description')}
            </label>
            <textarea
              value={relationDraft.description}
              onChange={(e) => setRelationDraft((prev) => ({ ...prev, description: e.target.value }))}
              rows={3}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setRelationModalOpen(false)
                setRelationDraft(resetRelationDraft())
              }}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={submitRelation}
              loading={createRelationMutation.isPending || updateRelationMutation.isPending}
              disabled={!relationDraft.sourceModelId || !relationDraft.targetModelId || !relationDraft.sourceColumn || !relationDraft.targetColumn}
            >
              {relationDraft.id ? t('common.save', 'Save') : t('common.create', 'Create')}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
