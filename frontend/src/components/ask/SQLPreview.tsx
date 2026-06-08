'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { sql as sqlLang, PostgreSQL } from '@codemirror/lang-sql'
import { oneDark } from '@codemirror/theme-one-dark'
import { keymap, EditorView } from '@codemirror/view'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'

interface SQLPreviewProps {
  sql: string
  expanded?: boolean
}

export function SQLPreview({ sql, expanded = false }: SQLPreviewProps) {
  const t = useI18nStore((s) => s.t)
  const editorRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const [isExpanded, setIsExpanded] = useState(expanded)
  const [copied, setCopied] = useState(false)
  const isLongQuery = sql.length > 300

  useEffect(() => {
    if (!editorRef.current) return
    if (viewRef.current) {
      viewRef.current.destroy()
    }

    const state = EditorState.create({
      doc: sql,
      extensions: [
        basicSetup,
        sqlLang(),
        oneDark,
        EditorView.editable.of(false),
        EditorView.theme({
          '&': { maxHeight: isExpanded ? 'none' : '200px', overflowY: 'auto' },
          '.cm-scroller': { fontFamily: '"JetBrains Mono", "Fira Code", monospace', fontSize: '13px' },
          '.cm-gutters': { borderRight: '1px solid #374151' },
        }),
        keymap.of([
          { key: 'Mod-c', run: () => { navigator.clipboard.writeText(sql); return true } },
        ]),
      ],
    })

    viewRef.current = new EditorView({
      state,
      parent: editorRef.current,
    })

    return () => {
      viewRef.current?.destroy()
      viewRef.current = null
    }
  }, [sql, isExpanded])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(sql)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // fallback
    }
  }, [sql])

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="flex items-center justify-between bg-gray-50 px-3 py-2 dark:bg-gray-800">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">{t('sqlPreview.title', 'SQL')}</span>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={handleCopy}>
            {copied ? t('chat.copied', 'Copied') : t('chat.copy', 'Copy')}
          </Button>
          {isLongQuery && (
            <Button variant="ghost" size="sm" onClick={() => setIsExpanded((v) => !v)}>
              {isExpanded ? t('sqlPreview.collapse', 'Collapse') : t('sqlPreview.expand', 'Expand')}
            </Button>
          )}
        </div>
      </div>
      <div ref={editorRef} className={cn(isExpanded ? '' : 'max-h-[200px]')} />
    </div>
  )
}
