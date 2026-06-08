'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
  type NodeMouseHandler,
  type EdgeMouseHandler,
  type ReactFlowInstance,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ModelNode } from '@/components/modeling/ModelNode'
import {
  modelObjectKindHeaderColor,
  normalizeModelObjectKind,
  type ModelObjectKind,
} from '@/lib/modelObjectKind'

const nodeTypes: NodeTypes = {
  modelNode: ModelNode as any,
}

function parseNodeIdToNumericModelId(nodeId: string): number | undefined {
  const parsed = parseDiagramNodeId(nodeId)
  const rawId = parsed.id
  const numeric = Number(rawId)
  return Number.isFinite(numeric) ? numeric : undefined
}

const DEFAULT_CANVAS_SIZE = { width: 1200, height: 720 }
const MODEL_NODE_WIDTH = 240
const MODEL_NODE_HEADER_HEIGHT = 38
const MODEL_FIELD_ROW_HEIGHT = 28
const MODEL_NODE_MIN_HEIGHT = 96
const MODEL_NODE_GAP_X = 56
const MODEL_NODE_GAP_Y = 40
const LAYER_GAP_X = 128
const COMPONENT_GAP_X = 96
const COMPONENT_GAP_Y = 72
const CANVAS_PADDING = 48

type ModelField = { name: string; type: string; isPrimaryKey?: boolean; primaryKey?: boolean; description?: string | null; display_name?: string | null }

interface LayoutItem {
  model: Model
  fields: ModelField[]
  height: number
}

interface LayoutMeasure {
  width: number
  height: number
  rowHeights: number[]
}

interface LayoutRelation {
  id: string
  sourceId: string
  targetId: string
  layoutSourceId: string
  layoutTargetId: string
  relationType?: string
  virtual?: boolean
}

interface LayoutComponent {
  positions: Map<string, { x: number; y: number }>
  width: number
  height: number
  size: number
  relationCount: number
}

export type DiagramSelectionKind = 'model' | 'view' | 'relation' | 'calculated_field'

export interface DiagramSelection {
  kind: DiagramSelectionKind
  id: string
}

export interface DiagramContextMenuState extends DiagramSelection {
  x: number
  y: number
}

type CanvasContextMenuState =
  | (DiagramSelection & { x: number; y: number })
  | { kind: 'canvas'; x: number; y: number }

interface Relation {
  id: string | number
  sourceModelId?: string | number
  targetModelId?: string | number
  sourceField?: string
  targetField?: string
  source_model_id?: string | number
  target_model_id?: string | number
  source_column?: string
  target_column?: string
  relation_type?: string
  type?: string
  description?: string | null
}

interface Model {
  id: string | number
  name?: string
  display_name?: string
  description?: string | null
  model_type?: string | null
  table_type?: string | null
  fields?: ModelField[]
  column_defs?: { name: string; type: string; is_primary_key?: boolean; primaryKey?: boolean; description?: string | null; display_name?: string | null }[]
  color?: string
  model_id?: string | number
  nodeKind?: 'model' | 'view'
}

interface View {
  id: string | number
  name?: string
  display_name?: string
  description?: string | null
  model_id?: string | number
  fields?: ModelField[]
  column_defs?: { name: string; type: string; is_primary_key?: boolean; primaryKey?: boolean; description?: string | null; display_name?: string | null }[]
}

interface CanvasProps {
  models: Model[]
  views: View[]
  relations: Relation[]
  modelKindsById?: Record<string, ModelObjectKind>
  calculatedFields: Array<{
    id: string | number
    model_id?: string | number
    name?: string
    display_name?: string
    description?: string | null
    expression?: string
    result_type?: string
  }>
  selectedId?: string | null
  selectedKind?: DiagramSelectionKind | null
  linkedModelIds?: string[]
  focusTarget?: { kind: DiagramSelectionKind; id: string; nonce: number } | null
  onSelect?: (selection: DiagramSelection) => void
  onEdit?: (selection: DiagramSelection) => void
  onDelete?: (selection: DiagramSelection) => void
  onCreateModel?: () => void
  onCreateView?: () => void
  onCreateCalculatedField?: (modelId?: number) => void
  onCreateRelation?: (sourceModelId?: number, targetModelId?: number) => void
  onPaneClick?: () => void
}

function getModelFields(model: Model): ModelField[] {
  const fallbackFields: ModelField[] = (model.column_defs ?? []).map((c) => ({
    name: c.name,
    type: c.type,
    display_name: c.display_name,
    description: c.description,
    primaryKey: Boolean(c.is_primary_key || c.primaryKey),
  }))
  const fields = model.fields && model.fields.length > 0 ? model.fields : fallbackFields
  return fields.map((field) => ({
    ...field,
    primaryKey: Boolean(field.primaryKey || field.isPrimaryKey),
  }))
}

