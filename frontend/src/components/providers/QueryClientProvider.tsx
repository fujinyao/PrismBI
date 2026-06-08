'use client'

import { useState } from 'react'
import { QueryClient, QueryClientProvider as TanStackQueryClientProvider } from '@tanstack/react-query'
import { setAppQueryClient } from '@/lib/queryClientEvents'

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof Response && error.status >= 400 && error.status < 500) return false
  return failureCount < 2
}

function exponentialBackoff(failureCount: number): number {
  return Math.min(1000 * Math.pow(2, failureCount), 30000)
}

export function QueryClientProvider({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () => {
      const client = new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,
            retry: shouldRetry,
            retryDelay: exponentialBackoff,
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: 1,
            retryDelay: exponentialBackoff,
          },
        },
      })
      setAppQueryClient(client)
      return client
    },
  )

  return (
    <TanStackQueryClientProvider client={queryClient}>
      {children}
    </TanStackQueryClientProvider>
  )
}