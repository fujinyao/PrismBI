'use client'

/* eslint-disable @next/next/no-img-element */

import { useMemo, useState, useEffect, useRef, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { modelingApi, projectsApi } from '@/lib/api'
import { useProjectStore } from '@/stores/projectStore'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'
import { TaskProgress, type TaskStep } from '@/components/ui/TaskProgress'
import { DATASOURCE_CONFIGS, getDatasourceConfig } from '@/lib/datasourceConfig'
import { SAMPLE_DATASET_LIST, SAMPLE_DATASETS, getInitSql, getSampleTableDetails } from '@/lib/sampleDatasets'
import { SAMPLE_RELATIONS } from '@/lib/sampleRelations'
import ConnectionForm from '@/components/setup/ConnectionForm'
import { EmptyState } from '@/components/ui/EmptyState'
import { saveSnapshot, loadSnapshot, clearSnapshot } from '@/lib/wizardState'
import { generateId } from '@/lib/utils'

type StepKey = 'mode' | 'models' | 'relations'
type ModelObjectKind = 'table' | 'view' | 'materialized_view' | 'other'

interface DatasourceBindingDraft {
  bindingId: number
  datasourceId: number
  name: string
  type: string
  sampleDatasetKey?: string
  alias?: string
  properties: Record<string, unknown>
}

interface ManualDatasourceDraft {
  key: string
  type: string
  name: string
  properties: Record<string, unknown>
  mappedProperties?: Record<string, unknown>
}

interface TableRecord {
  key: string
  bindingId: number
  datasourceName: string
  datasourceType: string
  tableName: string
  tableReference: string
  displayName?: string | null
  description?: string | null
  schema?: string | null
  tableType?: string | null
  columns: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]
}

interface DatasourceModelFilter {
  schemaKeyword: string
  tableKeyword: string
  activeKinds: ModelObjectKind[]
}

interface CreatedModelRecord {
  id: number
  name: string
  bindingId: number
  tableReference: string
  tableType?: string | null
  description?: string | null
  columns: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]
}

interface RelationDraftRecord {
  key: string
  source_model_id: number
  source_column: string
  target_model_id: number
  target_column: string
  relation_type: string
  description?: string | null
  source: 'sample' | 'recommended' | 'manual'
}

interface DiagramModel {
  id: number
  name: string
  table_reference?: string
  model_type?: string
  source_binding_id?: number
  description?: string | null
  fields?: { name: string; type: string; primaryKey?: boolean; description?: string | null; display_name?: string | null }[]
  column_defs?: { name: string; type: string; is_primary_key?: boolean; description?: string | null; display_name?: string | null }[]
}

interface DiagramRelation {
  id: number
  name?: string
  source_model_id: number
  source_column: string
  target_model_id: number
  target_column: string
  relation_type?: string
  type?: string
  description?: string | null
}

interface DiagramData {
  models: DiagramModel[]
  views: unknown[]
  relations: DiagramRelation[]
  calculated_fields: unknown[]
}

type WizardMode = 'manual' | 'sample' | null

type ModalDatasourceType = 'manual-selector' | string | null

type CreatingStage =
  | 'idle'
  | 'project'
  | 'datasource'
  | 'discovering'
  | 'models'
  | 'relations'

const STAGE_LABEL: Record<CreatingStage, string> = {
  idle: '',
  project: 'Creating project...',
  datasource: 'Saving data source...',
  discovering: 'Discovering tables...',
  models: 'Creating models...',
  relations: 'Creating relations...',
}

const STAGE_ORDER: CreatingStage[] = ['project', 'datasource', 'discovering', 'models', 'relations']

function getSetupSteps(currentStage: CreatingStage, t: (key: string, fallback?: string) => string): TaskStep[] {
  const stageIndex = STAGE_ORDER.indexOf(currentStage)
  return STAGE_ORDER.map((stage, i) => ({
    key: stage,
    title: t(`setup.stage_${stage}`, STAGE_LABEL[stage]),
    status: stage === 'idle' || currentStage === 'idle'
      ? 'pending' as const
      : i < stageIndex
        ? 'finished' as const
        : i === stageIndex
          ? 'running' as const
          : 'pending' as const,
  }))
}

const RELATION_TYPE_LABELS: { value: string; label: string }[] = [
  { value: 'MANY_TO_ONE', label: 'Many-to-One' },
  { value: 'ONE_TO_MANY', label: 'One-to-Many' },
  { value: 'ONE_TO_ONE', label: 'One-to-One' },
  { value: 'MANY_TO_MANY', label: 'Many-to-Many' },
]

const DEFAULT_RELATION_TYPE = 'MANY_TO_ONE'

const SAMPLE_DATASET_ORDER = ['ecommerce', 'hr', 'music', 'nba'] as const

const SAMPLE_DATASET_ICON_TEXT: Record<string, string> = {
  ecommerce: 'EC',
  hr: 'HR',
  music: 'MU',
  nba: 'NBA',
}

const RELATION_SOURCE_LABEL: Record<RelationDraftRecord['source'], string> = {
  sample: 'Sample',
  recommended: 'Recommended',
  manual: 'Manual',
}

const MODEL_KIND_ORDER: ModelObjectKind[] = ['table', 'view', 'materialized_view', 'other']

function orderModelKinds(kinds: ModelObjectKind[]): ModelObjectKind[] {
  return MODEL_KIND_ORDER.filter((kind) => kinds.includes(kind))
}

function normalizeModelObjectKind(value: unknown): ModelObjectKind {
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

function modelObjectKindLabel(
  kind: ModelObjectKind,
  t?: (key: string, fallback?: string) => string,
): string {
  if (kind === 'table') return t ? t('modeling.modelKind.table', 'Table') : 'Table'
  if (kind === 'view') return t ? t('modeling.modelKind.view', 'View') : 'View'
  if (kind === 'materialized_view') {
    return t ? t('modeling.modelKind.materialized_view', 'Materialized View') : 'Materialized View'
  }
  return t ? t('modeling.modelKind.other', 'Other') : 'Other'
}

function tableSchemaText(row: TableRecord): string {
  if (row.schema && String(row.schema).trim()) return String(row.schema).trim()
  const reference = String(row.tableReference || '')
  if (!reference.includes('.')) return ''
  return reference.split('.').slice(0, -1).join('.')
}

function ModelKindIcon({ kind, className = 'h-4 w-4' }: { kind: ModelObjectKind; className?: string }) {
  if (kind === 'view') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
    )
  }
  if (kind === 'materialized_view') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" />
        <path d="M3 12l9 4.5 9-4.5" />
        <path d="M3 16.5 12 21l9-4.5" />
      </svg>
    )
  }
  if (kind === 'other') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M12 2 4 6v12l8 4 8-4V6l-8-4Z" />
        <path d="m4 6 8 4 8-4" />
        <path d="M12 10v12" />
      </svg>
    )
  }
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M8 4v16" />
    </svg>
  )
}

function buildRelationKey(
  sourceModelId: number,
  sourceColumn: string,
  targetModelId: number,
  targetColumn: string,
): string {
  return `${sourceModelId}|${sourceColumn}|${targetModelId}|${targetColumn}`
}

function normalizeType(t: string): string {
  return String(t || '').toLowerCase()
}

function toModelName(reference: string): string {
  return reference.replace(/[^a-zA-Z0-9_]/g, '_')
}

function normalizeColumnDefs(columns: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]) {
  return columns.map((c) => ({
    name: c.name,
    type: c.type,
    is_primary_key: Boolean(c.is_primary_key),
    display_name: c.display_name ?? undefined,
    description: c.description ?? undefined,
  }))
}

function inferRelations(models: CreatedModelRecord[]) {
  const output: {
    name: string
    source_model_id: number
    source_column: string
    target_model_id: number
    target_column: string
    relation_type: string
    description?: string | null
    signature: string
  }[] = []

  const byName = new Map<string, CreatedModelRecord>()
  for (const model of models) {
    byName.set(model.name.toLowerCase(), model)
    const singular = model.name.endsWith('s') ? model.name.slice(0, -1) : model.name
    byName.set(singular.toLowerCase(), model)
  }

  for (const model of models) {
    for (const col of model.columns) {
      const colName = col.name.toLowerCase()
      if (!colName.endsWith('_id') && colName !== 'id') continue
      if (colName === 'id') continue

      const base = colName.slice(0, -3)
      const target = byName.get(base) || byName.get(`${base}s`)
      if (!target || target.id === model.id) continue

      const pk =
        target.columns.find((c) => c.name.toLowerCase() === 'id') ||
        target.columns.find((c) => c.is_primary_key) ||
        target.columns[0]
      if (!pk) continue

      const signature = [model.id, col.name, target.id, pk.name].join('|')
      if (output.some((r) => r.signature === signature)) continue

      output.push({
        name: `rel_${model.name}_${col.name}_${target.name}`,
        source_model_id: model.id,
        source_column: col.name,
        target_model_id: target.id,
        target_column: pk.name,
        relation_type: DEFAULT_RELATION_TYPE,
        description: `Recommended relationship from ${model.name}.${col.name} to ${target.name}.${pk.name}.`,
        signature,
      })
    }
  }

  return output
}

function resolveColumnName(
  columns: { name: string; type: string; is_primary_key?: boolean }[],
  expected: string,
): string {
  const exact = columns.find((c) => c.name === expected)
  if (exact) return exact.name
  const normalized = expected.toLowerCase()
  const caseInsensitive = columns.find((c) => c.name.toLowerCase() === normalized)
  if (caseInsensitive) return caseInsensitive.name
  return expected
}