function estimateNodeHeight(fields: ModelField[]) {
  return Math.max(MODEL_NODE_MIN_HEIGHT, MODEL_NODE_HEADER_HEIGHT + fields.length * MODEL_FIELD_ROW_HEIGHT)
}

function getModelId(model: Model) {
  return toDiagramNodeId(model.nodeKind ?? 'model', model.id)
}

function toDiagramNodeId(kind: 'model' | 'view', id: string | number) {
  return `${kind}:${id}`
}

function parseDiagramNodeId(id: string): { kind: 'model' | 'view'; id: string } {
  const [kind, ...rest] = id.split(':')
  return {
    kind: kind === 'view' ? 'view' : 'model',
    id: rest.length > 0 ? rest.join(':') : id,
  }
}

function getModelName(itemMap: Map<string, LayoutItem>, id: string) {
  const model = itemMap.get(id)?.model
  return (model?.name ?? String(model?.id ?? id)).toLowerCase()
}

function toModelId(value: string | number | undefined | null) {
  return value === undefined || value === null ? null : String(value)
}

function normalizeRelationType(type?: string) {
  return (type ?? '').replace(/[\s-]+/g, '_').toUpperCase()
}

function buildLayoutRelations(relations: Relation[], modelIds: Set<string>): LayoutRelation[] {
  const layoutRelations: LayoutRelation[] = []

  relations.forEach((rel) => {
    const sourceRawId = toModelId(rel.sourceModelId ?? rel.source_model_id)
    const targetRawId = toModelId(rel.targetModelId ?? rel.target_model_id)
    const sourceId = sourceRawId ? toDiagramNodeId('model', sourceRawId) : null
    const targetId = targetRawId ? toDiagramNodeId('model', targetRawId) : null
    if (!sourceId || !targetId || sourceId === targetId || !modelIds.has(sourceId) || !modelIds.has(targetId)) {
      return
    }

    const relationType = normalizeRelationType(rel.relation_type ?? rel.type)
    const [layoutSourceId, layoutTargetId] =
      relationType === 'MANY_TO_ONE'
        ? [targetId, sourceId]
        : relationType === 'ONE_TO_MANY'
          ? [sourceId, targetId]
          : [sourceId, targetId]

    layoutRelations.push({
      id: String(rel.id),
      sourceId,
      targetId,
      layoutSourceId,
      layoutTargetId,
      relationType,
    })
  })

  return layoutRelations
}

function buildViewLayoutRelations(views: View[], modelIds: Set<string>, viewIds: Set<string>): LayoutRelation[] {
  return views.flatMap((view) => {
    if (view.model_id === undefined || view.model_id === null) return []
    const sourceId = toDiagramNodeId('model', view.model_id)
    const targetId = toDiagramNodeId('view', view.id)
    if (!modelIds.has(sourceId) || !viewIds.has(targetId)) return []
    return [{
      id: `view-link:${view.id}`,
      sourceId,
      targetId,
      layoutSourceId: sourceId,
      layoutTargetId: targetId,
      relationType: 'VIEW',
      virtual: true,
    }]
  })
}

function buildAdjacency(ids: string[], relations: LayoutRelation[]) {
  const adjacency = new Map<string, Set<string>>(ids.map((id) => [id, new Set<string>()]))
  relations.forEach((relation) => {
    adjacency.get(relation.sourceId)?.add(relation.targetId)
    adjacency.get(relation.targetId)?.add(relation.sourceId)
  })
  return adjacency
}

function getRelationDegree(ids: string[], relations: LayoutRelation[]) {
  const degree = new Map<string, number>(ids.map((id) => [id, 0]))
  relations.forEach((relation) => {
    degree.set(relation.sourceId, (degree.get(relation.sourceId) ?? 0) + 1)
    degree.set(relation.targetId, (degree.get(relation.targetId) ?? 0) + 1)
  })
  return degree
}

function sortByGraphImportance(ids: string[], itemMap: Map<string, LayoutItem>, degree: Map<string, number>) {
  return [...ids].sort((a, b) => {
    const degreeDiff = (degree.get(b) ?? 0) - (degree.get(a) ?? 0)
    if (degreeDiff !== 0) return degreeDiff
    return getModelName(itemMap, a).localeCompare(getModelName(itemMap, b))
  })
}

function getComponents(ids: string[], relations: LayoutRelation[]) {
  const adjacency = buildAdjacency(ids, relations)
  const visited = new Set<string>()
  const components: string[][] = []

  ids.forEach((id) => {
    if (visited.has(id)) return
    const component: string[] = []
    const stack = [id]
    visited.add(id)

    while (stack.length > 0) {
      const current = stack.pop()
      if (!current) continue
      component.push(current)
      adjacency.get(current)?.forEach((neighbor) => {
        if (visited.has(neighbor)) return
        visited.add(neighbor)
        stack.push(neighbor)
      })
    }

    components.push(component)
  })

  return components
}

