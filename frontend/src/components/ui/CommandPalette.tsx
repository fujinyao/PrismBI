'use client'

import { useState, useEffect, useRef, useMemo, useCallback, Fragment } from 'react'
import { CommandItem, useCommandPalette } from '@/hooks/useCommandPalette'
import { useI18nStore } from '@/stores/i18nStore'

export function CommandPalette() {
  const { open, setOpen, commands } = useCommandPalette()
  const t = useI18nStore((s) => s.t)
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim()
    const visible = commands.filter((c) => !c.hidden)
    if (!q) return visible
    return visible.filter((c) => {
      const label = (c.labelKey ? t(c.labelKey) : c.label || '').toLowerCase()
      const keywords = (c.keywordsKey || c.keywords || []).join(' ').toLowerCase()
      const category = (c.categoryKey ? t(c.categoryKey) : c.category || '').toLowerCase()
      return label.includes(q) || keywords.includes(q) || category.includes(q)
    })
  }, [commands, query, t])

  useEffect(() => {
    if (open) {
      setQuery('')
      setSelectedIndex(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => {
    setSelectedIndex(0)
  }, [query])

  const execute = useCallback((cmd: CommandItem) => {
    setOpen(false)
    cmd.action()
  }, [setOpen])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter' && filtered[selectedIndex]) {
        e.preventDefault()
        execute(filtered[selectedIndex])
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, filtered, selectedIndex, execute, setOpen])

  useEffect(() => {
    if (selectedIndex >= 0 && listRef.current) {
      const selected = listRef.current.children[selectedIndex] as HTMLElement
      selected?.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex])

  if (!open) return null

  const grouped = filtered.reduce<Record<string, CommandItem[]>>((acc, cmd) => {
    const cat = cmd.categoryKey ? t(cmd.categoryKey) : cmd.category || t('command.category.other', 'Other')
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(cmd)
    return acc
  }, {})

  let flatIndex = 0

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center pt-[20vh]" onClick={() => setOpen(false)}>
      <div className="fixed inset-0 bg-black/50" />
      <div
        className="relative z-10 w-full max-w-lg overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center border-b border-gray-200 px-4 dark:border-gray-700">
          <svg className="h-5 w-5 shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            className="flex-1 border-0 bg-transparent px-3 py-3 text-sm text-gray-900 placeholder-gray-400 outline-none dark:text-gray-100"
            placeholder={t('command.placeholder', 'Type a command or search...')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <kbd className="hidden rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-400 dark:border-gray-600 sm:inline-block">Esc</kbd>
        </div>

        <div ref={listRef} className="max-h-80 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-gray-400">
              {t('command.noResults', 'No commands found.')}
            </div>
          ) : (
            Object.entries(grouped).map(([category, items]) => (
              <Fragment key={category}>
                <div className="px-2 pt-2 pb-1 text-xs font-medium uppercase tracking-wider text-gray-400">
                  {category}
                </div>
                {items.map((cmd) => {
                  const idx = flatIndex++
                  return (
                    <button
                      key={cmd.id}
                      className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                        idx === selectedIndex
                          ? 'bg-primary-50 text-primary dark:bg-primary-900/20 dark:text-primary-400'
                          : 'text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800'
                      }`}
                      onClick={() => execute(cmd)}
                      onMouseEnter={() => setSelectedIndex(idx)}
                    >
                      {cmd.icon && <span className="shrink-0">{cmd.icon}</span>}
                      <span className="flex-1 truncate">{cmd.labelKey ? t(cmd.labelKey) : cmd.label}</span>
                      {cmd.shortcut && (
                        <kbd className="rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-400 dark:border-gray-600">
                          {cmd.shortcut}
                        </kbd>
                      )}
                    </button>
                  )
                })}
              </Fragment>
            ))
          )}
        </div>

        <div className="border-t border-gray-200 px-4 py-2 dark:border-gray-700">
          <div className="flex items-center gap-4 text-xs text-gray-400">
            <span>↑↓ navigate</span>
            <span>↵ select</span>
            <span>esc close</span>
            <span className="ml-auto">Ctrl+K</span>
          </div>
        </div>
      </div>
    </div>
  )
}