function inferSampleRelations(
  models: CreatedModelRecord[],
  sampleDatasetKeys: string[],
): RelationDraftRecord[] {
  if (models.length === 0 || sampleDatasetKeys.length === 0) return []

  const modelLookup = new Map<string, CreatedModelRecord>()
  for (const model of models) {
    modelLookup.set(model.name.toLowerCase(), model)
    modelLookup.set(model.tableReference.toLowerCase(), model)
    modelLookup.set(toModelName(model.tableReference).toLowerCase(), model)
  }

  const uniqueDatasets = Array.from(new Set(sampleDatasetKeys))
  const output: RelationDraftRecord[] = []
  const seen = new Set<string>()

  for (const datasetKey of uniqueDatasets) {
    const relations = SAMPLE_RELATIONS[datasetKey] || []
    for (const relation of relations) {
      const sourceModel = modelLookup.get(relation.fromModelName.toLowerCase())
      const targetModel = modelLookup.get(relation.toModelName.toLowerCase())
      if (!sourceModel || !targetModel) continue

      const sourceColumn = resolveColumnName(sourceModel.columns || [], relation.fromColumnName)
      const targetColumn = resolveColumnName(targetModel.columns || [], relation.toColumnName)
      const key = buildRelationKey(sourceModel.id, sourceColumn, targetModel.id, targetColumn)
      if (seen.has(key)) continue
      seen.add(key)

      output.push({
        key,
        source_model_id: sourceModel.id,
        source_column: sourceColumn,
        target_model_id: targetModel.id,
        target_column: targetColumn,
        relation_type: relation.type,
        description: relation.description ?? `Sample relationship from ${sourceModel.name}.${sourceColumn} to ${targetModel.name}.${targetColumn}.`,
        source: 'sample',
      })
    }
  }

  return output
}

function buildDefaultNewRelation(models: CreatedModelRecord[]) {
  if (models.length === 0) {
    return {
      sourceModelId: '',
      sourceColumn: '',
      targetModelId: '',
      targetColumn: '',
      relationType: DEFAULT_RELATION_TYPE,
    }
  }

  const first = models[0]
  if (!first) {
    return {
      sourceModelId: '',
      sourceColumn: '',
      targetModelId: '',
      targetColumn: '',
      relationType: DEFAULT_RELATION_TYPE,
    }
  }
  const second = models[1] || first
  return {
    sourceModelId: String(first.id),
    sourceColumn: first.columns[0]?.name || '',
    targetModelId: String(second.id),
    targetColumn: second.columns[0]?.name || '',
    relationType: DEFAULT_RELATION_TYPE,
  }
}

function SetupPanel({
  title,
  subtitle,
  selected,
  onClick,
  children,
}: {
  title: string
  subtitle: string
  selected: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onClick()
        }
      }}
      className={`rounded-xl border p-5 text-left transition-all ${
        selected
          ? 'border-primary bg-primary-50 shadow-sm'
          : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
      }`}
    >
      <div className="mb-4">
        <p className="text-base font-semibold text-gray-900">{title}</p>
        <p className="mt-1 text-sm text-gray-600">{subtitle}</p>
      </div>
      {children}
    </div>
  )
}

