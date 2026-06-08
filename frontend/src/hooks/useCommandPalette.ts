import { useState, useEffect, useMemo, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useI18nStore } from '@/stores/i18nStore'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'

export interface CommandItem {
  id: string
  label?: string
  labelKey?: string
  icon?: string
  shortcut?: string
  action: () => void
  category?: string
  categoryKey?: string
  keywords?: string[]
  keywordsKey?: string[]
  hidden?: boolean
}

export function useCommandPalette() {
  const [open, setOpen] = useState(false)
  const t = useI18nStore((s) => s.t)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const setMode = useThemeStore((s) => s.setMode)
  const router = useRouter()

  const commands: CommandItem[] = useMemo(() => [
    { id: 'nav.home', labelKey: 'nav.home', action: () => router.push('/home'), categoryKey: 'command.category.navigation', keywordsKey: ['home', 'ask', 'qa'] },
    { id: 'nav.dashboard', labelKey: 'nav.dashboard', action: () => router.push('/home/dashboard'), categoryKey: 'command.category.navigation', keywordsKey: ['dashboard', 'chart'] },
    { id: 'nav.modeling', labelKey: 'nav.modeling', action: () => router.push('/modeling'), categoryKey: 'command.category.navigation', keywordsKey: ['model', 'canvas', 'entity'] },
    { id: 'nav.knowledge', labelKey: 'nav.knowledge', action: () => router.push('/knowledge/instructions'), categoryKey: 'command.category.navigation', keywordsKey: ['knowledge', 'instruction', 'doc'] },
    { id: 'nav.settings', labelKey: 'nav.settings', action: () => router.push('/settings'), categoryKey: 'command.category.navigation', keywordsKey: ['settings', 'config', 'preference'] },
    { id: 'nav.admin.users', labelKey: 'nav.admin.users', action: () => router.push('/admin/users'), categoryKey: 'command.category.admin', keywordsKey: ['admin', 'user', 'manage'], hidden: !hasPermission('admin', 'read') },
    { id: 'nav.admin.roles', labelKey: 'nav.admin.roles', action: () => router.push('/admin/roles'), categoryKey: 'command.category.admin', keywordsKey: ['admin', 'role', 'permission'], hidden: !hasPermission('admin', 'read') },
    { id: 'nav.admin.audit', labelKey: 'nav.admin.audit', action: () => router.push('/admin/audit'), categoryKey: 'command.category.admin', keywordsKey: ['admin', 'audit', 'log'], hidden: !hasPermission('admin', 'read') },
    { id: 'nav.admin.backup', labelKey: 'nav.admin.backup', action: () => router.push('/admin/backup'), categoryKey: 'command.category.admin', keywordsKey: ['backup', 'restore', 'admin'], hidden: !hasPermission('backup', 'read') },
    { id: 'action.new-thread', labelKey: 'command.newThread', icon: 'plus', action: () => router.push('/home'), categoryKey: 'command.category.actions', keywordsKey: ['new', 'thread', 'ask', 'question'] },
    { id: 'action.theme.toggle', labelKey: 'command.toggleTheme', icon: 'theme', action: () => { const mode = useThemeStore.getState().mode; const next = mode === 'dark' ? 'light' : 'dark'; setMode(next) }, categoryKey: 'command.category.actions', keywordsKey: ['theme', 'dark', 'light', 'mode'] },
  ], [router, hasPermission, setMode, t])

  const toggle = useCallback(() => setOpen((o) => !o), [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [toggle])

  return { open, setOpen, commands }
}