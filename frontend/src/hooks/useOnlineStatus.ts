'use client'

import { useSyncExternalStore, useCallback } from 'react'

function getSnapshot() {
  if (typeof window === 'undefined') return true
  return navigator.onLine
}

function getServerSnapshot() {
  return true
}

function subscribe(callback: () => void) {
  if (typeof window === 'undefined') return () => {}
  window.addEventListener('online', callback)
  window.addEventListener('offline', callback)
  return () => {
    window.removeEventListener('online', callback)
    window.removeEventListener('offline', callback)
  }
}

export function useOnlineStatus(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

export function useOfflineState() {
  const isOnline = useOnlineStatus()
  const isOffline = !isOnline
  return { isOnline, isOffline }
}