export default function SetupConnectionPage() {
  const t = useI18nStore((s) => s.t)
  const router = useRouter()
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const switchProject = useProjectStore((s) => s.switchProject)
  const hasPermission = useAuthStore((s) => s.hasPermission)

  const [mode, setMode] = useState<WizardMode>(null)
  const [step, setStep] = useState<StepKey>('mode')

  const [projectName, setProjectName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')

  const [projectId, setProjectId] = useState<number | null>(null)
  const [datasources, setDatasources] = useState<DatasourceBindingDraft[]>([])
  const [selectedSampleDatasetKeys, setSelectedSampleDatasetKeys] = useState<Set<string>>(new Set())
  const [tableRecords, setTableRecords] = useState<TableRecord[]>([])
  const [selectedTableKeys, setSelectedTableKeys] = useState<Set<string>>(new Set())

  const [models, setModels] = useState<CreatedModelRecord[]>([])
  const [relationsDraft, setRelationsDraft] = useState<RelationDraftRecord[]>([])
  const [selectedRelationKeys, setSelectedRelationKeys] = useState<Set<string>>(new Set())
  const [editingRelationKey, setEditingRelationKey] = useState<string | null>(null)
  const [newRelation, setNewRelation] = useState<{
    sourceModelId: string
    sourceColumn: string
    targetModelId: string
    targetColumn: string
    relationType: string
  }>({
    sourceModelId: '',
    sourceColumn: '',
    targetModelId: '',
    targetColumn: '',
    relationType: DEFAULT_RELATION_TYPE,
  })

  const [stage, setStage] = useState<CreatingStage>('idle')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [idempotencyKey, setIdempotencyKey] = useState<string>(() => {
      const savedKey = typeof window !== 'undefined' ? localStorage.getItem('prismbi-setup-idempotency-key') : null
      if (savedKey) return savedKey
      const key = `setup-${generateId()}`
      if (typeof window !== 'undefined') localStorage.setItem('prismbi-setup-idempotency-key', key)
      return key
    })

  const [manualTableDrafts, setManualTableDrafts] = useState<
    Record<number, { name: string; reference: string }>
  >({})
  const [modelFiltersByDatasource, setModelFiltersByDatasource] = useState<Record<number, DatasourceModelFilter>>({})

  const [manualDraftDatasources, setManualDraftDatasources] = useState<ManualDatasourceDraft[]>([])
  const [selectedManualDatasourceKeys, setSelectedManualDatasourceKeys] = useState<Set<string>>(new Set())
  const [manualModalDatasourceType, setManualModalDatasourceType] = useState<ModalDatasourceType>(null)
  const [editingManualDatasourceKey, setEditingManualDatasourceKey] = useState<string | null>(null)
  const [resumeSnapshot, setResumeSnapshot] = useState<ReturnType<typeof loadSnapshot>>(null)

  useEffect(() => {
    const saved = loadSnapshot()
    if (saved && saved.projectId) {
      setResumeSnapshot(saved)
    }
  }, [])

  const handleResume = () => {
    if (!resumeSnapshot) return
    const s = resumeSnapshot

    setMode(s.mode)
    setProjectName(s.projectName)
    setDisplayName(s.displayName)
    setProjectDescription(s.projectDescription)
    setProjectId(s.projectId)
    setDatasources(s.datasources)
    setSelectedSampleDatasetKeys(new Set(s.selectedSampleDatasetKeys))
    setTableRecords(s.tableRecords)
    setSelectedTableKeys(new Set(s.selectedTableKeys))
    setModels(s.models)
    setRelationsDraft(s.relationsDraft)
    setSelectedRelationKeys(new Set(s.selectedRelationKeys))
    setManualDraftDatasources(s.manualDraftDatasources)
    setSelectedManualDatasourceKeys(new Set(s.selectedManualDatasourceKeys))
    setManualTableDrafts(s.manualTableDrafts ?? {})
    setModelFiltersByDatasource({})

    if (s.step === 'relations' || s.step === 'models') {
      setStep('relations')
    } else if (s.step === 'tables' || s.step === 'datasource') {
      setStep('models')
    } else {
      setStep('mode')
    }

    setResumeSnapshot(null)
    toast(t('setup.resumed', 'Resumed previous setup progress'), 'success')
  }

  const handleStartFresh = () => {
    clearSnapshot()
    setResumeSnapshot(null)
    setProjectId(null)
    setStep('mode')
    setMode(null)
    setProjectName('')
    setDisplayName('')
    setProjectDescription('')
    setDatasources([])
    setSelectedSampleDatasetKeys(new Set())
    setTableRecords([])
    setSelectedTableKeys(new Set())
    setModels([])
    setRelationsDraft([])
    setSelectedRelationKeys(new Set())
    setManualDraftDatasources([])
    setSelectedManualDatasourceKeys(new Set())
    setModelFiltersByDatasource({})
    const newKey = `setup-${generateId()}`
    setIdempotencyKey(newKey)
    if (typeof window !== 'undefined') {
      localStorage.removeItem('prismbi-setup-idempotency-key')
    }
  }

  const isSampleMode = mode === 'sample'

  const connectionTypes = useMemo(
    () =>
      Object.entries(DATASOURCE_CONFIGS).map(([key, config]) => ({
        key,
        displayName: config.displayName,
        icon: config.icon,
      })),
    [],
  )

  const sampleDatasetOptions = useMemo(
    () =>
      SAMPLE_DATASET_ORDER
        .map((key) => SAMPLE_DATASETS[key])
        .filter((dataset): dataset is (typeof SAMPLE_DATASET_LIST)[number] => Boolean(dataset)),
    [],
  )

  const relationsInTableOrder = useMemo(() => relationsDraft, [relationsDraft])

  const selectedRelationCount = useMemo(
    () => relationsInTableOrder.filter((relation) => selectedRelationKeys.has(relation.key)).length,
    [relationsInTableOrder, selectedRelationKeys],
  )

  const selectedTables = useMemo(
    () => tableRecords.filter((tr) => selectedTableKeys.has(tr.key)),
    [tableRecords, selectedTableKeys],
  )

  const selectedSampleDatasets = useMemo(() => {
    return sampleDatasetOptions.filter((dataset) => selectedSampleDatasetKeys.has(dataset.key))
  }, [sampleDatasetOptions, selectedSampleDatasetKeys])

  const selectedManualDatasourceCount = useMemo(
    () =>
      manualDraftDatasources.filter((datasource) => selectedManualDatasourceKeys.has(datasource.key)).length,
    [manualDraftDatasources, selectedManualDatasourceKeys],
  )

  const editingManualDatasource = useMemo(
    () =>
      editingManualDatasourceKey
        ? manualDraftDatasources.find((datasource) => datasource.key === editingManualDatasourceKey) || null
        : null,
    [editingManualDatasourceKey, manualDraftDatasources],
  )

  const manualModalActiveType = useMemo(() => {
    if (!manualModalDatasourceType || manualModalDatasourceType === 'manual-selector') return null
    return manualModalDatasourceType
  }, [manualModalDatasourceType])

  const tablesByDatasource = useMemo(() => {
    const map = new Map<number, TableRecord[]>()
    for (const tr of tableRecords) {
      const rows = map.get(tr.bindingId) || []
      rows.push(tr)
      map.set(tr.bindingId, rows)
    }
    return map
  }, [tableRecords])

  const modelNameMap = useMemo(() => {
    const map = new Map<number, string>()
    for (const m of models) map.set(m.id, m.name)
    return map
  }, [models])

  const modelColumnsMap = useMemo(() => {
    const map = new Map<number, { name: string; type: string; is_primary_key?: boolean }[]>()
    for (const m of models) {
      map.set(m.id, m.columns || [])
    }
    return map
  }, [models])

  const sourceColumnOptions = useMemo(() => {
    const id = Number(newRelation.sourceModelId)
    if (!id) return []
    return modelColumnsMap.get(id) || []
  }, [modelColumnsMap, newRelation.sourceModelId])

  const targetColumnOptions = useMemo(() => {
    const id = Number(newRelation.targetModelId)
    if (!id) return []
    return modelColumnsMap.get(id) || []
  }, [modelColumnsMap, newRelation.targetModelId])

  const createSampleProject = async (datasetKeys: string[]) => {
    if (busyRef.current) return
    if (mode === 'manual') return
    if (datasetKeys.length === 0) {
      toast(t('setup.selectSampleDataset', 'Select at least one sample dataset'), 'warning')
      return
    }

    const selectedDatasetKeySet = new Set(datasetKeys)
    const datasets = SAMPLE_DATASET_ORDER
      .filter((key) => selectedDatasetKeySet.has(key))
      .map((key) => SAMPLE_DATASETS[key])
      .filter((dataset): dataset is (typeof SAMPLE_DATASET_LIST)[number] => Boolean(dataset))

    if (datasets.length === 0) {
      toast(t('setup.selectSampleDataset', 'Select at least one sample dataset'), 'warning')
      return
    }

    busyRef.current = true; setBusy(true)
    let createdProjectId: number | null = null
    const nextDatasources: DatasourceBindingDraft[] = []
    const nextTables: TableRecord[] = []
    const nextSelected = new Set<string>()
    const selectedKeys = new Set<string>()
    try {
      const name = projectName.trim() || 'sample-project'
      setStage('project')
      const project = (await projectsApi.create({
        name,
        display_name:
          displayName.trim() || `Sample Project (${datasets.map((dataset) => dataset.displayName).join(', ')})`,
        description: projectDescription.trim() || undefined,
        type: 'sample',
      }, idempotencyKey)) as { id: number }

      createdProjectId = project.id
      setProjectId(project.id)
      await switchProject(project.id)
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['threads'] })

      for (const ds of datasets) {
        setStage('datasource')
        try {
        const reg = (await projectsApi.datasources.register(project.id, {
          name: `sample_${ds.key}`,
          type: 'duckdb',
          properties: {
            dbname: `sample_${ds.key}_${project.id}`,
            initSql: getInitSql(ds.key),
            displayName: ds.displayName,
            sampleTableDetails: getSampleTableDetails(ds.key),
          },
        })) as { id: number; bindingId: number }

        const draft: DatasourceBindingDraft = {
          bindingId: reg.bindingId,
          datasourceId: reg.id,
          name: `sample_${ds.key}`,
          sampleDatasetKey: ds.key,
          alias: ds.displayName,
          type: 'duckdb',
          properties: {
            dbname: `sample_${ds.key}_${project.id}`,
            initSql: getInitSql(ds.key),
            sampleTableDetails: getSampleTableDetails(ds.key),
          },
        }
        nextDatasources.push(draft)
        selectedKeys.add(ds.key)

        setStage('discovering')
        const discovered = (await projectsApi.datasources.tables(project.id, reg.bindingId)) as {
          tables: string[]
          table_details?: {
            name: string
            schema?: string | null
            reference?: string
            table_type?: string | null
            display_name?: string | null
            description?: string | null
            columns?: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]
          }[]
        }
        const details = discovered.table_details ?? []
        const records = details.length
          ? details.map((d) => ({
              key: `${reg.bindingId}:${d.reference || d.name}`,
              bindingId: reg.bindingId,
              datasourceName: ds.displayName,
              datasourceType: 'duckdb',
              tableName: d.name,
              tableReference: d.reference || d.name,
              tableType: d.table_type,
              displayName: d.display_name,
              description: d.description,
              schema: d.schema,
              columns: d.columns ?? [],
            }))
          : (discovered.tables || []).map((name) => ({
              key: `${reg.bindingId}:${name}`,
              bindingId: reg.bindingId,
              datasourceName: ds.displayName,
              datasourceType: 'duckdb',
              tableName: name,
              tableReference: name,
              tableType: 'table',
              description: null,
              schema: null,
              columns: [],
            }))
        for (const r of records) {
          nextTables.push(r)
          nextSelected.add(r.key)
        }
        } catch (dsErr) {
          const dsMsg = dsErr instanceof Error ? dsErr.message : String(dsErr)
          toast(t('setup.datasourceFailed', 'Datasource failed: ') + ds.displayName + ': ' + dsMsg, 'warning')
        }
      }

      if (nextDatasources.length === 0) {
        throw new Error(t('setup.allDatasourcesFailed', 'All datasources failed to load. Please check your connection and try again.'))
      }

      setDatasources(nextDatasources)
      setTableRecords(nextTables)
      setModelFiltersByDatasource({})
      setSelectedTableKeys(nextSelected)
      setModels([])
      setRelationsDraft([])
      setSelectedRelationKeys(new Set())
      setEditingRelationKey(null)
      setNewRelation(buildDefaultNewRelation([]))
      setSelectedSampleDatasetKeys(new Set(datasetKeys))
      setMode('sample')
      setStep('models')
      saveSnapshot({
        step: 'tables',
        mode: 'sample',
        projectName: projectName.trim(),
        displayName: displayName.trim(),
        projectDescription: projectDescription.trim(),
        projectId: project.id,
        datasources: nextDatasources,
        selectedSampleDatasetKeys: Array.from(datasetKeys),
        tableRecords: nextTables,
        selectedTableKeys: Array.from(nextSelected),
        models: [],
        relationsDraft: [],
        selectedRelationKeys: [],
        manualDraftDatasources: [],
        selectedManualDatasourceKeys: [],
          manualTableDrafts: {},
      })
      toast(t('setup.sampleProjectCreated', 'Sample project ready. Please confirm models and relations.'), 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : t('setup.failedToCreateProject', 'Failed to create project')
      toast(message, 'error')
      if (createdProjectId && nextDatasources.length > 0) {
        setProjectId(createdProjectId)
        setDatasources(nextDatasources)
        setSelectedSampleDatasetKeys(new Set(datasetKeys))
        setTableRecords(nextTables)
        setModelFiltersByDatasource({})
        setSelectedTableKeys(nextSelected)
        saveSnapshot({
          step: 'tables',
          mode: 'sample',
          projectName: projectName.trim(),
          displayName: displayName.trim(),
          projectDescription: projectDescription.trim(),
          projectId: createdProjectId,
          datasources: nextDatasources,
          selectedSampleDatasetKeys: Array.from(datasetKeys),
          tableRecords: nextTables,
          selectedTableKeys: Array.from(nextSelected),
          models: [],
          relationsDraft: [],
          selectedRelationKeys: [],
          manualDraftDatasources: [],
          selectedManualDatasourceKeys: [],
          manualTableDrafts: {},
        })
      }
    } finally {
      setStage('idle')
      busyRef.current = false; setBusy(false)
    }
  }

  const openAddManualDatasourceModal = () => {
    setEditingManualDatasourceKey(null)
    setManualModalDatasourceType('manual-selector')
    setMode('manual')
    setSelectedSampleDatasetKeys(new Set())
  }

  const toggleManualDatasourceSelection = (key: string) => {
    setMode('manual')
    setSelectedSampleDatasetKeys(new Set())
    setSelectedManualDatasourceKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const startEditManualDatasource = (key: string) => {
    const target = manualDraftDatasources.find((datasource) => datasource.key === key)
    if (!target) return
    setMode('manual')
    setSelectedSampleDatasetKeys(new Set())
    setEditingManualDatasourceKey(key)
    setManualModalDatasourceType(target.type)
  }

  const removeSelectedManualDatasources = () => {
    if (selectedManualDatasourceKeys.size === 0) return

    setManualDraftDatasources((prev) =>
      prev.filter((datasource) => !selectedManualDatasourceKeys.has(datasource.key)),
    )

    if (editingManualDatasourceKey && selectedManualDatasourceKeys.has(editingManualDatasourceKey)) {
      setEditingManualDatasourceKey(null)
      setManualModalDatasourceType('manual-selector')
    }

    setSelectedManualDatasourceKeys(new Set())
  }

  const closeManualDatasourceModal = () => {
    setManualModalDatasourceType(null)
    setEditingManualDatasourceKey(null)
  }

  const saveManualDatasourceDraft = (dsType: string, properties: Record<string, unknown>) => {
    const displayName = String(properties.displayName || getDatasourceConfig(dsType)?.displayName || dsType).trim()
    const config = getDatasourceConfig(dsType)
    const mappedProperties = config ? config.propertiesMapping(properties) : properties
    if (properties.displayName && !mappedProperties.displayName) {
      mappedProperties.displayName = properties.displayName
    }

    if (editingManualDatasourceKey) {
      setManualDraftDatasources((prev) =>
        prev.map((datasource) =>
          datasource.key === editingManualDatasourceKey
            ? {
                ...datasource,
                type: dsType,
                name: displayName,
                properties,
                mappedProperties,
              }
            : datasource,
        ),
      )
      setSelectedManualDatasourceKeys((prev) => {
        const next = new Set(prev)
        next.add(editingManualDatasourceKey)
        return next
      })
      closeManualDatasourceModal()
      return
    }

    const key = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const draft: ManualDatasourceDraft = {
      key,
      type: dsType,
      name: displayName,
      properties,
      mappedProperties,
    }
    setManualDraftDatasources((prev) => [...prev, draft])
    setSelectedManualDatasourceKeys((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
    closeManualDatasourceModal()
  }

  const createManualProjectFromSelection = async () => {
    if (busyRef.current) return
    if (!projectName.trim()) {
      toast(t('project.nameRequired', 'Project name is required'), 'warning')
      return
    }

    const selectedDrafts = manualDraftDatasources.filter((datasource) =>
      selectedManualDatasourceKeys.has(datasource.key),
    )

    if (selectedDrafts.length === 0) {
      toast(t('setup.selectDatasourceFirst', 'Select at least one datasource in Manual Setup'), 'warning')
      return
    }

    busyRef.current = true; setBusy(true)
    let createdProjectId: number | null = null
    const nextDatasources: DatasourceBindingDraft[] = []
    const nextTables: TableRecord[] = []
    try {
      setStage('project')
      const project = (await projectsApi.create({
        name: projectName.trim(),
        display_name: displayName.trim() || undefined,
        description: projectDescription.trim() || undefined,
      }, idempotencyKey)) as { id: number }

      createdProjectId = project.id
      setProjectId(project.id)
      await switchProject(project.id)
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['threads'] })

      for (const draft of selectedDrafts) {
        setStage('datasource')
        try {
        const mappedProperties = draft.mappedProperties || draft.properties
        const registered = (await projectsApi.datasources.register(project.id, {
          name: draft.name,
          type: draft.type,
          properties: mappedProperties,
        })) as { id: number; bindingId: number }

        const binding: DatasourceBindingDraft = {
          bindingId: registered.bindingId,
          datasourceId: registered.id,
          name: draft.name,
          type: draft.type,
          alias: String(draft.properties.displayName || draft.name),
          properties: mappedProperties,
        }
        nextDatasources.push(binding)

        setStage('discovering')
        const discovered = (await projectsApi.datasources.tables(project.id, registered.bindingId)) as {
          tables: string[]
          table_details?: {
            name: string
            schema?: string | null
            reference?: string
            table_type?: string | null
            display_name?: string | null
            description?: string | null
            columns?: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]
          }[]
          warning?: string
        }

        const details = discovered.table_details ?? []
        const records = details.length
          ? details.map((d) => ({
              key: `${registered.bindingId}:${d.reference || d.name}`,
              bindingId: registered.bindingId,
              datasourceName: binding.alias || binding.name,
              datasourceType: draft.type,
              tableName: d.name,
              tableReference: d.reference || d.name,
              tableType: d.table_type,
              displayName: d.display_name,
              description: d.description,
              schema: d.schema,
              columns: d.columns ?? [],
            }))
          : (discovered.tables || []).map((tableName) => ({
              key: `${registered.bindingId}:${tableName}`,
              bindingId: registered.bindingId,
              datasourceName: binding.alias || binding.name,
              datasourceType: draft.type,
              tableName,
              tableReference: tableName,
              tableType: 'table',
              description: null,
              schema: null,
              columns: [],
            }))
        nextTables.push(...records)

        if (discovered.warning) {
          toast(discovered.warning, 'warning')
        }
        } catch (dsErr) {
          const dsMsg = dsErr instanceof Error ? dsErr.message : String(dsErr)
          toast(t('setup.datasourceFailed', 'Datasource failed: ') + draft.name + ': ' + dsMsg, 'warning')
        }
      }

      if (nextDatasources.length === 0) {
        throw new Error(t('setup.allDatasourcesFailed', 'All datasources failed to load. Please check your connection and try again.'))
      }

      setDatasources(nextDatasources)
      setTableRecords(nextTables)
      setModelFiltersByDatasource({})
      setSelectedTableKeys(new Set())
      setModels([])
      setRelationsDraft([])
      setSelectedRelationKeys(new Set())
      setEditingRelationKey(null)
      setNewRelation(buildDefaultNewRelation([]))
      setMode('manual')
      setStep('models')
      saveSnapshot({
        step: 'tables',
        mode: 'manual',
        projectName: projectName.trim(),
        displayName: displayName.trim(),
        projectDescription: projectDescription.trim(),
        projectId: project.id,
        datasources: nextDatasources,
        selectedSampleDatasetKeys: [],
        tableRecords: nextTables,
        selectedTableKeys: [],
        models: [],
        relationsDraft: [],
        selectedRelationKeys: [],
        manualDraftDatasources: manualDraftDatasources,
        selectedManualDatasourceKeys: Array.from(selectedManualDatasourceKeys),
          manualTableDrafts: manualTableDrafts,
      })
      toast(t('setup.manualProjectReady', 'Manual project ready. Please select models and relations.'), 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : t('setup.failedToCreateProject', 'Failed to create project')
      toast(message, 'error')
      if (createdProjectId) {
        setProjectId(createdProjectId)
        setDatasources(nextDatasources)
        setTableRecords(nextTables)
        setModelFiltersByDatasource({})
        saveSnapshot({
          step: 'tables',
          mode: 'manual',
          projectName: projectName.trim(),
          displayName: displayName.trim(),
          projectDescription: projectDescription.trim(),
          projectId: createdProjectId,
          datasources: nextDatasources,
          selectedSampleDatasetKeys: [],
          tableRecords: nextTables,
          selectedTableKeys: [],
          models: [],
          relationsDraft: [],
          selectedRelationKeys: [],
          manualDraftDatasources,
          selectedManualDatasourceKeys: Array.from(selectedManualDatasourceKeys),
          manualTableDrafts: manualTableDrafts,
        })
      }
    } finally {
      setStage('idle')
      busyRef.current = false; setBusy(false)
    }
  }

  const proceedToModelsFromMode = async () => {
    if (busyRef.current) return
    if (!projectName.trim()) {
      toast(t('project.nameRequired', 'Project name is required'), 'warning')
      return
    }
    if (mode === 'manual') {
      await createManualProjectFromSelection()
      return
    }
    if (mode === null) {
      toast(t('setup.chooseSetupMode', 'Please choose Sample Project or Manual Setup first'), 'warning')
      return
    }
    await startSampleSetup()
  }

  const createModelsFromSelection = async () => {
    if (busyRef.current) return
    if (!projectId) {
      toast(t('setup.projectMissing', 'Project is not ready yet'), 'warning')
      return
    }
    if (selectedTables.length === 0) {
      toast(t('setup.selectAtLeastOneObject', 'Please select at least one modeling object'), 'warning')
      return
    }

    busyRef.current = true; setBusy(true)
    setStage('models')
    try {
      const existing = (await modelingApi.models.list(projectId)) as {
        id: number
        name: string
        source_binding_id?: number
        table_reference?: string
      }[]
      const existingMap = new Map<string, number>()
      const existingNameSet = new Set<string>()
      for (const m of existing) {
        const bindingPart = m.source_binding_id ?? ''
        const refPart = (m.table_reference || m.name).toLowerCase()
        const key = `${bindingPart}::${refPart}`
        existingMap.set(key, m.id)
        existingNameSet.add(m.name.toLowerCase())
      }

      const created: CreatedModelRecord[] = []
      const plannedNames = new Set<string>()
      for (const table of selectedTables) {
        const modelKey = `${table.bindingId}::${table.tableReference.toLowerCase()}`
        const baseModelName = toModelName(table.tableReference)
        let modelId: number | undefined
        let modelName = baseModelName

        const makeName = (base: string) => {
          const normalizedBase = base || 'model'
          let candidate = normalizedBase
          let index = 1
          while (
            existingNameSet.has(candidate.toLowerCase()) ||
            plannedNames.has(candidate.toLowerCase())
          ) {
            candidate = `${normalizedBase}_${index}`
            index += 1
          }
          return candidate
        }

        modelId = existingMap.get(modelKey)
        if (!modelId) {
          modelName = makeName(baseModelName)
          const createdModel = (await modelingApi.models.create(projectId, {
            name: modelName,
            display_name: table.displayName || table.tableName,
            description: table.description || undefined,
            table_reference: table.tableReference,
            model_type: normalizeModelObjectKind(table.tableType),
            source_binding_id: table.bindingId,
            columns: normalizeColumnDefs(table.columns),
          })) as { id: number }
          modelId = createdModel.id
          existingMap.set(modelKey, modelId)
          plannedNames.add(modelName.toLowerCase())
          existingNameSet.add(modelName.toLowerCase())
        } else {
          const matched = existing.find(
            (m) => m.id === modelId,
          )
          modelName = matched?.name || modelName
        }

        created.push({
          id: modelId,
          name: modelName,
          bindingId: table.bindingId,
          tableReference: table.tableReference,
          tableType: normalizeModelObjectKind(table.tableType),
          description: table.description,
          columns: table.columns,
        })
      }

      setModels(created)
      const inferred = inferRelations(created).map((r) => ({
        key: r.signature,
        source_model_id: r.source_model_id,
        source_column: r.source_column,
        target_model_id: r.target_model_id,
        target_column: r.target_column,
        relation_type: r.relation_type,
        description: r.description,
        source: 'recommended' as const,
      }))

      const sampleDatasetKeys = datasources
        .map((ds) => ds.sampleDatasetKey)
        .filter((key): key is string => Boolean(key))
      const sampleRelations = inferSampleRelations(created, sampleDatasetKeys)
      const nextRelations = isSampleMode
        ? (sampleRelations.length > 0 ? sampleRelations : inferred)
        : inferred

      setRelationsDraft(nextRelations)
      setSelectedRelationKeys(
        isSampleMode ? new Set(nextRelations.map((relation) => relation.key)) : new Set(),
      )
      setEditingRelationKey(null)
      setNewRelation(buildDefaultNewRelation(created))
      setStep('relations')
      saveSnapshot({
        step: 'models',
        mode: isSampleMode ? 'sample' : 'manual',
        projectName: projectName.trim(),
        displayName: displayName.trim(),
        projectDescription: projectDescription.trim(),
        projectId: projectId!,
        datasources: datasources,
        selectedSampleDatasetKeys: Array.from(selectedSampleDatasetKeys),
        tableRecords: tableRecords,
        selectedTableKeys: Array.from(selectedTableKeys),
        models: created,
        relationsDraft: nextRelations,
        selectedRelationKeys: isSampleMode ? nextRelations.map((r) => r.key) : [],
        manualDraftDatasources: manualDraftDatasources,
        selectedManualDatasourceKeys: Array.from(selectedManualDatasourceKeys),
          manualTableDrafts: manualTableDrafts,
      })
      toast(t('setup.modelsCreated', 'Models created successfully'), 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('setup.failedToCreateModels', 'Failed to create models'), 'error')
    } finally {
      busyRef.current = false; setBusy(false)
      setStage('idle')
    }
  }

  const finalizeSetup = async () => {
    if (busyRef.current) return
    if (!projectId) {
      toast(t('setup.projectMissing', 'Project is not ready yet'), 'warning')
      return
    }

    busyRef.current = true; setBusy(true)
    setStage('relations')
    try {
      const existingRelations = (await modelingApi.relations.list(projectId)) as DiagramRelation[]
      const existingSignature = new Set(
        existingRelations.map((r) =>
          [r.source_model_id, r.source_column, r.target_model_id, r.target_column].join('|'),
        ),
      )

      const selectedRelations = relationsDraft.filter((relation) => selectedRelationKeys.has(relation.key))

      for (const relation of selectedRelations) {
        const signature = [
          relation.source_model_id,
          relation.source_column,
          relation.target_model_id,
          relation.target_column,
        ].join('|')
        if (existingSignature.has(signature)) continue
        await modelingApi.relations.create(projectId, {
          name: `rel_${relation.source_model_id}_${relation.target_model_id}_${relation.source_column}_${relation.target_column}`,
          source_model_id: relation.source_model_id,
          source_column: relation.source_column,
          target_model_id: relation.target_model_id,
          target_column: relation.target_column,
          relation_type: relation.relation_type,
          description: relation.description || undefined,
        })
      }

      await switchProject(projectId)
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['threads'] })
      queryClient.invalidateQueries({ queryKey: ['modeling'] })
      queryClient.invalidateQueries({ queryKey: ['modeling', projectId] })
      queryClient.invalidateQueries({ queryKey: ['diagram'] })
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      queryClient.invalidateQueries({ queryKey: ['recommendations'] })
      queryClient.invalidateQueries({ queryKey: ['recommendations-onboarding'] })
      queryClient.invalidateQueries({ queryKey: ['dashboards'] })
      clearSnapshot()
      if (typeof window !== 'undefined') localStorage.removeItem('prismbi-setup-idempotency-key')
      toast(t('setup.complete', 'Setup complete!'), 'success')
      router.push('/modeling')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('setup.failedToSaveDiagram', 'Failed to save setup'), 'error')
    } finally {
      busyRef.current = false; setBusy(false)
      setStage('idle')
    }
  }

  const skipRelationsAndOpenModeling = async () => {
    if (busyRef.current) return
    if (!projectId) {
      toast(t('setup.projectMissing', 'Project is not ready yet'), 'warning')
      return
    }

    busyRef.current = true; setBusy(true)
    try {
      await switchProject(projectId)
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['diagram'] })
      queryClient.invalidateQueries({ queryKey: ['diagram', projectId] })
      router.push('/modeling')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('setup.failedToSaveDiagram', 'Failed to save setup'), 'error')
    } finally {
      busyRef.current = false; setBusy(false)
    }
  }

  const loadExistingProjectModels = async () => {
    if (!projectId) return
    const diagram = (await modelingApi.diagram(projectId)) as DiagramData
    const rows = (diagram.models || []).map((m) => ({
      id: m.id,
      name: m.name,
      bindingId: m.source_binding_id || 0,
      tableReference: m.table_reference || m.name,
      tableType: m.model_type || 'table',
      columns:
        m.column_defs?.map((c) => ({ name: c.name, type: c.type, is_primary_key: c.is_primary_key })) ||
        m.fields?.map((f) => ({ name: f.name, type: f.type, is_primary_key: f.primaryKey, description: f.description, display_name: f.display_name })) ||
        [],
    }))
    setModels(rows)

    const rels = (diagram.relations || []).map((r) => ({
      key: buildRelationKey(r.source_model_id, r.source_column, r.target_model_id, r.target_column),
      source_model_id: r.source_model_id,
      source_column: r.source_column,
      target_model_id: r.target_model_id,
      target_column: r.target_column,
      relation_type: r.relation_type || r.type || DEFAULT_RELATION_TYPE,
      description: r.description,
      source: 'recommended' as const,
    }))
    setRelationsDraft(rels)
    setSelectedRelationKeys((prev) => {
      const valid = new Set(rels.map((relation) => relation.key))
      if (isSampleMode) return valid
      return new Set(Array.from(prev).filter((key) => valid.has(key)))
    })
    setEditingRelationKey(null)

    setNewRelation(buildDefaultNewRelation(rows))
  }

  const addRelationDraft = () => {
    const sourceModelId = Number(newRelation.sourceModelId)
    const targetModelId = Number(newRelation.targetModelId)
    const sourceColumn = newRelation.sourceColumn
    const targetColumn = newRelation.targetColumn
    const relationType = newRelation.relationType || DEFAULT_RELATION_TYPE

    if (!sourceModelId || !targetModelId) {
      toast(t('setup.selectModelsForRelation', 'Please select source and target models'), 'warning')
      return
    }
    if (!sourceColumn || !targetColumn) {
      toast(t('setup.selectColumnsForRelation', 'Please select source and target columns'), 'warning')
      return
    }

    const key = buildRelationKey(sourceModelId, sourceColumn, targetModelId, targetColumn)

    if (editingRelationKey) {
      const editingRelation = relationsDraft.find((relation) => relation.key === editingRelationKey)
      if (!editingRelation) {
        setEditingRelationKey(null)
        return
      }

      if (key !== editingRelationKey && relationsDraft.some((relation) => relation.key === key)) {
        toast(t('setup.relationExists', 'This relationship already exists'), 'warning')
        return
      }

      setRelationsDraft((prev) =>
        prev.map((relation) =>
          relation.key === editingRelationKey
            ? {
                ...relation,
                key,
                source_model_id: sourceModelId,
                source_column: sourceColumn,
                target_model_id: targetModelId,
                target_column: targetColumn,
                relation_type: relationType,
                description: relation.description,
              }
            : relation,
        ),
      )
      setSelectedRelationKeys((prev) => {
        const next = new Set(prev)
        next.delete(editingRelationKey)
        next.add(key)
        return next
      })
      setEditingRelationKey(null)
      setNewRelation(buildDefaultNewRelation(models))
      return
    }

    if (relationsDraft.some((r) => r.key === key)) {
      toast(t('setup.relationExists', 'This relationship already exists'), 'warning')
      return
    }

    setRelationsDraft((prev) => [
      ...prev,
      {
        key,
        source_model_id: sourceModelId,
        source_column: sourceColumn,
        target_model_id: targetModelId,
        target_column: targetColumn,
        relation_type: relationType,
        description: `Manual relationship from ${modelNameMap.get(sourceModelId) || sourceModelId}.${sourceColumn} to ${modelNameMap.get(targetModelId) || targetModelId}.${targetColumn}.`,
        source: 'manual',
      },
    ])
    setSelectedRelationKeys((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
    setNewRelation(buildDefaultNewRelation(models))
  }

  const selectAllRelations = () => {
    setSelectedRelationKeys(new Set(relationsDraft.map((relation) => relation.key)))
  }

  const deselectAllRelations = () => {
    setSelectedRelationKeys(new Set())
  }

  const invertRelationSelection = () => {
    setSelectedRelationKeys((prev) => {
      const next = new Set<string>()
      for (const relation of relationsDraft) {
        if (!prev.has(relation.key)) next.add(relation.key)
      }
      return next
    })
  }

  const toggleRelationSelection = (relationKey: string, checked: boolean) => {
    setSelectedRelationKeys((prev) => {
      const next = new Set(prev)
      if (checked) next.add(relationKey)
      else next.delete(relationKey)
      return next
    })
  }

  const startEditRelation = (relation: RelationDraftRecord) => {
    setEditingRelationKey(relation.key)
    setNewRelation({
      sourceModelId: String(relation.source_model_id),
      sourceColumn: relation.source_column,
      targetModelId: String(relation.target_model_id),
      targetColumn: relation.target_column,
      relationType: relation.relation_type,
    })
  }

  const cancelEditRelation = () => {
    setEditingRelationKey(null)
    setNewRelation(buildDefaultNewRelation(models))
  }

  const removeRelationDraft = (relationKey: string) => {
    setRelationsDraft((prev) => prev.filter((relation) => relation.key !== relationKey))
    setSelectedRelationKeys((prev) => {
      const next = new Set(prev)
      next.delete(relationKey)
      return next
    })
    if (editingRelationKey === relationKey) {
      cancelEditRelation()
    }
  }

  const startSampleSetup = async () => {
    if (busyRef.current) return
    if (!projectName.trim()) {
      toast(t('project.nameRequired', 'Project name is required'), 'warning')
      return
    }

    await createSampleProject(Array.from(selectedSampleDatasetKeys))
  }

  const toggleSampleDataset = (datasetKey: string) => {
    setMode('sample')
    setSelectedManualDatasourceKeys(new Set())
    setSelectedSampleDatasetKeys((prev) => {
      const next = new Set(prev)
      if (next.has(datasetKey)) next.delete(datasetKey)
      else next.add(datasetKey)
      return next
    })
  }

  const relationMode: 'sample' | 'manual' =
    isSampleMode || datasources.some((datasource) => Boolean(datasource.sampleDatasetKey))
      ? 'sample'
      : 'manual'

  const activateManualMode = () => {
    setMode('manual')
    setSelectedSampleDatasetKeys(new Set())
  }

  const activateSampleMode = () => {
    setMode('sample')
    setSelectedManualDatasourceKeys(new Set())
  }

  const renderModeStep = () => (
    <>
      <h1 className="mb-2 text-2xl font-semibold">{t('setup.connectionTitle', 'Create Project')}</h1>
      <p className="mb-6 text-gray-500">
        {t(
          'setup.connectionDescription',
          'Choose sample project or manually connect multiple datasources, then model tables and relationships.',
        )}
      </p>

      <div className="mb-6 space-y-4">
        <Input
          label={t('project.name', 'Project Name')}
          value={projectName}
          onChange={(e) => setProjectName(e.target.value)}
          placeholder="my-project"
          disabled={busy}
        />
        <Input
          label={t('project.displayName', 'Display Name')}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder={t('project.displayNamePlaceholder', 'Enter a display name for this project')}
          disabled={busy}
        />
        <div>
          <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
            {t('project.description', 'Project Description')}
          </label>
          <textarea
            value={projectDescription}
            onChange={(e) => setProjectDescription(e.target.value)}
            rows={3}
            className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900"
            placeholder={t('project.descriptionPlaceholder', 'Describe the business domain, key metrics, and intended users of this project.')}
            disabled={busy}
          />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <SetupPanel
          title={t('setup.manualProject', 'Manual Setup')}
          subtitle={t(
            'setup.manualProjectDesc',
            'Add one or more datasources, then pick tables and relationships.',
          )}
          selected={mode === 'manual'}
          onClick={activateManualMode}
        >
          <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={openAddManualDatasourceModal}
                disabled={busy || mode === 'sample'}
              >
                {t('setup.addDatasource', 'Add DataSource')}
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={removeSelectedManualDatasources}
                disabled={busy || mode === 'sample' || selectedManualDatasourceCount === 0}
              >
                {t('setup.removeDatasource', 'Remove Datasource')}
              </Button>
            </div>

            {manualDraftDatasources.length === 0 ? (
              <p className="rounded-lg border border-dashed border-gray-300 px-3 py-4 text-xs text-gray-500">
                {t('setup.noManualDatasource', 'No datasource in Manual Setup yet.')}
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                {manualDraftDatasources.map((datasource) => {
                  const selected = selectedManualDatasourceKeys.has(datasource.key)
                  const icon = getDatasourceConfig(datasource.type)?.icon
                  return (
                    <button
                      key={datasource.key}
                      type="button"
                      onClick={() => toggleManualDatasourceSelection(datasource.key)}
                      onDoubleClick={() => startEditManualDatasource(datasource.key)}
                      disabled={busy || mode === 'sample'}
                      className={`rounded-lg border px-3 py-2 text-left transition-colors disabled:opacity-60 ${
                        selected
                          ? 'border-blue-500 bg-blue-100 shadow-sm'
                          : 'border-gray-200 bg-white hover:border-blue-300'
                      }`}
                    >
                      <div className="mb-1 flex items-center gap-2">
                        {icon ? (
                          <img src={icon} alt={datasource.type} className="h-5 w-5 object-contain" />
                        ) : (
                          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-blue-600 text-[10px] font-semibold text-white">
                            {datasource.type.slice(0, 2).toUpperCase()}
                          </span>
                        )}
                        <span className="truncate text-xs font-semibold text-gray-900">{datasource.name}</span>
                      </div>
                      <p className="truncate text-[11px] text-gray-500">{datasource.type}</p>
                    </button>
                  )
                })}
              </div>
            )}
            {manualDraftDatasources.length > 0 && (
              <p className="text-xs text-gray-500">
                {selectedManualDatasourceCount}/{manualDraftDatasources.length}{' '}
                {t('setup.datasourcesSelected', 'datasources selected')}
              </p>
            )}
          </div>
        </SetupPanel>

        <SetupPanel
          title={t('setup.sampleProject', 'Sample Project')}
          subtitle={t(
            'setup.sampleProjectDesc',
            'Choose one or more sample datasources, then continue with model and relationship review.',
          )}
          selected={mode === 'sample'}
          onClick={activateSampleMode}
        >
          <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="grid grid-cols-2 gap-3">
              {sampleDatasetOptions.map((dataset) => {
                const selected = selectedSampleDatasetKeys.has(dataset.key)
                return (
                  <button
                    key={dataset.key}
                    type="button"
                    onClick={() => toggleSampleDataset(dataset.key)}
                    disabled={busy || mode === 'manual'}
                    className={`rounded-lg border p-3 text-left transition-colors disabled:opacity-60 ${
                      selected
                        ? 'border-green-500 bg-green-100 shadow-sm'
                        : 'border-gray-200 bg-white hover:border-green-300'
                    }`}
                  >
                    <div className="mb-2 flex items-center gap-2">
                      <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-green-600 text-xs font-semibold text-white">
                        {SAMPLE_DATASET_ICON_TEXT[dataset.key] || dataset.displayName.slice(0, 2).toUpperCase()}
                      </span>
                      <span className="text-sm font-semibold text-gray-900">{dataset.displayName}</span>
                    </div>
                    <p className="text-xs text-gray-600">{dataset.tableCount} tables</p>
                  </button>
                )
              })}
            </div>

            {selectedSampleDatasets.length > 0 && (
              <p className="text-xs text-gray-600">
                {selectedSampleDatasets.map((dataset) => dataset.displayName).join(', ')}
              </p>
            )}
          </div>
        </SetupPanel>
      </div>

      <div className="mt-6 flex justify-between">
        <Button variant="secondary" onClick={() => router.push('/modeling')} disabled={busy}>
          {t('setup.backToMain', 'Back to Main')}
        </Button>
        <Button
          variant="primary"
          onClick={proceedToModelsFromMode}
          disabled={
            busy ||
            mode === null ||
            (mode === 'manual' && selectedManualDatasourceCount === 0) ||
            (mode === 'sample' && selectedSampleDatasetKeys.size === 0)
          }
        >
          {t('setup.nextModels', 'Next: Select Models')}
        </Button>
      </div>

      {manualModalDatasourceType && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 px-4">
          <div className="w-full max-w-2xl rounded-xl bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {manualModalActiveType && getDatasourceConfig(manualModalActiveType)?.icon && (
                  <img
                    src={getDatasourceConfig(manualModalActiveType)?.icon}
                    alt={getDatasourceConfig(manualModalActiveType)?.displayName}
                    className="h-6 w-6"
                  />
                )}
                <p className="text-base font-semibold text-gray-900">
                  {editingManualDatasource
                    ? t('setup.editDatasource', 'Edit datasource')
                    : t('setup.addDatasource', 'Add datasource')}
                </p>
              </div>
              <Button size="sm" variant="secondary" onClick={closeManualDatasourceModal}>
                {t('common.cancel', 'Cancel')}
              </Button>
            </div>

            {manualModalDatasourceType === 'manual-selector' && (
              <div className="mb-4">
                <p className="mb-2 text-sm font-medium text-gray-700">
                  {t('setup.selectDatasourceType', 'Select datasource type')}
                </p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                  {connectionTypes.map((ds) => (
                    <button
                      key={ds.key}
                      type="button"
                      onClick={() => setManualModalDatasourceType(ds.key)}
                      className={`rounded-lg border p-3 text-center text-xs transition-colors ${
                        ds.key === manualModalActiveType
                          ? 'border-primary bg-primary-50'
                          : 'border-gray-200 hover:border-primary'
                      }`}
                    >
                      <img src={ds.icon} alt={ds.displayName} className="mx-auto mb-1 h-7 w-7 object-contain" />
                      {ds.displayName}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {manualModalActiveType ? (
              <ConnectionForm
                dsType={manualModalActiveType}
                preserveDisplayName
                skipMapping
                initialValues={editingManualDatasource?.properties}
                submitLabel={
                  editingManualDatasource
                    ? t('setup.saveDatasource', 'Save Datasource')
                    : t('setup.addDatasource', 'Add Datasource')
                }
                loading={busy}
                onSubmit={(properties) => {
                  saveManualDatasourceDraft(manualModalActiveType, properties)
                }}
              />
            ) : (
              <div className="rounded-lg border border-dashed border-gray-300 px-4 py-8 text-center text-sm text-gray-500">
                {t('setup.chooseDatasourceTypeFirst', 'Choose a datasource type to continue')}
              </div>
            )}
          </div>
        </div>
      )}

      {busy && stage !== 'idle' && (
        <div className="mt-6">
          <TaskProgress steps={getSetupSteps(stage, t)} />
        </div>
      )}
    </>
  )

  const renderModelsStep = () => (
    <>
      <h1 className="mb-2 text-2xl font-semibold">{t('setup.modelObjectsTitle', 'Step 2: Select Modeling Objects')}</h1>
      <p className="mb-6 text-gray-500">
        {t(
          'setup.modelObjectsDescription',
          'Select tables or views across all datasources. Each selected object becomes one model with datasource binding.',
        )}
      </p>

      {tableRecords.length === 0 ? (
        <EmptyState
          message={t('setup.noModelObjects', 'No modeling objects available. Add at least one datasource first.')}
          action={{
            label: t('setup.backConnection', 'Back to datasources'),
            onClick: () => setStep('mode'),
          }}
        />
      ) : (
        <div className="mb-6 space-y-4">
          {datasources.map((ds) => {
            const rows = tablesByDatasource.get(ds.bindingId) || []
            const availableKinds = orderModelKinds(
              Array.from(new Set(rows.map((row) => normalizeModelObjectKind(row.tableType)))),
            )
            const currentFilter = modelFiltersByDatasource[ds.bindingId]
            const activeKinds = currentFilter
              ? orderModelKinds(currentFilter.activeKinds.filter((kind) => availableKinds.includes(kind)))
              : availableKinds
            const activeKindSet = new Set(activeKinds)
            const schemaKeyword = (currentFilter?.schemaKeyword || '').trim().toLowerCase()
            const tableKeyword = (currentFilter?.tableKeyword || '').trim().toLowerCase()
            const filteredRows = rows.filter((row) => {
              const rowKind = normalizeModelObjectKind(row.tableType)
              if (!activeKindSet.has(rowKind)) return false
              const schemaText = tableSchemaText(row).toLowerCase()
              if (schemaKeyword && !schemaText.includes(schemaKeyword)) return false
              const tableText = `${row.tableName} ${row.tableReference}`.toLowerCase()
              if (tableKeyword && !tableText.includes(tableKeyword)) return false
              return true
            })
            const allFilteredSelected =
              filteredRows.length > 0 && filteredRows.every((row) => selectedTableKeys.has(row.key))
            return (
              <div key={ds.bindingId} className="rounded-lg border border-gray-200">
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2.5">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{ds.alias || ds.name}</p>
                    <p className="text-xs text-gray-500">{ds.type}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setSelectedTableKeys((prev) => {
                        const next = new Set(prev)
                        if (allFilteredSelected) {
                          for (const row of filteredRows) next.delete(row.key)
                        } else {
                          for (const row of filteredRows) next.add(row.key)
                        }
                        return next
                      })
                    }}
                    disabled={filteredRows.length === 0}
                  >
                    {allFilteredSelected
                      ? t('setup.deselectAll', 'Deselect All')
                      : t('setup.selectAll', 'Select All')}
                  </Button>
                </div>

                <div className="grid gap-2 border-b border-gray-100 bg-gray-50 px-4 py-3 md:grid-cols-[1fr_1fr_auto]">
                  <Input
                    value={currentFilter?.schemaKeyword || ''}
                    onChange={(e) =>
                      setModelFiltersByDatasource((prev) => {
                        const existing = prev[ds.bindingId] || {
                          schemaKeyword: '',
                          tableKeyword: '',
                          activeKinds: availableKinds,
                        }
                        return {
                          ...prev,
                          [ds.bindingId]: {
                            ...existing,
                            schemaKeyword: e.target.value,
                          },
                        }
                      })
                    }
                    placeholder={t('setup.filterSchemaPlaceholder', 'Filter schema name')}
                  />
                  <Input
                    value={currentFilter?.tableKeyword || ''}
                    onChange={(e) =>
                      setModelFiltersByDatasource((prev) => {
                        const existing = prev[ds.bindingId] || {
                          schemaKeyword: '',
                          tableKeyword: '',
                          activeKinds: availableKinds,
                        }
                        return {
                          ...prev,
                          [ds.bindingId]: {
                            ...existing,
                            tableKeyword: e.target.value,
                          },
                        }
                      })
                    }
                    placeholder={t('setup.filterModelPlaceholder', 'Filter table/view name')}
                  />
                  <div className="flex items-center gap-1">
                    {MODEL_KIND_ORDER.map((kind) => {
                      const hasKind = availableKinds.includes(kind)
                      const selectedKind = activeKindSet.has(kind)
                      return (
                        <button
                          key={kind}
                          type="button"
                          title={modelObjectKindLabel(kind, t)}
                          onClick={() => {
                            if (!hasKind) return
                            setModelFiltersByDatasource((prev) => {
                              const existing = prev[ds.bindingId] || {
                                schemaKeyword: '',
                                tableKeyword: '',
                                activeKinds: availableKinds,
                              }
                              const currentKinds = orderModelKinds(
                                existing.activeKinds.filter((item) => availableKinds.includes(item)),
                              )
                              const nextKinds = currentKinds.includes(kind)
                                ? currentKinds.filter((item) => item !== kind)
                                : orderModelKinds([...currentKinds, kind])
                              return {
                                ...prev,
                                [ds.bindingId]: {
                                  ...existing,
                                  activeKinds: nextKinds,
                                },
                              }
                            })
                          }}
                          disabled={!hasKind}
                          className={`inline-flex h-9 w-9 items-center justify-center rounded border text-xs transition-colors ${
                            !hasKind
                              ? 'cursor-not-allowed border-gray-200 bg-gray-100 text-gray-300'
                              : selectedKind
                                ? 'border-blue-500 bg-blue-50 text-blue-700'
                                : 'border-gray-300 bg-white text-gray-500 hover:border-blue-300 hover:text-blue-600'
                          }`}
                        >
                          <ModelKindIcon kind={kind} />
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="max-h-72 overflow-auto">
                  {rows.length === 0 ? (
                    <p className="px-4 py-3 text-sm text-gray-500">
                      {t('setup.noModelObjectsDiscovered', 'No modeling objects discovered.')}
                    </p>
                  ) : filteredRows.length === 0 ? (
                    <p className="px-4 py-3 text-sm text-gray-500">
                      {t('setup.noTablesMatchedFilter', 'No models match the current filters.')}
                    </p>
                  ) : (
                    filteredRows.map((table) => {
                      const checked = selectedTableKeys.has(table.key)
                      const rowKind = normalizeModelObjectKind(table.tableType)
                      return (
                        <label
                          key={table.key}
                          className={`flex cursor-pointer items-start gap-3 border-b border-gray-100 px-4 py-3 hover:bg-gray-50 ${
                            checked ? 'bg-blue-50' : ''
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              setSelectedTableKeys((prev) => {
                                const next = new Set(prev)
                                if (e.target.checked) next.add(table.key)
                                else next.delete(table.key)
                                return next
                              })
                            }}
                            className="mt-1"
                          />
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="inline-flex h-5 w-5 items-center justify-center rounded border border-gray-200 bg-white text-gray-500">
                                <ModelKindIcon kind={rowKind} className="h-3.5 w-3.5" />
                              </span>
                              <p className="truncate text-sm font-medium text-gray-900">{table.tableReference}</p>
                            </div>
                            <p className="truncate text-xs text-gray-500">
                              {`${modelObjectKindLabel(rowKind, t)} · `}
                              {table.columns.length > 0
                                ? t('setup.columnsCount', '{count} columns', { count: String(table.columns.length) })
                                : t('setup.columnsMetadataUnavailable', 'Columns metadata unavailable')}
                            </p>
                          </div>
                        </label>
                      )
                    })
                  )}
                </div>
              </div>
            )
          })}

          <p className="text-sm text-gray-500">
            {selectedTableKeys.size}/{tableRecords.length} {t('setup.objectsSelected', 'objects selected')}
          </p>
        </div>
      )}

      {busy && stage !== 'idle' && (
        <div className="mb-6 rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm text-blue-700">
          {STAGE_LABEL[stage]}
        </div>
      )}

      <div className="flex justify-between">
        <Button variant="secondary" onClick={() => setStep('mode')} disabled={busy}>
          {t('setup.backConnection', 'Back: Connection')}
        </Button>
        <Button
          variant="primary"
          onClick={createModelsFromSelection}
          disabled={busy || selectedTableKeys.size === 0}
          loading={busy && stage === 'models'}
        >
          {t('setup.nextRelations', 'Next: Define Relationships')}
        </Button>
      </div>
    </>
  )

  const renderRelationsStep = () => (
    <>
      <h1 className="mb-2 text-2xl font-semibold">{t('setup.relationshipsTitle', 'Step 3: Define Relationships')}</h1>
      <p className="mb-6 text-gray-500">
        {relationMode === 'sample'
          ? t(
              'setup.relationshipsDescriptionSample',
              'Sample relationships are selected by default. You can edit, add, or remove before finishing setup.',
            )
          : t(
              'setup.relationshipsDescriptionManual',
              'Review recommended relationships. Nothing is selected by default; choose what to apply.',
            )}
      </p>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
        <p className="text-sm text-gray-700">
          {selectedRelationCount}/{relationsInTableOrder.length}{' '}
          {t('setup.relationsSelected', 'relationships selected')}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={selectAllRelations}
            disabled={relationsInTableOrder.length === 0}
          >
            {t('setup.selectAll', 'Select All')}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={deselectAllRelations}
            disabled={selectedRelationCount === 0}
          >
            {t('setup.deselectAll', 'Deselect All')}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={invertRelationSelection}
            disabled={relationsInTableOrder.length === 0}
          >
            {t('setup.invertSelection', 'Invert Selection')}
          </Button>
        </div>
      </div>

      <div className="mb-6 rounded-lg border border-gray-200">
        <div className="border-b border-gray-200 px-4 py-3">
          <p className="text-sm font-semibold text-gray-800">
            {t('setup.relations', 'Relationships')} ({relationsInTableOrder.length})
          </p>
        </div>

        <div className="border-b border-gray-200 px-4 py-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
              {editingRelationKey
                ? t('setup.editRelationTitle', 'Edit Relationship')
                : t('setup.addRelationTitle', 'Add Relationship')}
            </p>
            {editingRelationKey && (
              <Button size="sm" variant="secondary" onClick={cancelEditRelation}>
                {t('common.cancel', 'Cancel')}
              </Button>
            )}
          </div>

          <div className="grid gap-3 md:grid-cols-5">
            <select
              value={newRelation.sourceModelId}
              onChange={(e) => {
                const nextModelId = e.target.value
                const cols = modelColumnsMap.get(Number(nextModelId)) || []
                setNewRelation((prev) => ({
                  ...prev,
                  sourceModelId: nextModelId,
                  sourceColumn: cols[0]?.name || '',
                }))
              }}
              className="rounded border border-gray-200 px-2 py-2 text-sm"
            >
              <option value="">Source model</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>

            <select
              value={newRelation.sourceColumn}
              onChange={(e) => setNewRelation((prev) => ({ ...prev, sourceColumn: e.target.value }))}
              className="rounded border border-gray-200 px-2 py-2 text-sm"
            >
              <option value="">Source column</option>
              {sourceColumnOptions.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>

            <select
              value={newRelation.targetModelId}
              onChange={(e) => {
                const nextModelId = e.target.value
                const cols = modelColumnsMap.get(Number(nextModelId)) || []
                setNewRelation((prev) => ({
                  ...prev,
                  targetModelId: nextModelId,
                  targetColumn: cols[0]?.name || '',
                }))
              }}
              className="rounded border border-gray-200 px-2 py-2 text-sm"
            >
              <option value="">Target model</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>

            <select
              value={newRelation.targetColumn}
              onChange={(e) => setNewRelation((prev) => ({ ...prev, targetColumn: e.target.value }))}
              className="rounded border border-gray-200 px-2 py-2 text-sm"
            >
              <option value="">Target column</option>
              {targetColumnOptions.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>

            <div className="flex gap-2">
              <select
                value={newRelation.relationType}
                onChange={(e) =>
                  setNewRelation((prev) => ({ ...prev, relationType: e.target.value }))
                }
                className="flex-1 rounded border border-gray-200 px-2 py-2 text-sm"
              >
                {RELATION_TYPE_LABELS.map((rt) => (
                  <option key={rt.value} value={rt.value}>
                    {rt.label}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="secondary" onClick={addRelationDraft}>
                {editingRelationKey ? t('common.save', 'Save') : t('common.add', 'Add')}
              </Button>
            </div>
          </div>
        </div>

        {relationsInTableOrder.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-gray-500">
            {t('setup.noRelationsYet', 'No inferred relationships. You can finish and add them later.')}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-2">Select</th>
                  <th className="px-4 py-2">Source</th>
                  <th className="px-4 py-2">Source Column</th>
                  <th className="px-4 py-2">Target</th>
                  <th className="px-4 py-2">Target Column</th>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2">Origin</th>
                  <th className="px-4 py-2">Action</th>
                </tr>
              </thead>
              <tbody>
                {relationsInTableOrder.map((relation) => (
                  <tr
                    key={relation.key}
                    className={`border-t border-gray-100 ${
                      selectedRelationKeys.has(relation.key) ? 'bg-blue-50/60' : ''
                    }`}
                  >
                    <td className="px-4 py-2.5">
                      <input
                        type="checkbox"
                        checked={selectedRelationKeys.has(relation.key)}
                        onChange={(e) => toggleRelationSelection(relation.key, e.target.checked)}
                      />
                    </td>
                    <td className="px-4 py-2.5">{modelNameMap.get(relation.source_model_id) || relation.source_model_id}</td>
                    <td className="px-4 py-2.5 font-mono">{relation.source_column}</td>
                    <td className="px-4 py-2.5">{modelNameMap.get(relation.target_model_id) || relation.target_model_id}</td>
                    <td className="px-4 py-2.5 font-mono">{relation.target_column}</td>
                    <td className="px-4 py-2.5">
                      <select
                        value={relation.relation_type}
                        onChange={(e) => {
                          const nextType = e.target.value
                          setRelationsDraft((prev) =>
                            prev.map((r) =>
                              r.key === relation.key ? { ...r, relation_type: nextType } : r,
                            ),
                          )
                        }}
                        className="rounded border border-gray-200 px-2 py-1 text-xs"
                      >
                        {RELATION_TYPE_LABELS.map((rt) => (
                          <option key={rt.value} value={rt.value}>
                            {rt.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-600">
                      {RELATION_SOURCE_LABEL[relation.source]}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => startEditRelation(relation)}
                        >
                          {t('common.edit', 'Edit')}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => removeRelationDraft(relation.key)}
                        >
                          {t('common.remove', 'Remove')}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {busy && stage !== 'idle' && (
        <div className="mb-6 rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm text-blue-700">
          {STAGE_LABEL[stage]}
        </div>
      )}

      <div className="flex justify-between">
        <Button
          variant="secondary"
          onClick={async () => {
            try {
              await loadExistingProjectModels()
              setStep('models')
            } catch (err) {
              toast(err instanceof Error ? err.message : t('setup.failedToLoadModels', 'Failed to load models'), 'error')
            }
          }}
          disabled={busy}
        >
          {t('setup.backModels', 'Back: Models')}
        </Button>
        <div className="flex gap-3">
          <Button
            variant="ghost"
            onClick={skipRelationsAndOpenModeling}
            disabled={busy}
          >
            {t('setup.skip', 'Skip this step')}
          </Button>
          <Button variant="primary" onClick={finalizeSetup} loading={busy && stage === 'relations'}>
            {t('setup.completeSetup', 'Complete Setup')}
          </Button>
        </div>
      </div>
    </>
  )

  const renderContent = () => {
    if (step === 'mode') return renderModeStep()
    if (step === 'models') return renderModelsStep()
    return renderRelationsStep()
  }

  if (!hasPermission('admin', 'manage')) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <EmptyState
          title={t('auth.permissionDenied', 'Permission denied')}
          description={t('setup.adminOnly', 'Only administrators can create projects.')}
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl p-6">
      {resumeSnapshot && (
        <div className="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950">
          <p className="mb-3 text-sm text-blue-800 dark:text-blue-200">
            {t('setup.resumeFound', 'You have an unfinished setup from {time}. Resume where you left off?', {
              time: resumeSnapshot.updatedAt ? new Date(resumeSnapshot.updatedAt).toLocaleString() : '',
            })}
          </p>
          <div className="flex gap-3">
            <Button variant="primary" onClick={handleResume}>
              {t('setup.resume', 'Resume Setup')}
            </Button>
            <Button variant="ghost" onClick={handleStartFresh}>
              {t('setup.startFresh', 'Start Fresh')}
            </Button>
          </div>
        </div>
      )}
      {!resumeSnapshot && renderContent()}
    </div>
  )
}
