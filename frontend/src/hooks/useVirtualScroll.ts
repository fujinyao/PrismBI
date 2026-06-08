'use client'

import { useRef, useCallback } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'

interface VirtualScrollOptions {
  count: number
  estimateSize?: number
  overscan?: number
}

export function useVirtualScroll({
  count,
  estimateSize = 48,
  overscan = 5,
}: VirtualScrollOptions) {
  const parentRef = useRef<HTMLDivElement | null>(null)

  const containerRef = useCallback((node: HTMLDivElement | null) => {
    parentRef.current = node
  }, [])

  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count,
    getScrollElement: () => parentRef.current,
    estimateSize: () => estimateSize,
    overscan,
  })

  return {
    virtualizer,
    containerRef,
    totalSize: virtualizer.getTotalSize(),
    virtualItems: virtualizer.getVirtualItems(),
  }
}
