'use client'

import { useState, useEffect } from 'react'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface SqlPairFormProps {
  open: boolean
  pair?: any
  onClose: () => void
  onSave: (data: any) => void
}

const sqlKeywords = ['SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON',
  'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'OFFSET', 'AS', 'AND', 'OR', 'NOT', 'IN', 'BETWEEN',
  'LIKE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'DISTINCT',
  'UNION', 'ALL', 'EXISTS', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'TABLE', 'ALTER', 'DROP',
  'INDEX', 'VIEW', 'WITH', 'RECURSIVE', 'CAST', 'NULL', 'IS', 'TRUE', 'FALSE']

function highlightSql(sql: string): React.ReactNode {
  const regex = new RegExp(`\\b(${sqlKeywords.join('|')})\\b`, 'gi')
  const parts = sql.split(/(\s+)/)
  return parts.map((part, i) => {
    if (sqlKeywords.includes(part.toUpperCase())) {
      return <span key={i} className="text-primary-600 dark:text-primary-400 font-semibold">{part}</span>
    }
    return <span key={i}>{part}</span>
  })
}

export function SqlPairForm({ open, pair, onClose, onSave }: SqlPairFormProps) {
  const [question, setQuestion] = useState('')
  const [sql, setSql] = useState('')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState('')
  const [scope, setScope] = useState<'global' | 'project'>('global')
  const [saving, setSaving] = useState(false)
  const t = useI18nStore((s) => s.t)

  useEffect(() => {
    if (pair) {
      setQuestion(pair.question ?? '')
      setSql(pair.sql ?? '')
      setDescription(pair.description ?? '')
      setCategory(pair.category ?? '')
      setScope(pair.scope ?? 'global')
    } else {
      setQuestion('')
      setSql('')
      setDescription('')
      setCategory('')
      setScope('global')
    }
  }, [pair, open])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    await onSave({ question, sql, description, category, scope, id: pair?.id })
    setSaving(false)
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={pair ? t('knowledge.editSqlPair', 'Edit SQL Pair') : t('knowledge.addSqlPair', 'Add SQL Pair')}
      size="xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('knowledge.question', 'Question')}
          </label>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
            className="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-300 placeholder:text-gray-400"
            placeholder={t('knowledge.questionPlaceholder', "e.g., Show total revenue by month for 2025")}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('knowledge.sqlQuery', 'SQL Query')}
          </label>
          <div
            className={cn(
              'relative rounded-md border border-gray-300 dark:border-gray-600',
              'focus-within:ring-2 focus-within:ring-primary-300',
            )}
          >
            <div className="pointer-events-none absolute inset-0 overflow-auto p-3 font-mono text-sm whitespace-pre-wrap break-all">
              {sql ? highlightSql(sql) : null}
            </div>
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              rows={6}
              className="relative block w-full bg-transparent p-3 font-mono text-sm text-transparent caret-gray-900 dark:caret-gray-100 focus:outline-none resize-y"
              placeholder={t('knowledge.sqlPlaceholder', "SELECT DATE_TRUNC('month', order_date) AS month, SUM(revenue) AS total_revenue FROM orders WHERE YEAR(order_date) = 2025 GROUP BY 1 ORDER BY 1")}
              spellCheck={false}
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            {t('knowledge.descriptionOptional', 'Description (optional)')}
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-300 placeholder:text-gray-400"
            placeholder={t('knowledge.descriptionPlaceholder', 'What does this query do?')}
          />
        </div>

        <Input
          label={t('knowledge.category', 'Category')}
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder={t('knowledge.categoryPlaceholder', 'e.g., Revenue, Users, Inventory')}
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

        <div className="flex justify-end gap-3 pt-4">
          <Button type="button" variant="secondary" onClick={onClose}>{t('knowledge.cancel', 'Cancel')}</Button>
          <Button type="submit" loading={saving} disabled={!question.trim() || !sql.trim() || !category.trim()}>
            {pair ? t('knowledge.update', 'Update') : t('knowledge.create', 'Create')}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