function measureLayout(items: LayoutItem[], columns: number): LayoutMeasure {
  let width = 0
  let height = 0
  const rowHeights: number[] = []

  for (let rowStart = 0; rowStart < items.length; rowStart += columns) {
    const rowItems = items.slice(rowStart, rowStart + columns)
    const rowHeight = Math.max(...rowItems.map((item) => item.height))
    const rowWidth = rowItems.length * MODEL_NODE_WIDTH + Math.max(0, rowItems.length - 1) * MODEL_NODE_GAP_X
    rowHeights.push(rowHeight)
    width = Math.max(width, rowWidth)
    height += rowHeight
    if (rowStart + columns < items.length) height += MODEL_NODE_GAP_Y
  }

  return { width, height, rowHeights }
}

function chooseColumnCount(items: LayoutItem[], canvasSize: { width: number; height: number }) {
  if (items.length <= 1) return 1

  const availableWidth = Math.max(MODEL_NODE_WIDTH, canvasSize.width - CANVAS_PADDING * 2)
  const availableHeight = Math.max(MODEL_NODE_MIN_HEIGHT, canvasSize.height - CANVAS_PADDING * 2)
  const targetAspect = availableWidth / availableHeight
  let bestColumns = 1
  let bestScore = Number.NEGATIVE_INFINITY

  for (let columns = 1; columns <= items.length; columns += 1) {
    const measured = measureLayout(items, columns)
    const fitScale = Math.min(availableWidth / measured.width, availableHeight / measured.height, 1)
    const layoutAspect = measured.width / measured.height
    const aspectPenalty = Math.abs(layoutAspect - targetAspect) * 0.03
    const oneRowPenalty = items.length > 4 && columns === items.length ? 0.2 : 0
    const score = fitScale - aspectPenalty - oneRowPenalty

    if (score > bestScore) {
      bestScore = score
      bestColumns = columns
    }
  }

  return bestColumns
}

function layoutGrid(items: LayoutItem[], canvasSize: { width: number; height: number }) {
  if (items.length === 0) return new Map<string, { x: number; y: number }>()

  const columns = chooseColumnCount(items, canvasSize)
  const measured = measureLayout(items, columns)
  const startY = (canvasSize.height - measured.height) / 2
  const positions = new Map<string, { x: number; y: number }>()
  let currentY = startY

  for (let rowStart = 0, rowIndex = 0; rowStart < items.length; rowStart += columns, rowIndex += 1) {
    const rowItems = items.slice(rowStart, rowStart + columns)
    const rowHeight = measured.rowHeights[rowIndex] ?? Math.max(...rowItems.map((item) => item.height))
    const rowWidth = rowItems.length * MODEL_NODE_WIDTH + Math.max(0, rowItems.length - 1) * MODEL_NODE_GAP_X
    const startX = (canvasSize.width - rowWidth) / 2

    rowItems.forEach((item, columnIndex) => {
      positions.set(getModelId(item.model), {
        x: Math.round(startX + columnIndex * (MODEL_NODE_WIDTH + MODEL_NODE_GAP_X)),
        y: Math.round(currentY + (rowHeight - item.height) / 2),
      })
    })

    currentY += rowHeight + MODEL_NODE_GAP_Y
  }

  return positions
}

function assignComponentLayers(
  componentIds: string[],
  componentRelations: LayoutRelation[],
  itemMap: Map<string, LayoutItem>,
  degree: Map<string, number>,
) {
  if (componentIds.length === 1) {
    const onlyId = componentIds[0]
    return onlyId ? new Map<string, number>([[onlyId, 0]]) : new Map<string, number>()
  }

  const directedOut = new Map<string, Set<string>>(componentIds.map((id) => [id, new Set<string>()]))
  const directedIn = new Map<string, Set<string>>(componentIds.map((id) => [id, new Set<string>()]))
  componentRelations.forEach((relation) => {
    if (!directedOut.has(relation.layoutSourceId) || !directedIn.has(relation.layoutTargetId)) return
    directedOut.get(relation.layoutSourceId)?.add(relation.layoutTargetId)
    directedIn.get(relation.layoutTargetId)?.add(relation.layoutSourceId)
  })

  const roots = sortByGraphImportance(
    componentIds.filter((id) => (directedIn.get(id)?.size ?? 0) === 0 && (directedOut.get(id)?.size ?? 0) > 0),
    itemMap,
    degree,
  )
  const traversalStarts = roots.length > 0 ? roots : sortByGraphImportance(componentIds, itemMap, degree)
  const layers = new Map<string, number>()

  traversalStarts.forEach((startId) => {
    if (!layers.has(startId)) layers.set(startId, 0)
    const queue = [startId]
    let guard = 0

    while (queue.length > 0 && guard < componentIds.length * componentIds.length) {
      guard += 1
      const current = queue.shift()
      if (!current) continue
      const currentLayer = layers.get(current) ?? 0

      directedOut.get(current)?.forEach((next) => {
        const nextLayer = currentLayer + 1
        if ((layers.get(next) ?? Number.NEGATIVE_INFINITY) < nextLayer) {
          layers.set(next, nextLayer)
          queue.push(next)
        }
      })
    }
  })

  componentIds.forEach((id) => {
    if (!layers.has(id)) layers.set(id, 0)
  })

  const minLayer = Math.min(...Array.from(layers.values()))
  if (minLayer !== 0) {
    layers.forEach((layer, id) => layers.set(id, layer - minLayer))
  }

  return layers
}

