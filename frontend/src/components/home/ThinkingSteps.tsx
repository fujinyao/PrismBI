'use client'

import { useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'
import type { ChatProcessStep, ThreadResponseAskingTask, ThreadResponseBreakdownDetail } from '@/lib/api'

interface ThinkingStepsProps {
  askingTask?: ThreadResponseAskingTask
  breakdownDetail?: ThreadResponseBreakdownDetail | null
  isPending?: boolean
  liveSteps?: { key: string; detail?: string }[]
}

const DEFAULT_PENDING_STEP_CONFIG = [
  {
    key: 'understand',
    titleKey: 'thinking.understand',
    titleFallback: 'Understand question',
    detailKey: 'thinking.detail.understand',
    detailFallback: 'Identifying intent and project context.',
  },
  {
    key: 'retrieve',
    titleKey: 'thinking.retrieve',
    titleFallback: 'Retrieve semantic models',
    detailKey: 'thinking.detail.retrieve',
    detailFallback: 'Matching models, fields, and relationships.',
  },
  {
    key: 'organize',
    titleKey: 'thinking.organize',
    titleFallback: 'Organize query plan',
    detailKey: 'thinking.detail.organize',
    detailFallback: 'Deciding whether SQL or a direct answer is needed.',
  },
  {
    key: 'answer',
    titleKey: 'thinking.answer',
    titleFallback: 'Generate answer',
    detailKey: 'thinking.detail.answer',
    detailFallback: 'Waiting for query results or completing content.',
  },
] as const

const STEP_TITLE_KEY_MAP: Record<string, { key: string; fallback: string }> = {
  understand: { key: 'thinking.understand', fallback: 'Understand question' },
  understand_question: { key: 'thinking.understand', fallback: 'Understand question' },
  retrieve: { key: 'thinking.retrieve', fallback: 'Retrieve semantic models' },
  retrieve_metadata: { key: 'thinking.retrieve', fallback: 'Retrieve semantic models' },
  organize: { key: 'thinking.organize', fallback: 'Organize query plan' },
  route_or_answer: { key: 'thinking.routeOrAnswer', fallback: 'Route or answer' },
  answer: { key: 'thinking.answer', fallback: 'Generate answer' },
}

function buildDefaultPendingSteps(t: (key: string, fallback?: string) => string): ChatProcessStep[] {
  return DEFAULT_PENDING_STEP_CONFIG.map((item) => ({
    key: item.key,
    title: t(item.titleKey, item.titleFallback),
    status: 'PENDING',
    detail: t(item.detailKey, item.detailFallback),
  }))
}

function resolveStepTitle(step: ChatProcessStep, t: (key: string, fallback?: string) => string) {
  const rawKey = String(step.key ?? '').toLowerCase()
  const mapped = STEP_TITLE_KEY_MAP[rawKey]
  if (mapped) {
    return t(mapped.key, step.title ?? mapped.fallback)
  }
  return step.title || step.key || t('thinking.step', 'Step')
}

function normalizeStatus(status?: string) {
  const value = String(status || '').toUpperCase()
  if (['FINISHED', 'DONE', 'SUCCESS'].includes(value)) return 'FINISHED'
  if (['FAILED', 'ERROR'].includes(value)) return 'FAILED'
  if (['RUNNING', 'PROCESSING', 'STREAMING', 'GENERATING', 'SEARCHING', 'PLANNING'].includes(value)) return 'RUNNING'
  return 'PENDING'
}

function stepClasses(status?: string) {
  const normalized = normalizeStatus(status)
  if (normalized === 'FINISHED') return 'border-primary bg-primary text-white'
  if (normalized === 'FAILED') return 'border-error-500 bg-error-500 text-white'
  if (normalized === 'RUNNING') return 'border-primary bg-white text-primary dark:bg-gray-900'
  return 'border-gray-300 bg-white text-gray-400 dark:border-gray-600 dark:bg-gray-900'
}

export function ThinkingSteps({ askingTask, breakdownDetail, isPending, liveSteps }: ThinkingStepsProps) {
  const t = useI18nStore((s) => s.t)
  const taskStatus = normalizeStatus(askingTask?.status)
  const finished = !isPending && ['FINISHED', 'FAILED'].includes(taskStatus)
  const [expanded, setExpanded] = useState(!finished)
  const [pendingTick, setPendingTick] = useState(0)
  const defaultPendingSteps = useMemo(() => buildDefaultPendingSteps(t), [t])

  const steps = useMemo(() => {
    const explicit = askingTask?.processSteps?.length
      ? askingTask.processSteps
      : breakdownDetail?.processSteps?.length
        ? breakdownDetail.processSteps
        : []
    if (explicit.length > 0) return explicit
    if (liveSteps && liveSteps.length > 0 && (isPending || !finished)) {
      const liveKeys = liveSteps.map((s) => s.key)
      return defaultPendingSteps.map((step) => {
        const liveIndex = liveKeys.indexOf(step.key ?? '')
        if (liveIndex < 0) return { ...step, status: 'PENDING' as const }
        const liveStep = liveSteps[liveIndex]!
        const detail = liveStep.detail
        return {
          ...step,
          status: liveIndex < liveKeys.length - 1 ? ('FINISHED' as const) : ('RUNNING' as const),
          detail: detail || step.detail || undefined,
        }
      })
    }
    if (isPending || !finished) {
      const activeIndex = Math.min(pendingTick, defaultPendingSteps.length - 1)
      return defaultPendingSteps.map((step, index) => ({
        ...step,
        status: index < activeIndex ? 'FINISHED' : index === activeIndex ? 'RUNNING' : 'PENDING',
      }))
    }
    return [
      { key: 'understand', title: t('thinking.understand', 'Understand question'), status: taskStatus, detail: askingTask?.intentReasoning },
      { key: 'answer', title: t('thinking.answer', 'Generate answer'), status: taskStatus, detail: breakdownDetail?.description },
    ]
  }, [askingTask, breakdownDetail, defaultPendingSteps, finished, isPending, liveSteps, pendingTick, taskStatus, t])

  useEffect(() => {
    setExpanded(!finished)
  }, [finished])

  useEffect(() => {
    if (!isPending) {
      setPendingTick(0)
      return
    }
    const timer = window.setInterval(() => {
      setPendingTick((value) => Math.min(value + 1, defaultPendingSteps.length - 1))
    }, 1200)
    return () => window.clearInterval(timer)
  }, [defaultPendingSteps.length, isPending])

  if (!askingTask && !breakdownDetail && !isPending) return null

  const statusText = finished ? t('thinking.finished', 'Finished') : t('thinking.processing', 'Thinking')

  return (
    <div role="region" aria-label={t('thinking.title', 'Answer preparation steps')} aria-live="polite" className="overflow-hidden rounded-lg border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-900">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <div className="flex items-center gap-2">
          <span className={cn('h-2 w-2 rounded-full', finished ? 'bg-primary' : 'bg-primary motion-safe:animate-pulse')} />
          <span className="text-sm font-medium text-gray-800 dark:text-gray-100">
            {t('thinking.title', 'Answer preparation steps')}
          </span>
          <span className="rounded-full bg-white px-2 py-0.5 text-xs text-gray-500 dark:bg-gray-800 dark:text-gray-400">
            {statusText}
          </span>
        </div>
        <span className={cn('text-xs text-gray-500 transition-transform', expanded && 'rotate-180')}>v</span>
      </button>

      {expanded && (
        <div className="border-t border-gray-200 px-3 py-3 dark:border-gray-700">
          <ol className="space-y-3">
            {steps.map((step, index) => {
              const normalized = normalizeStatus(step.status)
              return (
                <li key={step.key || `${step.title}-${index}`} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <span className={cn('flex h-5 w-5 items-center justify-center rounded-full border text-[10px]', stepClasses(step.status))}>
                      {normalized === 'FINISHED' ? '✓' : normalized === 'FAILED' ? '!' : index + 1}
                    </span>
                    {index < steps.length - 1 && <span className="mt-1 h-full w-px bg-gray-200 dark:bg-gray-700" />}
                  </div>
                  <div className="min-w-0 flex-1 pb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-800 dark:text-gray-100">
                        {resolveStepTitle(step, t)}
                      </span>
                      {normalized === 'RUNNING' && (
                        <span className="text-xs text-primary">{t('thinking.running', 'Running...')}</span>
                      )}
                    </div>
                    {step.detail && (
                      <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-gray-600 dark:text-gray-400">
                        {step.detail}
                      </p>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </div>
  )
}
