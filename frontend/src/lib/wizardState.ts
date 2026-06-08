'use client'

const STORAGE_KEY = 'prismbi-wizard-state'
const VERSION = 1

interface DatasourceDraftSnapshot {
  key: string
  type: string
  name: string
  properties: Record<string, unknown>
  mappedProperties?: Record<string, unknown>
}

interface DatasourceBindingSnapshot {
  bindingId: number
  datasourceId: number
  name: string
  type: string
  sampleDatasetKey?: string
  alias?: string
  properties: Record<string, unknown>
}

interface TableColumnSnapshot {
  name: string
  type: string
  is_primary_key?: boolean
  display_name?: string | null
  description?: string | null
}

interface TableRecordSnapshot {
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
  columns: TableColumnSnapshot[]
}

interface CreatedModelSnapshot {
  id: number
  name: string
  bindingId: number
  tableReference: string
  description?: string | null
  columns: TableColumnSnapshot[]
}

interface RelationDraftSnapshot {
  key: string
  source_model_id: number
  source_column: string
  target_model_id: number
  target_column: string
  relation_type: string
  description?: string | null
  source: 'sample' | 'recommended' | 'manual'
}

export interface WizardSnapshot {
  version: number
  step: 'project' | 'datasource' | 'tables' | 'models' | 'relations' | 'complete'
  mode: 'sample' | 'manual' | null
  projectName: string
  displayName: string
  projectDescription: string
  projectId: number | null
  datasources: DatasourceBindingSnapshot[]
  selectedSampleDatasetKeys: string[]
  tableRecords: TableRecordSnapshot[]
  selectedTableKeys: string[]
  models: CreatedModelSnapshot[]
  relationsDraft: RelationDraftSnapshot[]
  selectedRelationKeys: string[]
  manualDraftDatasources: DatasourceDraftSnapshot[]
  selectedManualDatasourceKeys: string[]
  manualTableDrafts: Record<number, { name: string; reference: string }>
  updatedAt: string
}


function toSerializable(value: unknown): unknown {
  if (value instanceof Set) return Array.from(value)
  return value
}

function fromSet(value: unknown): string[] {
  if (Array.isArray(value)) return value
  return []
}

export function saveSnapshot(data: Partial<WizardSnapshot>): void {
  try {
    const existing = loadSnapshot()
    const snapshot: WizardSnapshot = {
      version: VERSION,
      step: 'project',
      mode: null,
      projectName: '',
      displayName: '',
      projectDescription: '',
      projectId: null,
      datasources: [],
      selectedSampleDatasetKeys: [],
      tableRecords: [],
      selectedTableKeys: [],
      models: [],
      relationsDraft: [],
      selectedRelationKeys: [],
      manualDraftDatasources: [],
      selectedManualDatasourceKeys: [],
      manualTableDrafts: {},
      ...existing,
      ...data,
      updatedAt: new Date().toISOString(),
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot, (_key, value) => toSerializable(value)))
  } catch {
    // localStorage might be full or unavailable
  }
}

export function loadSnapshot(): WizardSnapshot | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<WizardSnapshot>
    if (parsed.version !== VERSION) {
      clearSnapshot()
      return null
    }
    return {
      version: VERSION,
      step: parsed.step ?? 'project',
      mode: parsed.mode ?? null,
      projectName: parsed.projectName ?? '',
      displayName: parsed.displayName ?? '',
      projectDescription: parsed.projectDescription ?? '',
      projectId: parsed.projectId ?? null,
      datasources: parsed.datasources ?? [],
      selectedSampleDatasetKeys: fromSet(parsed.selectedSampleDatasetKeys),
      tableRecords: parsed.tableRecords ?? [],
      selectedTableKeys: fromSet(parsed.selectedTableKeys),
      models: parsed.models ?? [],
      relationsDraft: parsed.relationsDraft ?? [],
      selectedRelationKeys: fromSet(parsed.selectedRelationKeys),
      manualDraftDatasources: parsed.manualDraftDatasources ?? [],
      selectedManualDatasourceKeys: fromSet(parsed.selectedManualDatasourceKeys),
      manualTableDrafts: parsed.manualTableDrafts ?? {},
      updatedAt: parsed.updatedAt ?? '',
    }
  } catch {
    return null
  }
}

export function clearSnapshot(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}

export function getSnapshotAgeMinutes(snapshot: WizardSnapshot): number {
  if (!snapshot.updatedAt) return 0
  const saved = new Date(snapshot.updatedAt).getTime()
  const now = Date.now()
  return Math.floor((now - saved) / 60000)
}
