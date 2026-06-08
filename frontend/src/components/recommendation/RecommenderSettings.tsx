'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useI18nStore } from '@/stores/i18nStore'

interface RecommenderSettings {
  enabled: boolean
  minRelevance: number
  maxResults: number
}

interface RecommenderSettingsProps {
  settings: RecommenderSettings
  onSave: (settings: RecommenderSettings) => void
  className?: string
}

export function RecommenderSettings({ settings, onSave, className }: RecommenderSettingsProps) {
  const t = useI18nStore((s) => s.t)

  const [enabled, setEnabled] = useState(settings.enabled)
  const [minRelevance, setMinRelevance] = useState(settings.minRelevance)
  const [maxResults, setMaxResults] = useState(settings.maxResults)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({ enabled, minRelevance, maxResults })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={cn('space-y-5', className)}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
            {t('recommenderSettings.enable', 'Enable Recommendations')}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {t('recommenderSettings.enableDesc', 'Automatically suggest questions based on user activity')}
          </p>
        </div>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="peer sr-only"
          />
          <div className="h-6 w-11 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:bg-primary peer-checked:after:translate-x-full dark:bg-gray-700" />
        </label>
      </div>

      <div className="space-y-1">
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
          {t('recommenderSettings.minRelevance', 'Minimum Relevance ({0}%)').replace('{0}', String(minRelevance))}
        </label>
        <input
          type="range"
          min={0}
          max={100}
          value={minRelevance}
          onChange={(e) => setMinRelevance(Number(e.target.value))}
          className="w-full accent-primary"
        />
        <div className="flex justify-between text-xs text-gray-400">
          <span>0%</span>
          <span>100%</span>
        </div>
      </div>

      <Input
        label={t('recommenderSettings.maxResults', 'Max Results')}
        type="number"
        min={1}
        max={100}
        value={maxResults}
        onChange={(e) => setMaxResults(Number(e.target.value))}
      />

      <div className="pt-2">
        <Button onClick={handleSave} loading={saving}>
          {t('recommenderSettings.save', 'Save Settings')}
        </Button>
      </div>
    </div>
  )
}
