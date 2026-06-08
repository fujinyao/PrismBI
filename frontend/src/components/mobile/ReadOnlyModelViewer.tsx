'use client'

import { useI18nStore } from '@/stores/i18nStore'

interface ReadOnlyModelViewerProps {
  modelName: string
  displayName?: string
  description?: string
  fields: { name: string; type: string; description?: string }[]
  onClose: () => void
}

export function ReadOnlyModelViewer({
  modelName,
  displayName,
  description,
  fields,
  onClose,
}: ReadOnlyModelViewerProps) {
  const t = useI18nStore((s) => s.t)

  return (
    <div className="flex flex-col">
      <div className="border-b border-gray-200 px-4 py-3 dark:border-gray-700">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          {displayName || modelName}
        </h2>
        {description && (
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{description}</p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {fields.map((field) => (
            <div key={field.name} className="px-4 py-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  {field.name}
                </span>
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-mono text-gray-600 dark:bg-gray-800 dark:text-gray-400">
                  {field.type}
                </span>
              </div>
              {field.description && (
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{field.description}</p>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="border-t border-gray-200 px-4 py-3 dark:border-gray-700">
        <button
          onClick={onClose}
          className="w-full rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          {t('common.close', 'Close')}
        </button>
      </div>
    </div>
  )
}