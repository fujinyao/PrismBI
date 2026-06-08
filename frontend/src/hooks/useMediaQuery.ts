'use client'

import { useSyncExternalStore, useCallback } from 'react'

const breakpoints = {
  xs: 480,
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
  '2xl': 1536,
} as const

type Breakpoint = keyof typeof breakpoints

function getSnapshot(query: string) {
  if (typeof window === 'undefined') return false
  return window.matchMedia(query).matches
}

function getServerSnapshot() {
  return false
}

function subscribe(callback: () => void, query: string) {
  if (typeof window === 'undefined') return () => {}
  const mql = window.matchMedia(query)
  mql.addEventListener('change', callback)
  return () => mql.removeEventListener('change', callback)
}

export function useMediaQuery(query: string): boolean {
  const subscribeQuery = useCallback((cb: () => void) => subscribe(cb, query), [query])
  return useSyncExternalStore(subscribeQuery, () => getSnapshot(query), getServerSnapshot)
}

export function useBreakpoint(breakpoint: Breakpoint, direction: 'up' | 'down' = 'up'): boolean {
  const width = breakpoints[breakpoint]
  const query = direction === 'up' ? `(min-width: ${width}px)` : `(max-width: ${width - 1}px)`
  return useMediaQuery(query)
}

export function useIsMobile(): boolean {
  return useBreakpoint('md', 'down')
}

export function useIsDesktop(): boolean {
  return useBreakpoint('lg', 'up')
}