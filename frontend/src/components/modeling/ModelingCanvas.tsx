'use client'

import { useCallback, useEffect, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
  BackgroundVariant,
  type OnConnect,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { cn } from '@/lib/utils'
import { ModelNode } from './ModelNode'

const nodeTypes: NodeTypes = {
  modelNode: ModelNode as any,
}

interface ModelingCanvasProps {
  models: any[]
  views: any[]
  relations: any[]
  onNodeClick: (id: string) => void
  onConnect: (connection: any) => void
}

export function ModelingCanvas({
  models,
  views,
  relations,
  onNodeClick,
  onConnect,
}: ModelingCanvasProps) {
  const initialNodes: Node[] = useMemo(
    () => [
      ...models.map((m, i) => ({
        id: m.id ?? `model-${i}`,
        type: 'modelNode' as const,
        position: m.position ?? { x: 100 + i * 250, y: 100 },
        data: { label: m.name ?? m.label, fields: m.fields ?? [], color: m.color ?? '#1677ff' },
      })),
      ...views.map((v, i) => ({
        id: v.id ?? `view-${i}`,
        type: 'modelNode' as const,
        position: v.position ?? { x: 100 + i * 250, y: 400 },
        data: { label: v.name ?? v.label, fields: v.fields ?? [], color: v.color ?? '#52c41a' },
      })),
    ],
    [models, views],
  )

  const initialEdges: Edge[] = useMemo(
    () =>
      relations.map((r, i) => ({
        id: r.id ?? `rel-${i}`,
        source: r.source,
        target: r.target,
        sourceHandle: r.sourceHandle,
        targetHandle: r.targetHandle,
        type: r.type ?? 'smoothstep',
        animated: r.animated ?? false,
        label: r.label ?? '',
      })),
    [relations],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)

  useEffect(() => {
    setNodes(initialNodes)
  }, [initialNodes, setNodes])

  useEffect(() => {
    setEdges(initialEdges)
  }, [initialEdges, setEdges])

  const handleConnect: OnConnect = useCallback(
    (connection: Connection) => {
      const edge = addEdge(connection, edges)
      onConnect(edge[edge.length - 1] ?? connection)
    },
    [edges, onConnect],
  )

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeClick(node.id)
    },
    [onNodeClick],
  )

  return (
    <div className={cn('h-full w-full')}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        onNodeClick={handleNodeClick}
        nodeTypes={nodeTypes}
        fitView
        attributionPosition="bottom-left"
        deleteKeyCode={null}
        className="bg-gray-50 dark:bg-gray-900"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d1d5db" />
        <Controls className="rounded-md border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800" />
        <MiniMap
          nodeStrokeColor="#1677ff"
          nodeColor="#f0f5ff"
          nodeBorderRadius={4}
          className="rounded-md border border-gray-200 dark:border-gray-700"
        />
      </ReactFlow>
    </div>
  )
}