function orderLayerNodes(
  layerIds: string[],
  previousLayerIds: string[] | undefined,
  componentRelations: LayoutRelation[],
  itemMap: Map<string, LayoutItem>,
  degree: Map<string, number>,
) {
  const previousOrder = new Map<string, number>((previousLayerIds ?? []).map((id, index) => [id, index]))

  return [...layerIds].sort((a, b) => {
    const getBarycenter = (id: string) => {
      const connectedPrevious = componentRelations
        .filter((relation) => relation.layoutTargetId === id || relation.layoutSourceId === id)
        .map((relation) => (relation.layoutTargetId === id ? relation.layoutSourceId : relation.layoutTargetId))
        .filter((neighbor) => previousOrder.has(neighbor))
        .map((neighbor) => previousOrder.get(neighbor) as number)

      if (connectedPrevious.length === 0) return Number.POSITIVE_INFINITY
      return connectedPrevious.reduce((sum, value) => sum + value, 0) / connectedPrevious.length
    }

    const barycenterDiff = getBarycenter(a) - getBarycenter(b)
    if (Number.isFinite(barycenterDiff) && Math.abs(barycenterDiff) > 0.001) return barycenterDiff
    const degreeDiff = (degree.get(b) ?? 0) - (degree.get(a) ?? 0)
    if (degreeDiff !== 0) return degreeDiff
    return getModelName(itemMap, a).localeCompare(getModelName(itemMap, b))
  })
}

function layoutConnectedComponent(
  componentIds: string[],
  componentRelations: LayoutRelation[],
  itemMap: Map<string, LayoutItem>,
  degree: Map<string, number>,
): LayoutComponent {
  if (componentIds.length === 1) {
    const id = componentIds[0] as string
    const height = itemMap.get(id)?.height ?? MODEL_NODE_MIN_HEIGHT
    return {
      positions: new Map([[id, { x: 0, y: 0 }]]),
      width: MODEL_NODE_WIDTH,
      height,
      size: 1,
      relationCount: componentRelations.length,
    }
  }

  const layerById = assignComponentLayers(componentIds, componentRelations, itemMap, degree)
  const layerEntries: Array<[string, number]> = Array.from(layerById.entries())
  const maxLayer = Math.max(...layerEntries.map(([, layer]) => layer))
  const layers: string[][] = Array.from({ length: maxLayer + 1 }, (_, layerIndex) =>
    layerEntries.filter(([, layer]) => layer === layerIndex).map(([id]) => id),
  )

  for (let layerIndex = 0; layerIndex < layers.length; layerIndex += 1) {
    layers[layerIndex] = orderLayerNodes(
      layers[layerIndex] ?? [],
      layerIndex > 0 ? layers[layerIndex - 1] : undefined,
      componentRelations,
      itemMap,
      degree,
    )
  }

  const layerHeights = layers.map((layer) =>
    layer.reduce((height, id, index) => {
      const nodeHeight = itemMap.get(id)?.height ?? MODEL_NODE_MIN_HEIGHT
      return height + nodeHeight + (index > 0 ? MODEL_NODE_GAP_Y : 0)
    }, 0),
  )
  const componentHeight = Math.max(...layerHeights)
  const componentWidth = layers.length * MODEL_NODE_WIDTH + Math.max(0, layers.length - 1) * LAYER_GAP_X
  const positions = new Map<string, { x: number; y: number }>()

  layers.forEach((layer, layerIndex) => {
    let currentY = (componentHeight - (layerHeights[layerIndex] ?? 0)) / 2

    layer.forEach((id) => {
      positions.set(id, {
        x: layerIndex * (MODEL_NODE_WIDTH + LAYER_GAP_X),
        y: Math.round(currentY),
      })
      currentY += (itemMap.get(id)?.height ?? MODEL_NODE_MIN_HEIGHT) + MODEL_NODE_GAP_Y
    })
  })

  return {
    positions,
    width: componentWidth,
    height: componentHeight,
    size: componentIds.length,
    relationCount: componentRelations.length,
  }
}

