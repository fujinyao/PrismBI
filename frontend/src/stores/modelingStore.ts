import { create } from 'zustand'
import { modelingApi } from '@/lib/api'
import { useProjectStore } from './projectStore'

interface ColumnDef {
  name: string
  type: string
  is_primary_key?: boolean
  expression?: string
  display_name?: string
  description?: string
}

interface ModelDef {
  id: number
  project_id: number
  name: string
  display_name?: string
  table_reference?: string
  source_binding_id?: number
  column_defs: ColumnDef[]
  created_at?: string
  updated_at?: string
}

interface ViewDef {
  id: number
  project_id: number
  name: string
  display_name?: string
  model_id: number
  column_defs?: ColumnDef[]
  created_at?: string
}

interface RelationDef {
  id: number
  project_id: number
  name: string
  source_model_id: number
  source_column: string
  target_model_id: number
  target_column: string
  relation_type: string
  created_at?: string
}

interface CalculatedFieldDef {
  id: number
  project_id: number
  name: string
  display_name?: string
  model_id: number
  expression: string
  result_type?: string
}

function toModelDef(m: import('@/lib/api').ApiModelDef): ModelDef {
  return {
    id: m.id,
    project_id: m.project_id,
    name: m.name,
    display_name: m.display_name,
    table_reference: m.table_reference,
    source_binding_id: m.source_binding_id,
    column_defs: (m.column_defs ?? []).map((c) => ({
      name: c.name,
      type: c.type,
      is_primary_key: c.is_primary_key,
      expression: c.expression,
      display_name: c.display_name,
      description: c.description,
    })),
    created_at: m.created_at,
    updated_at: m.updated_at,
  }
}

function toViewDef(v: import('@/lib/api').ApiViewDef): ViewDef {
  return {
    id: v.id,
    project_id: v.project_id,
    name: v.name,
    display_name: v.display_name,
    model_id: v.model_id ?? 0,
    column_defs: v.column_defs?.map((c) => ({
      name: c.name,
      type: c.type,
      display_name: c.display_name,
      description: c.description,
    })),
    created_at: v.created_at,
  }
}

function toRelationDef(r: import('@/lib/api').ApiRelationDef): RelationDef {
  return {
    id: r.id,
    project_id: r.project_id,
    name: r.name ?? '',
    source_model_id: r.source_model_id,
    source_column: r.source_column,
    target_model_id: r.target_model_id,
    target_column: r.target_column,
    relation_type: r.relation_type ?? r.type ?? 'MANY_TO_ONE',
    created_at: r.created_at,
  }
}

function toCalculatedFieldDef(c: import('@/lib/api').ApiCalculatedFieldDef): CalculatedFieldDef {
  return {
    id: c.id,
    project_id: c.project_id,
    name: c.name,
    display_name: c.display_name,
    model_id: c.model_id,
    expression: c.expression,
    result_type: c.result_type,
  }
}

interface Command {
  type: string
  payload: unknown
  timestamp: number
}

interface ModelingState {
  models: ModelDef[]
  views: ViewDef[]
  relations: RelationDef[]
  calculatedFields: CalculatedFieldDef[]
  selectedNodeId: string | null
  undoStack: Command[]
  redoStack: Command[]
  loading: boolean
  error: string | null
  fetchDiagram: () => Promise<void>
  setSelectedNode: (id: string | null) => void
  addModel: (model: ModelDef) => void
  removeModel: (id: number) => Promise<void>
  updateModel: (id: number, data: Partial<ModelDef>) => Promise<void>
  addView: (view: ViewDef) => void
  removeView: (id: number) => Promise<void>
  addRelation: (relation: RelationDef) => void
  removeRelation: (id: number) => Promise<void>
  connect: (source: string, target: string) => void
  undo: () => void
  redo: () => void
  pushCommand: (cmd: Command) => void
  reset: () => void
}

