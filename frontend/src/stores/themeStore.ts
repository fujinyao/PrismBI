import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type ThemeMode = 'light' | 'dark' | 'system'

interface ThemeState {
  mode: ThemeMode
  primaryColor: string
  borderRadius: string
  font: string
  setMode: (mode: ThemeMode) => void
  setPrimaryColor: (color: string) => void
  setTheme: (theme: Partial<Pick<ThemeState, 'mode' | 'primaryColor' | 'borderRadius' | 'font'>>) => void
  getEffectiveMode: () => 'light' | 'dark'
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      mode: 'system',
      primaryColor: '#1677ff',
      borderRadius: 'md',
      font: 'Inter',

      setMode: (mode: ThemeMode) => {
        set({ mode })
        const state = get()
        applyTheme(mode, state.primaryColor, state.borderRadius, state.font)
      },

      setPrimaryColor: (primaryColor: string) => {
        set({ primaryColor })
        const state = get()
        applyTheme(state.mode, primaryColor, state.borderRadius, state.font)
      },

      setTheme: (theme) => {
        set(theme)
        const state = get()
        applyTheme(state.mode, state.primaryColor, state.borderRadius, state.font)
      },

      getEffectiveMode: () => {
        const { mode } = get()
        if (mode === 'system') {
          if (typeof window === 'undefined') return 'light'
          return window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark'
            : 'light'
        }
        return mode
      },
    }),
    {
      name: 'theme-store',
      partialize: (state) => ({
        mode: state.mode,
        primaryColor: state.primaryColor,
        borderRadius: state.borderRadius,
        font: state.font,
      }),
    },
  ),
)

export function applyTheme(mode: ThemeMode, primaryColor: string, borderRadius = 'md', font = 'Inter') {
  if (typeof window === 'undefined') return

  const root = document.documentElement
  const effective =
    mode === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : mode

  root.classList.toggle('dark', effective === 'dark')
  root.style.setProperty('--color-primary', primaryColor)
  root.style.setProperty('--color-primary-500', primaryColor)
  root.style.setProperty('--radius-md', borderRadius === 'sm' ? '4px' : borderRadius === 'lg' ? '12px' : '8px')
  root.style.setProperty('--font-sans', `'${font}', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`)
}
