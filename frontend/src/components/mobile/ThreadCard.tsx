'use client'

import { useI18nStore } from '@/stores/i18nStore'

interface ThreadCardProps {
  id: number
  summary: string
  createdAt?: string
  isActive?: boolean
  onClick: (id: number) => void
}

export function ThreadCard({ id, summary, createdAt, isActive, onClick }: ThreadCardProps) {
  const t = useI18nStore((s) => s.t)

  return (
    <button
      onClick={() => onClick(id)}
      className={`w-full rounded-lg border px-4 py-3 text-left transition-colors ${
        isActive
          ? 'border-primary-300 bg-primary-50 dark:border-primary-700 dark:bg-primary-900/20'
          : 'border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:hover:bg-gray-700'
      }`}
    >
      <p className={`line-clamp-2 text-sm font-medium ${
        isActive ? 'text-primary-700 dark:text-primary-300' : 'text-gray-900 dark:text-gray-100'
      }`}>
        {summary || t('thread.newConversation', 'New Conversation')}
      </p>
      {createdAt && (
        <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
          {createdAt}
        </p>
      )}
    </button>
  )
}