function measureComponentRows(components: LayoutComponent[], maxWidth: number) {
  const rows: LayoutComponent[][] = []
  const rowWidths: number[] = []
  const rowHeights: number[] = []

  components.forEach((component) => {
    const currentRow = rows[rows.length - 1]
    const currentWidth = rowWidths[rowWidths.length - 1] ?? 0
    const nextWidth = currentRow && currentRow.length > 0 ? currentWidth + COMPONENT_GAP_X + component.width : component.width
    if (currentRow && currentRow.length > 0 && nextWidth <= maxWidth) {
      currentRow.push(component)
      rowWidths[rowWidths.length - 1] = nextWidth
      rowHeights[rowHeights.length - 1] = Math.max(rowHeights[rowHeights.length - 1] ?? 0, component.height)
    } else {
      rows.push([component])
      rowWidths.push(component.width)
      rowHeights.push(component.height)
    }
  })

  const height = rowHeights.reduce((sum, rowHeight, index) => sum + rowHeight + (index > 0 ? COMPONENT_GAP_Y : 0), 0)
  const width = Math.max(...rowWidths, 0)
  return { rows, rowWidths, rowHeights, width, height }
}

function packComponents(components: LayoutComponent[], canvasSize: { width: number; height: number }) {
  if (components.length === 0) return new Map<string, { x: number; y: number }>()

  const sortedComponents = [...components].sort((a, b) => {
    const relationDiff = b.relationCount - a.relationCount
    if (relationDiff !== 0) return relationDiff
    return b.size - a.size
  })
  const availableWidth = Math.max(MODEL_NODE_WIDTH, canvasSize.width - CANVAS_PADDING * 2)
  const availableHeight = Math.max(MODEL_NODE_MIN_HEIGHT, canvasSize.height - CANVAS_PADDING * 2)
  let bestLayout = measureComponentRows(sortedComponents, availableWidth)
  let bestScore = Math.min(availableWidth / Math.max(bestLayout.width, 1), availableHeight / Math.max(bestLayout.height, 1), 1)

  for (let widthFactor = 0.55; widthFactor <= 1; widthFactor += 0.05) {
    const candidate = measureComponentRows(sortedComponents, Math.max(MODEL_NODE_WIDTH, availableWidth * widthFactor))
    const scale = Math.min(availableWidth / Math.max(candidate.width, 1), availableHeight / Math.max(candidate.height, 1), 1)
    const aspectPenalty = Math.abs(candidate.width / Math.max(candidate.height, 1) - availableWidth / availableHeight) * 0.04
    const rowPenalty = Math.max(0, candidate.rows.length - 1) * 0.015
    const score = scale - aspectPenalty - rowPenalty
    if (score > bestScore) {
      bestLayout = candidate
      bestScore = score
    }
  }

  const { rows, rowWidths, rowHeights, height: packedHeight } = bestLayout
  const positions = new Map<string, { x: number; y: number }>()
  let currentY = (canvasSize.height - packedHeight) / 2

  rows.forEach((row, rowIndex) => {
    const rowWidth = rowWidths[rowIndex] ?? 0
    const rowHeight = rowHeights[rowIndex] ?? 0
    let currentX = (canvasSize.width - rowWidth) / 2

    row.forEach((component, componentIndex) => {
      const offsetY = currentY + (rowHeight - component.height) / 2
      component.positions.forEach((position, id) => {
        positions.set(id, {
          x: Math.round(currentX + position.x),
          y: Math.round(offsetY + position.y),
        })
      })
      currentX += component.width + (componentIndex < row.length - 1 ? COMPONENT_GAP_X : 0)
    })

    currentY += rowHeight + COMPONENT_GAP_Y
  })

  return positions
}

function layoutModels(items: LayoutItem[], relations: LayoutRelation[], canvasSize: { width: number; height: number }) {
  if (items.length === 0) return new Map<string, { x: number; y: number }>()
  if (relations.length === 0) return layoutGrid(items, canvasSize)

  const itemMap = new Map(items.map((item) => [getModelId(item.model), item]))
  const ids = items.map((item) => getModelId(item.model))
  const degree = getRelationDegree(ids, relations)
  const components = getComponents(ids, relations).map((componentIds) => {
    const componentIdSet = new Set(componentIds)
    const componentRelations = relations.filter(
      (relation) => componentIdSet.has(relation.sourceId) && componentIdSet.has(relation.targetId),
    )
    return layoutConnectedComponent(componentIds, componentRelations, itemMap, degree)
  })

  return packComponents(components, canvasSize)
}

