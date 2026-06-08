'use client'

import { useState, useEffect } from 'react'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useI18nStore } from '@/stores/i18nStore'

interface InstructionFormProps {
  open: boolean
  instruction?: any
  onClose: () => void
  onSave: (data: any) => void
}

export function InstructionForm({ open, instruction, onClose, onSave }: InstructionFormProps) {
  const [text, setText] = useState('')
  const [category, setCategory] = useState('')
  const [scope, setScope] = useState<'global' | 'project'>('global')
  const [priority, setPriority] = useState<number>(1)
  const [saving, setSaving] = useState(false)
  const t = useI18nStore((s) => s.t)

  useEffect(() => {
    if (instruction) {
      setText(instruction.text ?? '')
      setCategory(instruction.category ?? '')
      setScope(instruction.scope ?? 'global')
      setPriority(typeof instruction.priority === 'number' ? instruction.priority : 1)
    } else {
      setText('')
      setCategory('')
      setScope('global')
      setPriority(1)
    }
  }, [instruction, open])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    await onSave({ text, category, scope, priority, id: instruction?.id })
    setSaving(false)
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={instruction ? t('knowledge.editInstruction', 'Edit Instruction') : t('knowledge.addInstruction', 'Add Instruction')}
      size="lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('knowledge.instructionText', 'Instruction Text')}
          </label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            className="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-300 placeholder:text-gray-400"
            placeholder={t('knowledge.instructionPlaceholder', 'e.g., Always use ISO date formats in responses...')}
          />
        </div>

        <Input
          label={t('knowledge.category', 'Category')}
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder={t('knowledge.instructionCategoryPlaceholder', 'e.g., Formatting, Naming, Security')}
        />

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('knowledge.scope', 'Scope')}
          </label>
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as 'global' | 'project')}
            className="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-300"
          >
            <option value="global">{t('knowledge.scope.global', 'Global')}</option>
            <option value="project">{t('knowledge.scope.project', 'Project')}</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('knowledge.priority', 'Priority')}
          </label>
          <select
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
            className="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-300"
          >
            <option value={3}>{t('knowledge.priority.p0', 'P0 - Urgent')}</option>
            <option value={2}>{t('knowledge.priority.p1', 'P1 - High')}</option>
            <option value={1}>{t('knowledge.priority.p2', 'P2 - Normal')}</option>
            <option value={0}>{t('knowledge.priority.p3', 'P3 - Low')}</option>
          </select>
        </div>

        <div className="flex justify-end gap-3 pt-4">
          <Button type="button" variant="secondary" onClick={onClose}>{t('knowledge.cancel', 'Cancel')}</Button>
          <Button type="submit" loading={saving} disabled={!text.trim() || !category.trim()}>
            {instruction ? t('knowledge.update', 'Update') : t('knowledge.create', 'Create')}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
