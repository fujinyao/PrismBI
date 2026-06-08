'use client'

import { useState } from 'react'
import { BaseEdge, getBezierPath } from '@xyflow/react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

export function RelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: any) {
  const [hovered, setHovered] = useState(false)
  const t = useI18nStore((s) => s.t)
  const label = data?.label ?? ''

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  return (
    <g
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="group"
    >
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          ...style,
          strokeDasharray: hovered ? undefined : '5 5',
          animation: hovered ? 'none' : 'dashdraw 0.5s linear infinite',
        }}
        markerEnd={markerEnd}
        className={cn(
          'stroke-gray-400 dark:stroke-gray-500',
          hovered && '!stroke-primary stroke-[2px]',
        )}
      />

      {label && (
        <foreignObject
          width={60}
          height={28}
          x={labelX - 30}
          y={labelY - 14}
          className="overflow-visible"
        >
          <div className="flex h-full items-center justify-center">
            <span
              className={cn(
                'rounded-md border bg-white px-2 py-0.5 text-xs font-semibold shadow-sm dark:bg-gray-800',
                hovered
                  ? 'border-primary text-primary'
                  : 'border-gray-300 text-gray-600 dark:border-gray-600 dark:text-gray-400',
              )}
            >
              {label}
            </span>
          </div>
        </foreignObject>
      )}

      {hovered && (
        <foreignObject
          width={24}
          height={24}
          x={labelX - 12}
          y={labelY + 20}
          className="overflow-visible"
        >
          <button
            onClick={(e) => {
              e.stopPropagation()
              if (typeof data?.onDelete === 'function') data.onDelete(id)
            }}
            className="flex h-6 w-6 items-center justify-center rounded-full bg-error text-white shadow hover:bg-error-600"
            title={t('modeling.deleteRelation', 'Delete relation')}
          >
            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </foreignObject>
      )}
    </g>
  )
}