export function Canvas({
  models,
  views,
  relations,
  modelKindsById = {},
  calculatedFields,
  selectedId,
  selectedKind,
  linkedModelIds = [],
  focusTarget,
  onSelect,
  onEdit,
  onDelete,
  onCreateModel,
  onCreateView,
  onCreateCalculatedField,
  onCreateRelation,
  onPaneClick,
}: CanvasProps) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const flowInstanceRef = useRef<ReactFlowInstance | null>(null)
  const [canvasSize, setCanvasSize] = useState(DEFAULT_CANVAS_SIZE)
  const [contextMenu, setContextMenu] = useState<CanvasContextMenuState | null>(null)
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null)
  const linkedModelIdSet = useMemo(() => new Set(linkedModelIds.map((id) => String(id))), [linkedModelIds])

  const layoutRelations = useMemo(() => {
    const modelIds = new Set(models.map((model) => toDiagramNodeId('model', model.id)))
    const viewIds = new Set(views.map((view) => toDiagramNodeId('view', view.id)))
    return [
      ...buildLayoutRelations(relations, modelIds),
      ...buildViewLayoutRelations(views, modelIds, viewIds),
    ]
  }, [models, relations, views])

  useEffect(() => {
    const wrapper = reactFlowWrapper.current
    if (!wrapper) return

    const updateSize = () => {
      const width = Math.round(wrapper.clientWidth) || DEFAULT_CANVAS_SIZE.width
      const height = Math.round(wrapper.clientHeight) || DEFAULT_CANVAS_SIZE.height
      setCanvasSize((current) => (current.width === width && current.height === height ? current : { width, height }))
    }

    updateSize()
    const resizeObserver = new ResizeObserver(updateSize)
    resizeObserver.observe(wrapper)
    return () => resizeObserver.disconnect()
  }, [])

  const mappedNodes = useMemo<Node[]>(
    () => {
      const calculatedFieldsByModel = new Map<string, typeof calculatedFields>()
      calculatedFields.forEach((field) => {
        if (field.model_id === undefined || field.model_id === null) return
        const key = String(field.model_id)
        const list = calculatedFieldsByModel.get(key) ?? []
        list.push(field)
        calculatedFieldsByModel.set(key, list)
      })

      const diagramItems: Model[] = [
        ...models.map((model) => ({ ...model, nodeKind: 'model' as const })),
        ...views.map((view) => ({ ...view, nodeKind: 'view' as const, color: '#52c41a' })),
      ]
      const layoutItems = diagramItems.map((model) => {
        const fields = [
          ...getModelFields(model),
          ...(model.nodeKind === 'model'
            ? (calculatedFieldsByModel.get(String(model.id)) ?? []).map((field) => ({
                name: field.display_name ?? field.name ?? `calc_${field.id}`,
                type: field.result_type ?? 'CALCULATED',
                description: field.expression,
              }))
            : []),
        ]
        return {
          model,
          fields,
          height: estimateNodeHeight(fields),
        }
      })

      const positions = layoutModels(layoutItems, layoutRelations, canvasSize)

      return layoutItems.map(({ model, fields }) => {
        const id = getModelId(model)
        const kind = model.nodeKind ?? 'model'
        const modelId = String(model.id)
        const modelObjectKind = kind === 'model'
          ? normalizeModelObjectKind(modelKindsById[modelId] ?? model.model_type ?? model.table_type)
          : undefined
        const nodeColor = kind === 'model'
          ? modelObjectKindHeaderColor(modelObjectKind ?? 'table')
          : (model.color ?? '#16a34a')
        const isRelationLinkedModel = selectedKind === 'relation' && kind === 'model' && linkedModelIdSet.has(modelId)
        return {
          id,
          type: 'modelNode',
          position: positions.get(id) ?? { x: canvasSize.width / 2, y: canvasSize.height / 2 },
          selected: selectedKind === kind && selectedId === String(model.id),
          data: {
            label: model.display_name ?? model.name ?? `${kind} ${model.id}`,
            description: model.description,
            fields,
            color: nodeColor,
            nodeKind: kind,
            modelObjectKind,
            relationLinked: isRelationLinkedModel,
          },
        }
      })
    },
    [calculatedFields, canvasSize, layoutRelations, linkedModelIdSet, modelKindsById, models, selectedId, selectedKind, views],
  )

  const mappedEdges = useMemo<Edge[]>(
    () =>
      layoutRelations
        .filter((rel) => !rel.virtual)
        .map((rel) => {
          const originalRelation = relations.find((relation) => String(relation.id) === rel.id)
          const sourceField = originalRelation?.sourceField ?? originalRelation?.source_column
          const targetField = originalRelation?.targetField ?? originalRelation?.target_column
          return {
            id: rel.id,
            source: rel.layoutSourceId,
            target: rel.layoutTargetId,
            label: sourceField && targetField ? `${sourceField} → ${targetField}` : undefined,
            selected: selectedKind === 'relation' && selectedId === rel.id,
            animated: false,
            type: 'smoothstep',
            sourceHandle: 'right',
            targetHandle: 'left',
            style: selectedKind === 'relation' && selectedId === rel.id
              ? { stroke: '#1677ff', strokeWidth: 2.2 }
              : hoveredEdgeId === rel.id
                ? { stroke: '#3b82f6', strokeWidth: 2 }
              : { stroke: '#64748b', strokeWidth: 1.4 },
          } as Edge
        })
        .filter((edge): edge is Edge => Boolean(edge)),
    [hoveredEdgeId, layoutRelations, relations, selectedId, selectedKind],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(mappedNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(mappedEdges)

  useEffect(() => {
    setNodes(mappedNodes)
  }, [mappedNodes, setNodes])

  useEffect(() => {
    setEdges(mappedEdges)
  }, [mappedEdges, setEdges])

  useEffect(() => {
    if (!flowInstanceRef.current || mappedNodes.length === 0) return
    const frame = window.requestAnimationFrame(() => {
      flowInstanceRef.current?.fitView({ padding: 0.18, duration: 200 })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [canvasSize, layoutRelations, mappedNodes.length, models, views])

  const focusBounds = useCallback((bounds: { x: number; y: number; width: number; height: number }) => {
    const instance = flowInstanceRef.current as any
    if (!instance?.fitBounds) return
    instance.fitBounds(bounds, { padding: 0.35, duration: 260 })
  }, [])

  useEffect(() => {
    if (!focusTarget) return
    const frame = window.requestAnimationFrame(() => {
      if (focusTarget.kind === 'model' || focusTarget.kind === 'view') {
        const nodeId = toDiagramNodeId(focusTarget.kind, focusTarget.id)
        const node = flowInstanceRef.current?.getNode(nodeId)
        if (!node) return
        const width = Number(node.measured?.width ?? node.width ?? MODEL_NODE_WIDTH)
        const height = Number(node.measured?.height ?? node.height ?? MODEL_NODE_MIN_HEIGHT)
        focusBounds({ x: node.position.x, y: node.position.y, width, height })
        return
      }

      if (focusTarget.kind !== 'relation') return
      const relation = layoutRelations.find((item) => String(item.id) === String(focusTarget.id))
      if (!relation) return
      const sourceNode = flowInstanceRef.current?.getNode(relation.layoutSourceId)
      const targetNode = flowInstanceRef.current?.getNode(relation.layoutTargetId)
      if (!sourceNode || !targetNode) return
      const sourceWidth = Number(sourceNode.measured?.width ?? sourceNode.width ?? MODEL_NODE_WIDTH)
      const sourceHeight = Number(sourceNode.measured?.height ?? sourceNode.height ?? MODEL_NODE_MIN_HEIGHT)
      const targetWidth = Number(targetNode.measured?.width ?? targetNode.width ?? MODEL_NODE_WIDTH)
      const targetHeight = Number(targetNode.measured?.height ?? targetNode.height ?? MODEL_NODE_MIN_HEIGHT)

      const minX = Math.min(sourceNode.position.x, targetNode.position.x)
      const minY = Math.min(sourceNode.position.y, targetNode.position.y)
      const maxX = Math.max(sourceNode.position.x + sourceWidth, targetNode.position.x + targetWidth)
      const maxY = Math.max(sourceNode.position.y + sourceHeight, targetNode.position.y + targetHeight)

      focusBounds({
        x: minX,
        y: minY,
        width: Math.max(MODEL_NODE_WIDTH, maxX - minX),
        height: Math.max(MODEL_NODE_MIN_HEIGHT, maxY - minY),
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [focusBounds, focusTarget, layoutRelations])

  const onConnect = useCallback(
    (connection: Connection) => {
      const source = connection.source ? parseDiagramNodeId(connection.source) : null
      const target = connection.target ? parseDiagramNodeId(connection.target) : null
      if (source?.kind === 'model' && target?.kind === 'model') {
        const sourceId = Number(source.id)
        const targetId = Number(target.id)
        if (Number.isFinite(sourceId) && Number.isFinite(targetId)) {
          onCreateRelation?.(sourceId, targetId)
        }
        return
      }
      setEdges((eds) => [...eds, { ...connection, animated: true, style: { stroke: '#1677ff' } } as Edge])
    },
    [onCreateRelation, setEdges],
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const type = event.dataTransfer.getData('application/reactflow')
      if (!type) return
      const position = flowInstanceRef.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY }) ?? {
        x: event.clientX,
        y: event.clientY,
      }
      const newModel: Model = {
        id: `model-${Date.now()}`,
        name: type,
        fields: [],
      }
      const newNode: Node = {
        id: String(newModel.id),
        type: 'modelNode',
        position,
        data: { label: newModel.name, fields: newModel.fields },
      }
      setNodes((nds) => [...nds, newNode])
    },
    [setNodes],
  )

  const getContextMenuPosition = useCallback((event: { clientX: number; clientY: number }): { x: number; y: number } => {
    const bounds = reactFlowWrapper.current?.getBoundingClientRect()
    if (!bounds) return { x: event.clientX, y: event.clientY }
    const menuWidth = 168
    const menuHeight = 240
    return {
      x: Math.min(Math.max(event.clientX - bounds.left, 8), Math.max(8, bounds.width - menuWidth - 8)),
      y: Math.min(Math.max(event.clientY - bounds.top, 8), Math.max(8, bounds.height - menuHeight - 8)),
    }
  }, [])

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      onSelect?.(parseDiagramNodeId(node.id))
      setContextMenu(null)
    },
    [onSelect],
  )

  const handleNodeDoubleClick: NodeMouseHandler = useCallback(
    (_, node) => {
      onEdit?.(parseDiagramNodeId(node.id))
      setContextMenu(null)
    },
    [onEdit],
  )

  const handleNodeContextMenu: NodeMouseHandler = useCallback(
    (event, node) => {
      event.preventDefault()
      const selection = parseDiagramNodeId(node.id)
      onSelect?.(selection)
      setContextMenu({ ...selection, ...getContextMenuPosition(event) })
    },
    [getContextMenuPosition, onSelect],
  )

  const handleEdgeClick: EdgeMouseHandler = useCallback(
    (_, edge) => {
      onSelect?.({ kind: 'relation', id: edge.id })
      setContextMenu(null)
    },
    [onSelect],
  )

  const handleEdgeDoubleClick: EdgeMouseHandler = useCallback(
    (_, edge) => {
      onEdit?.({ kind: 'relation', id: edge.id })
      setContextMenu(null)
    },
    [onEdit],
  )

  const handleEdgeContextMenu: EdgeMouseHandler = useCallback(
    (event, edge) => {
      event.preventDefault()
      onSelect?.({ kind: 'relation', id: edge.id })
      setContextMenu({ kind: 'relation', id: edge.id, ...getContextMenuPosition(event) })
    },
    [getContextMenuPosition, onSelect],
  )

  const handlePaneClick = useCallback(() => {
    setContextMenu(null)
    onPaneClick?.()
  }, [onPaneClick])

  const handlePaneContextMenu = useCallback(
    (event: MouseEvent | React.MouseEvent) => {
      event.preventDefault()
      setContextMenu({ kind: 'canvas', ...getContextMenuPosition(event) })
    },
    [getContextMenuPosition],
  )

  const menuTitle = contextMenu?.kind === 'canvas'
    ? 'Canvas'
    : contextMenu?.kind === 'relation'
      ? 'Relationship'
      : contextMenu?.kind === 'view'
        ? 'View'
        : 'Model'

  return (
    <div ref={reactFlowWrapper} className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onNodeClick={handleNodeClick}
        onNodeDoubleClick={handleNodeDoubleClick}
        onNodeContextMenu={handleNodeContextMenu}
        onEdgeClick={handleEdgeClick}
        onEdgeMouseEnter={(_, edge) => setHoveredEdgeId(String(edge.id))}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        onEdgeDoubleClick={handleEdgeDoubleClick}
        onEdgeContextMenu={handleEdgeContextMenu}
        onPaneClick={handlePaneClick}
        onPaneContextMenu={handlePaneContextMenu}
        onInit={(instance) => {
          flowInstanceRef.current = instance
        }}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        deleteKeyCode={null}
        className="bg-gray-50 dark:bg-gray-900"
      >
        <Background />
        <Controls />
        <MiniMap
          nodeStrokeColor="#1677ff"
          nodeColor={(node) => String(node.data?.color ?? '#f8fafc')}
          nodeBorderRadius={8}
        />
      </ReactFlow>
      {contextMenu && (
        <div
          className="absolute z-50 min-w-[168px] overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-gray-800"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="border-b border-gray-100 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:border-gray-700">
            {menuTitle}
          </div>
          {contextMenu.kind === 'canvas' ? (
            <>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  onCreateModel?.()
                  setContextMenu(null)
                }}
              >
                Create data model
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  onCreateView?.()
                  setContextMenu(null)
                }}
              >
                How to create a View
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  onCreateRelation?.()
                  setContextMenu(null)
                }}
              >
                Create relationship
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  onCreateCalculatedField?.()
                  setContextMenu(null)
                }}
              >
                Create calculated field
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  flowInstanceRef.current?.fitView({ padding: 0.18, duration: 200 })
                  setContextMenu(null)
                }}
              >
                Fit to screen
              </button>
            </>
          ) : (
            <>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  onSelect?.({ kind: contextMenu.kind, id: contextMenu.id })
                  setContextMenu(null)
                }}
              >
                View details
              </button>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                onClick={() => {
                  onEdit?.({ kind: contextMenu.kind, id: contextMenu.id })
                  setContextMenu(null)
                }}
              >
                Edit metadata
              </button>
              {contextMenu.kind === 'model' && (
                <>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                    onClick={() => {
                      onCreateCalculatedField?.(parseNodeIdToNumericModelId(contextMenu.id))
                      setContextMenu(null)
                    }}
                  >
                    Add calculated field
                  </button>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                    onClick={() => {
                      onCreateRelation?.(parseNodeIdToNumericModelId(contextMenu.id))
                      setContextMenu(null)
                    }}
                  >
                    Add relationship
                  </button>
                </>
              )}
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-error hover:bg-error-50 dark:hover:bg-error-900/30"
                onClick={() => {
                  onDelete?.({ kind: contextMenu.kind, id: contextMenu.id })
                  setContextMenu(null)
                }}
              >
                Delete
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
