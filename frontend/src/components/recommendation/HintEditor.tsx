'use client'

import { useState, useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Tag } from '@/components/ui/Tag'
import { EmptyState } from '@/components/ui/EmptyState'
import { useI18nStore } from '@/stores/i18nStore'

interface Hint {
  id: string
  text: string
  category: string
  weight: number
}

interface HintEditorProps {
  hints: Hint[]
  onAdd: (hint: { text: string; category: string; weight: number }) => void
  onUpdate: (id: string, hint: Partial<Hint>) => void
  onDelete: (id: string) => void
  className?: string
}

export function HintEditor({ hints, onAdd, onUpdate, onDelete, className }: HintEditorProps) {
  const t = useI18nStore((s) => s.t)
  const [newText, setNewText] = useState('')
  const [newCategory, setNewCategory] = useState('general')
  const [newWeight, setNewWeight] = useState(0.5)
  const composingId = useRef<string | null>(null)
  const newComposing = useRef(false)
  const debounceTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const debouncedUpdate = useCallback((id: string, hint: Partial<Hint>) => {
    const key = id + JSON.stringify(hint)
    const existing = debounceTimers.current.get(key)
    if (existing) clearTimeout(existing)
    debounceTimers.current.set(
      key,
      setTimeout(() => {
        debounceTimers.current.delete(key)
        onUpdate(id, hint)
      }, 400),
    )
  }, [onUpdate])

  const handleAdd = () => {
    if (!newText.trim()) return
    onAdd({ text: newText.trim(), category: newCategory, weight: newWeight })
    setNewText('')
    setNewWeight(0.5)
  }

  return (
    <div className={cn('space-y-4', className)}>
      {hints.length === 0 && (
        <EmptyState
          title={t('hintEditor.noHints', 'No hints yet')}
          description={t('hintEditor.noHintsDesc', 'Add preference hints to influence recommendation behavior.')}
        />
      )}

      <div className="space-y-2">
        {hints.map((hint) => (
          <div
            key={hint.id}
            className="rounded-lg border border-gray-200 p-3 dark:border-gray-700"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1 space-y-2">
                <input
                  value={hint.text}
                  onChange={(e) => {
                    if (composingId.current !== hint.id && !(e.nativeEvent as InputEvent).isComposing) debouncedUpdate(hint.id, { text: (e.target as HTMLInputElement).value })
                  }}
                  onCompositionStart={() => { composingId.current = hint.id }}
                  onCompositionEnd={(e) => { composingId.current = null; debouncedUpdate(hint.id, { text: (e.target as HTMLInputElement).value }) }}
                  className="w-full border-b border-transparent bg-transparent text-sm font-medium text-gray-900 focus:border-gray-300 focus:outline-none dark:text-gray-100 dark:focus:border-gray-600"
                  placeholder={t('hintEditor.hintPlaceholder', 'Hint text...')}
                />
                <div className="flex items-center gap-2">
                  <select
                    value={hint.category}
                    onChange={(e) => onUpdate(hint.id, { category: e.target.value })}
                    className="rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300"
                  >
                    <option value="general">{t('hintEditor.categoryGeneral', 'General')}</option>
                    <option value="topic">{t('hintEditor.categoryTopic', 'Topic')}</option>
                    <option value="user">{t('hintEditor.categoryUser', 'User')}</option>
                    <option value="time">{t('hintEditor.categoryTime', 'Time')}</option>
                  </select>
                  <span className="text-xs text-gray-500">{t('hintEditor.weight', 'Weight:')}</span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={hint.weight}
                    onChange={(e) =>
                      onUpdate(hint.id, { weight: Number(e.target.value) })
                    }
                    className="w-20 accent-primary"
                  />
                  <span className="w-8 text-xs text-gray-500">
                    {hint.weight.toFixed(1)}
                  </span>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onDelete(hint.id)}
              >
                {t('hintEditor.delete', 'Delete')}
              </Button>
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-dashed border-gray-300 p-4 dark:border-gray-600">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
          {t('hintEditor.addNewHint', 'Add New Hint')}
        </p>
        <div className="space-y-2">
          <Input
            placeholder={t('hintEditor.addHintPlaceholder', 'e.g. Prefer revenue-related questions')}
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
          />
          <div className="flex items-center gap-3">
            <select
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300"
            >
              <option value="general">{t('hintEditor.categoryGeneral', 'General')}</option>
              <option value="topic">{t('hintEditor.categoryTopic', 'Topic')}</option>
              <option value="user">{t('hintEditor.categoryUser', 'User')}</option>
              <option value="time">{t('hintEditor.categoryTime', 'Time')}</option>
            </select>
            <div className="flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-400">
              <span>{t('hintEditor.weight', 'Weight:')}</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={newWeight}
                onChange={(e) => setNewWeight(Number(e.target.value))}
                className="w-20 accent-primary"
              />
              <span className="w-6 text-xs">{newWeight.toFixed(1)}</span>
            </div>
            <Button size="sm" onClick={handleAdd} disabled={!newText.trim()}>
              {t('hintEditor.add', 'Add')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
