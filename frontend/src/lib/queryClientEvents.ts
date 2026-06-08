import type { QueryClient } from '@tanstack/react-query'

let currentQueryClient: QueryClient | null = null

export function setAppQueryClient(queryClient: QueryClient) {
  currentQueryClient = queryClient
}

export function clearAppQueryCache() {
  currentQueryClient?.clear()
}
