'use client'

import { useEffect, useState } from 'react'
import { useThemeStore } from '@/stores/themeStore'

interface ThemeProviderProps {
  children: React.ReactNode
  attribute?: string
  defaultTheme?: string
  enableSystem?: boolean
}

export function ThemeProvider({
  children,
  attribute = 'class',
  defaultTheme = 'light',
  enableSystem = true,
}: ThemeProviderProps) {
  const [mounted, setMounted] = useState(false)
  const { mode, primaryColor, borderRadius, font } = useThemeStore()

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (!mounted) return

    const root = document.documentElement
    const effective =
      mode === 'system' && enableSystem
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'
        : mode

    if (attribute === 'class') {
      root.classList.toggle('dark', effective === 'dark')
    } else {
      root.setAttribute(attribute, effective)
    }

    root.style.setProperty('--color-primary', primaryColor)
    root.style.setProperty('--color-primary-500', primaryColor)
    root.style.setProperty('--radius-md', borderRadius === 'sm' ? '4px' : borderRadius === 'lg' ? '12px' : '8px')
    root.style.setProperty('--font-sans', `'${font}', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`)

    if (enableSystem) {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
      const handler = () => {
        if (mode === 'system') {
          root.classList.toggle('dark', mediaQuery.matches)
        }
      }
      mediaQuery.addEventListener('change', handler)
      return () => mediaQuery.removeEventListener('change', handler)
    }
  }, [mode, primaryColor, borderRadius, font, mounted, attribute, enableSystem])

  if (!mounted) {
    return (
      <div style={{ visibility: 'hidden' }}>
        {children}
      </div>
    )
  }

  return <>{children}</>
}
