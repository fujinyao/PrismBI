'use client'

import { useState, useRef, useCallback, type TouchEvent } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

const THRESHOLD = 60

interface PullToRefreshProps {
  onRefresh: () => Promise<void>
  children: React.ReactNode
  disabled?: boolean
}

export function PullToRefresh({ onRefresh, children, disabled = false }: PullToRefreshProps) {
  const t = useI18nStore((s) => s.t)
  const [pullDistance, setPullDistance] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const startY = useRef(0)
  const pulling = useRef(false)

  const handleTouchStart = useCallback(
    (e: TouchEvent) => {
      if (disabled || refreshing) return
      if (containerRef.current && containerRef.current.scrollTop <= 0) {
        const touch = e.touches?.[0]
        if (!touch) return
        startY.current = touch.clientY
        pulling.current = true
      }
    },
    [disabled, refreshing],
  )

  const handleTouchMove = useCallback(
    (e: TouchEvent) => {
      if (!pulling.current || disabled || refreshing) return
      const touch = e.touches?.[0]
      if (!touch) return
      const currentY = touch.clientY
      const distance = Math.max(0, currentY - startY.current)
      const damped = distance * 0.4
      setPullDistance(damped)
    },
    [disabled, refreshing],
  )

  const handleTouchEnd = useCallback(async () => {
    if (!pulling.current) return
    pulling.current = false

    if (pullDistance >= THRESHOLD && !disabled && !refreshing) {
      setRefreshing(true)
      try {
        await onRefresh()
      } finally {
        setRefreshing(false)
        setPullDistance(0)
      }
    } else {
      setPullDistance(0)
    }
  }, [pullDistance, disabled, refreshing, onRefresh])

  return (
    <div
      ref={containerRef}
      className="relative h-full overflow-y-auto overscroll-contain"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      <div
        className="flex items-center justify-center transition-transform duration-200"
        style={{
          height: refreshing ? THRESHOLD : Math.min(pullDistance, THRESHOLD),
          transform: `translateY(${refreshing ? 0 : pullDistance}px)`,
          overflow: 'hidden',
        }}
      >
        <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          {refreshing ? (
            <>
              <svg className="h-5 w-5 animate-spin text-primary" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              <span>{t('common.refreshing', 'Refreshing...')}</span>
            </>
          ) : pullDistance >= THRESHOLD ? (
            <>
              <svg className={cn('h-5 w-5 transition-transform', pullDistance >= THRESHOLD && 'rotate-180')} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
              <span>{t('common.releaseToRefresh', 'Release to refresh')}</span>
            </>
          ) : (
            <>
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
              <span>{t('common.pullToRefresh', 'Pull to refresh')}</span>
            </>
          )}
        </div>
      </div>

      <div
        style={{
          transform: `translateY(${refreshing ? THRESHOLD : pullDistance}px)`,
          transition: refreshing ? 'transform 0.3s ease' : undefined,
        }}
      >
        {children}
      </div>
    </div>
  )
}