export const useModelingStore = create<ModelingState>()((set, get) => ({
  models: [],
  views: [],
  relations: [],
  calculatedFields: [],
  selectedNodeId: null,
  undoStack: [],
  redoStack: [],
  loading: false,
  error: null,

  fetchDiagram: async () => {
    set({ loading: true })
    try {
      const projectId = useProjectStore.getState().currentProject?.id
      if (!projectId) {
        set({ models: [], views: [], relations: [], calculatedFields: [], loading: false })
        return
      }
      const diagram = await modelingApi.diagram(projectId)
      set({
        models: diagram.models.map(toModelDef),
        views: diagram.views.map(toViewDef),
        relations: diagram.relations.map(toRelationDef),
        calculatedFields: diagram.calculated_fields.map(toCalculatedFieldDef),
        loading: false,
        error: null,
      })
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : 'Failed to fetch diagram' })
    }
  },

  setSelectedNode: (id: string | null) => {
    set({ selectedNodeId: id })
  },

  addModel: (model: ModelDef) => {
    set((state) => ({ models: [...state.models, model] }))
  },

  removeModel: async (id: number) => {
    const projectId = useProjectStore.getState().currentProject?.id
    if (!projectId) return
    try {
      await modelingApi.models.delete(projectId, id)
      set((state) => ({
        models: state.models.filter((m) => m.id !== id),
        relations: state.relations.filter(
          (r) => r.source_model_id !== id && r.target_model_id !== id,
        ),
      }))
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to remove model' })
    }
  },

  updateModel: async (id: number, data: Partial<ModelDef>) => {
    const projectId = useProjectStore.getState().currentProject?.id
    if (!projectId) return
    const before = get().models.find((m) => m.id === id)
    try {
      await modelingApi.models.update(projectId, id, data)
      set((state) => ({
        models: state.models.map((m) => (m.id === id ? { ...m, ...data } : m)),
      }))
      if (before) {
        const changedKeys = Object.keys(data) as (keyof ModelDef)[]
        const beforeFields: Partial<ModelDef> = {}
        const afterFields: Partial<ModelDef> = {}
        for (const key of changedKeys) {
          ;(beforeFields as Record<string, unknown>)[key] = before[key]
          ;(afterFields as Record<string, unknown>)[key] = data[key]
        }
        get().pushCommand({ type: 'updateModel', payload: { id, before: beforeFields, after: afterFields }, timestamp: Date.now() })
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to update model' })
    }
  },

  addView: (view: ViewDef) => {
    set((state) => ({ views: [...state.views, view] }))
  },

  removeView: async (id: number) => {
    const projectId = useProjectStore.getState().currentProject?.id
    if (!projectId) return
    try {
      await modelingApi.views.delete(projectId, id)
      set((state) => ({ views: state.views.filter((v) => v.id !== id) }))
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to remove view' })
    }
  },

  addRelation: (relation: RelationDef) => {
    set((state) => ({ relations: [...state.relations, relation] }))
  },

  removeRelation: async (id: number) => {
    const projectId = useProjectStore.getState().currentProject?.id
    if (!projectId) return
    try {
      await modelingApi.relations.delete(projectId, id)
      set((state) => ({ relations: state.relations.filter((r) => r.id !== id) }))
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to remove relation' })
    }
  },

  connect: async (source: string, target: string) => {
    const projectId = useProjectStore.getState().currentProject?.id
    if (!projectId) return
    const srcId = parseInt(source, 10)
    const tgtId = parseInt(target, 10)
    if (isNaN(srcId) || isNaN(tgtId)) return
    const models = get().models
    const srcModel = models.find((m) => m.id === srcId)
    const tgtModel = models.find((m) => m.id === tgtId)
    const srcCol = srcModel?.column_defs?.[0]?.name || 'id'
    const tgtCol = tgtModel?.column_defs?.[0]?.name || 'id'
    try {
      const result = await modelingApi.relations.create(projectId, {
        name: `${source}_to_${target}`,
        source_model_id: srcId,
        target_model_id: tgtId,
        source_column: srcCol,
        target_column: tgtCol,
        relation_type: 'MANY_TO_ONE',
      })
      set((state) => ({ relations: [...state.relations, { id: result.id, project_id: projectId, name: `${source}_to_${target}`, source_model_id: srcId, source_column: srcCol, target_model_id: tgtId, target_column: tgtCol, relation_type: 'MANY_TO_ONE' as const }] }))
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to create relation' })
    }
  },

  pushCommand: (cmd: Command) => {
    set((state) => ({
      undoStack: [...state.undoStack.slice(-50), cmd],
      redoStack: [],
    }))
  },

  undo: () => {
    const { undoStack } = get()
    if (undoStack.length === 0) return
    const cmd = undoStack[undoStack.length - 1]!
    const state = get()
    set({
      undoStack: undoStack.slice(0, -1),
      redoStack: [...get().redoStack, cmd],
    })
    switch (cmd.type) {
      case 'addModel': {
        const payload = cmd.payload as ModelDef
        set({ models: state.models.filter((m) => m.id !== payload.id) })
        break
      }
      case 'removeModel': {
        const payload = cmd.payload as ModelDef
        set({ models: [...state.models, payload] })
        break
      }
      case 'updateModel': {
        const payload = cmd.payload as { id: number; before: Partial<ModelDef>; after: Partial<ModelDef> }
        set({
          models: state.models.map((m) =>
            m.id === payload.id ? { ...m, ...payload.before } : m,
          ),
        })
        break
      }
      case 'addView': {
        const payload = cmd.payload as ViewDef
        set({ views: state.views.filter((v) => v.id !== payload.id) })
        break
      }
      case 'removeView': {
        const payload = cmd.payload as ViewDef
        set({ views: [...state.views, payload] })
        break
      }
      case 'addRelation': {
        const payload = cmd.payload as RelationDef
        set({ relations: state.relations.filter((r) => r.id !== payload.id) })
        break
      }
      case 'removeRelation': {
        const payload = cmd.payload as RelationDef
        set({ relations: [...state.relations, payload] })
        break
      }
      case 'addCalculatedField': {
        const payload = cmd.payload as CalculatedFieldDef
        set({ calculatedFields: state.calculatedFields.filter((c) => c.id !== payload.id) })
        break
      }
      case 'removeCalculatedField': {
        const payload = cmd.payload as CalculatedFieldDef
        set({ calculatedFields: [...state.calculatedFields, payload] })
        break
      }
    }
    get().fetchDiagram()
  },

  redo: () => {
    const { redoStack } = get()
    if (redoStack.length === 0) return
    const cmd = redoStack[redoStack.length - 1]!
    const state = get()
    set({
      redoStack: redoStack.slice(0, -1),
      undoStack: [...get().undoStack, cmd],
    })
    switch (cmd.type) {
      case 'addModel': {
        const payload = cmd.payload as ModelDef
        set({ models: [...state.models, payload] })
        break
      }
      case 'removeModel': {
        const payload = cmd.payload as ModelDef
        set({ models: state.models.filter((m) => m.id !== payload.id) })
        break
      }
      case 'updateModel': {
        const payload = cmd.payload as { id: number; before: Partial<ModelDef>; after: Partial<ModelDef> }
        set({
          models: state.models.map((m) =>
            m.id === payload.id ? { ...m, ...payload.after } : m,
          ),
        })
        break
      }
      case 'addView': {
        const payload = cmd.payload as ViewDef
        set({ views: [...state.views, payload] })
        break
      }
      case 'removeView': {
        const payload = cmd.payload as ViewDef
        set({ views: state.views.filter((v) => v.id !== payload.id) })
        break
      }
      case 'addRelation': {
        const payload = cmd.payload as RelationDef
        set({ relations: [...state.relations, payload] })
        break
      }
      case 'removeRelation': {
        const payload = cmd.payload as RelationDef
        set({ relations: state.relations.filter((r) => r.id !== payload.id) })
        break
      }
      case 'addCalculatedField': {
        const payload = cmd.payload as CalculatedFieldDef
        set({ calculatedFields: [...state.calculatedFields, payload] })
        break
      }
      case 'removeCalculatedField': {
        const payload = cmd.payload as CalculatedFieldDef
        set({ calculatedFields: state.calculatedFields.filter((c) => c.id !== payload.id) })
        break
      }
    }
    get().fetchDiagram()
  },

  reset: () => {
    set({
      models: [],
      views: [],
      relations: [],
      calculatedFields: [],
      selectedNodeId: null,
      undoStack: [],
      redoStack: [],
      loading: false,
      error: null,
    })
  },
}))
