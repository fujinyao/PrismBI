'use client'

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ThinkingSteps } from '@/components/home/ThinkingSteps'
import { ChartContainer } from '@/components/chart/ChartContainer'
import dynamic from 'next/dynamic'

const ChartEditor = dynamic(
  () => import('@/components/chart/ChartEditor').then((m) => ({ default: m.ChartEditor })),
  { ssr: false, loading: () => <div className="h-32 animate-pulse rounded bg-gray-100 dark:bg-gray-800" /> },
)
import { useI18nStore } from '@/stores/i18nStore'
import { dashboardApi, queryApi, type QueryResult, type ThreadResponse } from '@/lib/api'
import { queryMetricsQueryKey } from '@/lib/queryMetrics'
import { useProjectStore } from '@/stores/projectStore'
import { useToast } from '@/components/ui/Toast'

type ResponseTab = 'answer' | 'data' | 'chart' | 'sql'

function safeMarkdownUrl(url: string) {
  const trimmed = url.trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('/') || trimmed.startsWith('#')) return trimmed
  try {
    const parsed = new URL(trimmed)
    return ['http:', 'https:', 'mailto:', 'tel:'].includes(parsed.protocol) ? trimmed : ''
  } catch {
    return ''
  }
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.replace(/,/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function isDateLike(value: unknown): boolean {
  if (typeof value !== 'string') return false
  if (!/^\d{4}[-/年]\d{1,2}([-月/]\d{1,2})?/.test(value.trim())) return false
  return Number.isFinite(Date.parse(value.replace(/[年月]/g, '-').replace('日', '')))
}

function inferChartTitle(question: string, columns: string[]) {
  const questionText = question.replace(/[?？。.!！]/g, '').trim()
  const hasProduct = columns.some((column) => /product|category|产品|品类|类别/i.test(column))
  const hasCity = columns.some((column) => /city|region|state|城市|地区|区域/i.test(column))
  const hasSales = columns.some((column) => /sales|revenue|amount|total|销售额|收入|金额/i.test(column))
  if (hasProduct && hasCity && hasSales) return 'Product sales by city'
  if (hasProduct && hasSales) return 'Product sales'
  if (hasCity && hasSales) return 'Sales by city'
  if (questionText.length > 0 && questionText.length <= 24) return questionText
  if (questionText.length > 24) return `${questionText.slice(0, 24)}...`
  return 'Query result chart'
}

function inferChartSpec(columns: string[], rows: Record<string, unknown>[], question: string) {
  if (!columns.length || !rows.length) return null
  const sampleRows = rows.slice(0, 50)
  const measures = columns.filter((column) => {
    const values = sampleRows.map((row) => row[column]).filter((value) => value !== null && value !== undefined)
    if (!values.length) return false
    return values.filter((value) => toNumber(value) !== null).length / values.length >= 0.8
  })
  const temporals = columns.filter((column) => {
    const values = sampleRows.map((row) => row[column]).filter((value) => value !== null && value !== undefined)
    return values.length > 0 && values.filter(isDateLike).length / values.length >= 0.6
  })
  const dimensions = columns.filter((column) => !measures.includes(column) && !temporals.includes(column))
  const preferredMeasure = measures.find((column) => /sales|revenue|amount|total|value|销售额|金额|收入/i.test(column)) ?? measures[0]
  const secondMeasure = measures.find((column) => column !== preferredMeasure)
  const preferredDimension = dimensions.find((column) => /category|product|name|type|产品|品类|类别/i.test(column)) ?? dimensions[0]
  const colorDimension = dimensions.find((column) => column !== preferredDimension && /city|region|state|country|城市|地区|区域/i.test(column)) ?? dimensions.find((column) => column !== preferredDimension)

  if (temporals[0] && preferredMeasure) {
    return {
      title: inferChartTitle(question, columns),
      mark: { type: 'line', point: true, tooltip: true },
      encoding: {
        x: { field: temporals[0], type: 'temporal', title: temporals[0] },
        y: { field: preferredMeasure, type: 'quantitative', aggregate: 'sum', title: preferredMeasure },
        ...(colorDimension ? { color: { field: colorDimension, type: 'nominal', title: colorDimension } } : {}),
        tooltip: columns.slice(0, 8).map((field) => ({ field, type: measures.includes(field) ? 'quantitative' : temporals.includes(field) ? 'temporal' : 'nominal' })),
      },
    }
  }

  if (preferredDimension && preferredMeasure) {
    return {
      title: inferChartTitle(question, columns),
      mark: { type: 'bar', tooltip: true },
      encoding: {
        x: { field: preferredDimension, type: 'nominal', sort: '-y', title: preferredDimension, axis: { labelAngle: -35 } },
        y: { field: preferredMeasure, type: 'quantitative', aggregate: 'sum', title: preferredMeasure },
        ...(colorDimension ? { color: { field: colorDimension, type: 'nominal', title: colorDimension } } : {}),
        tooltip: columns.slice(0, 8).map((field) => ({ field, type: measures.includes(field) ? 'quantitative' : 'nominal' })),
      },
    }
  }

  if (preferredMeasure && secondMeasure) {
    return {
      title: inferChartTitle(question, columns),
      mark: { type: 'point', tooltip: true, filled: true },
      encoding: {
        x: { field: preferredMeasure, type: 'quantitative', title: preferredMeasure },
        y: { field: secondMeasure, type: 'quantitative', title: secondMeasure },
        ...(preferredDimension ? { color: { field: preferredDimension, type: 'nominal', title: preferredDimension } } : {}),
      },
    }
  }

  const firstColumn = columns[0]
  return firstColumn
    ? {
        title: inferChartTitle(question, columns),
        mark: { type: 'bar', tooltip: true },
        encoding: {
          x: { field: firstColumn, type: 'nominal', title: firstColumn },
          y: { aggregate: 'count', type: 'quantitative', title: 'count' },
        },
      }
    : null
}

function inferChartFields(columns: string[], rows: Record<string, unknown>[]) {
  const sampleRows = rows.slice(0, 50)
  return columns.map((column) => {
    const values = sampleRows.map((row) => row[column]).filter((value) => value !== null && value !== undefined)
    const numericRatio = values.length ? values.filter((value) => toNumber(value) !== null).length / values.length : 0
    const temporalRatio = values.length ? values.filter(isDateLike).length / values.length : 0
    return {
      key: column,
      label: column,
      type: temporalRatio >= 0.6 ? 'temporal' as const : numericRatio >= 0.8 ? 'measure' as const : 'dimension' as const,
    }
  })
}

function sqlEditorHeight(sql: string) {
  const lines = Math.max(8, sql.split('\n').length + 2)
  return Math.min(520, Math.max(192, lines * 22))
}

function normalizeSqlForComparison(sql: string) {
  return sql.replace(/\s+/g, ' ').trim()
}

function stringifyPreviewCell(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') {
    const compact = value.replace(/\s+/g, ' ').trim()
    return compact.length > 80 ? `${compact.slice(0, 77)}...` : compact || '-'
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    const serialized = JSON.stringify(value)
    if (!serialized) return '-'
    return serialized.length > 80 ? `${serialized.slice(0, 77)}...` : serialized
  } catch {
    return String(value)
  }
}

function escapeMarkdownCell(value: string): string {
  return value.replace(/\|/g, '\\|').replace(/\n/g, ' ')
}

function buildEditedSqlSummary(result: QueryResult, locale: string, previewRowLimit: number): string {
  const useChinese = locale.toLowerCase().startsWith('zh')
  const columns = Array.isArray(result.columns) ? result.columns.map((column) => String(column)) : []
  const rows = Array.isArray(result.rows) ? result.rows : []
  const totalRows = typeof result.total_rows === 'number' && Number.isFinite(result.total_rows)
    ? result.total_rows
    : rows.length
  const shownRows = Math.min(rows.length, Math.max(previewRowLimit, 0))
  const executionTime = typeof result.execution_time_ms === 'number' && Number.isFinite(result.execution_time_ms)
    ? Math.round(result.execution_time_ms)
    : null
  const warning = typeof result.warning === 'string' ? result.warning.trim() : ''

  const lines: string[] = []
  if (useChinese) {
    lines.push('### 已根据编辑后的 SQL 刷新结果')
    lines.push(`- 共返回 **${totalRows}** 行、**${columns.length}** 列，当前预览 **${shownRows}** 行。`)
    if (executionTime !== null) {
      lines.push(`- 执行耗时约 **${executionTime} ms**。`)
    }
  } else {
    lines.push('### Refreshed from edited SQL')
    lines.push(`- Returned **${totalRows}** rows across **${columns.length}** columns, showing **${shownRows}** preview rows.`)
    if (executionTime !== null) {
      lines.push(`- Execution time: **${executionTime} ms**.`)
    }
  }

  if (!columns.length) {
    lines.push(useChinese ? '- 结果没有可展示的字段。' : '- The result does not contain displayable columns.')
    if (warning) lines.push(useChinese ? `- 提示: ${warning}` : `- Warning: ${warning}`)
    return lines.join('\n')
  }

  const previewColumns = columns.slice(0, Math.min(6, columns.length))
  const previewRows = rows.slice(0, Math.min(3, shownRows))

  if (previewRows.length > 0) {
    lines.push('')
    lines.push(useChinese ? '#### 数据预览' : '#### Data preview')
    lines.push(`| ${previewColumns.map(escapeMarkdownCell).join(' | ')} |`)
    lines.push(`| ${previewColumns.map(() => '---').join(' | ')} |`)
    previewRows.forEach((row) => {
      const values = previewColumns.map((column) => escapeMarkdownCell(stringifyPreviewCell(row[column])))
      lines.push(`| ${values.join(' | ')} |`)
    })
  } else {
    lines.push(useChinese ? '- 查询已执行，但当前没有可预览的数据行。' : '- Query executed, but no preview rows are available.')
  }

  if (columns.length > previewColumns.length) {
    lines.push(
      useChinese
        ? `- 表格仅展示前 ${previewColumns.length} 列，完整字段可在 Data 选项卡查看。`
        : `- Table preview shows the first ${previewColumns.length} columns. Open the Data tab for the full result.`,
    )
  }
  if (warning) lines.push(useChinese ? `- 提示: ${warning}` : `- Warning: ${warning}`)
  return lines.join('\n')
}

interface ResponseSaveOverrides {
  sql?: string
  answerContent?: string | null
  columns?: string[]
}

interface ResponseCardProps {
  response: ThreadResponse
  onReRun?: (responseId: number) => void
  onSaveAsView?: (responseId: number, overrides?: ResponseSaveOverrides) => void
  onSaveAsPair?: (responseId: number, overrides?: ResponseSaveOverrides) => void
  isPending?: boolean
  liveSteps?: { key: string; detail?: string }[]
  className?: string
}

const DASHBOARD_LIST_SHORT_CACHE_MS = 5000

export function ResponseCard({
  response,
  onReRun,
  onSaveAsView,
  onSaveAsPair,
  isPending,
  liveSteps,
  className,
}: ResponseCardProps) {
  const t = useI18nStore((s) => s.t)
  const locale = useI18nStore((s) => s.locale)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const currentProject = useProjectStore((s) => s.currentProject)
  const answerDetail = response.answerDetail
  const askingTask = response.askingTask
  const breakdownDetail = response.breakdownDetail
  const initialSql = response.askingTask?.invalidSql || response.sql || ''
  const error = answerDetail?.error || askingTask?.error
  const persistedSummary = answerDetail?.content
  const preferredTab: ResponseTab = answerDetail?.status === 'FAILED' && initialSql ? 'sql' : persistedSummary ? 'answer' : 'data'
  const [activeTab, setActiveTab] = useState<ResponseTab>(preferredTab)
  const [userSelectedTab, setUserSelectedTab] = useState(false)
  const [editableSql, setEditableSql] = useState(initialSql)
  const [lastExecutedSql, setLastExecutedSql] = useState(initialSql)
  const [userEditedSql, setUserEditedSql] = useState(false)
  const [runSqlConfirmOpen, setRunSqlConfirmOpen] = useState(false)
  const [localResult, setLocalResult] = useState<QueryResult | null>(null)
  const [queryError, setQueryError] = useState<string | null>(null)
  const [runningSql, setRunningSql] = useState(false)
  const [isChartEditMode, setIsChartEditMode] = useState(false)
  const [pinDialogOpen, setPinDialogOpen] = useState(false)
  const [selectedDashboardId, setSelectedDashboardId] = useState<number | ''>('')
  const executableSql = editableSql.trim()
  const isSqlDirty = useMemo(
    () => normalizeSqlForComparison(editableSql) !== normalizeSqlForComparison(lastExecutedSql),
    [editableSql, lastExecutedSql],
  )
  const columns = useMemo(() => localResult?.columns ?? answerDetail?.columns ?? [], [localResult?.columns, answerDetail?.columns])
  const rows = useMemo(() => localResult?.rows ?? answerDetail?.rows ?? [], [localResult?.rows, answerDetail?.rows])
  const totalRows = localResult?.total_rows ?? answerDetail?.totalRows ?? rows.length
  const previewRowLimit = answerDetail?.previewRowLimit ?? 20
  const executionTimeMs = localResult?.execution_time_ms ?? answerDetail?.executionTimeMs
  const summary = useMemo(
    () => (localResult ? buildEditedSqlSummary(localResult, locale, previewRowLimit) : persistedSummary),
    [localResult, locale, previewRowLimit, persistedSummary],
  )
  const hasResultView = Boolean(summary || lastExecutedSql || columns.length > 0 || queryError)
  const inferredChartSpec = useMemo(() => inferChartSpec(columns, rows, response.question), [columns, rows, response.question])
  const [chartSpecOverride, setChartSpecOverride] = useState<any | null>(null)
  const chartSpec = chartSpecOverride ?? inferredChartSpec
  const chartFields = useMemo(() => inferChartFields(columns, rows), [columns, rows])
  const chartRows = useMemo(() => rows.slice(0, previewRowLimit), [rows, previewRowLimit])
  const { data: dashboards = [], isFetching: dashboardsLoading } = useQuery({
    queryKey: ['dashboards', currentProject?.id],
    queryFn: () => {
      const id = currentProject?.id
      if (!id) return []
      return dashboardApi.list({ project_id: id }) as Promise<{ id: number; name?: string }[]>
    },
    enabled: Boolean(currentProject?.id) && pinDialogOpen,
    staleTime: DASHBOARD_LIST_SHORT_CACHE_MS,
    gcTime: DASHBOARD_LIST_SHORT_CACHE_MS * 20,
    refetchOnWindowFocus: false,
  })
  const pinChart = useMutation({
    mutationFn: (dashboardId: number) => dashboardApi.items.create({
      dashboard_id: dashboardId,
        title: response.question.slice(0, 80),
        data_source: lastExecutedSql || undefined,
        response_id: response.id,
        chart_config: {
          spec: chartSpec,
          sql: lastExecutedSql || undefined,
          columns,
          rows: chartRows,
          preview_row_limit: previewRowLimit,
          source_response_id: response.id,
      },
    }),
    onSuccess: (_data, dashboardId) => {
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId] })
      queryClient.invalidateQueries({ queryKey: ['dashboards', currentProject?.id] })
      toast(t('response.chartPinned', 'Chart pinned to dashboard'), 'success')
      setPinDialogOpen(false)
      setSelectedDashboardId('')
    },
    onError: (err) => toast(err instanceof Error ? err.message : t('response.chartPinFailed', 'Failed to pin chart'), 'error'),
  })
  const tabs = [
    summary ? { key: 'answer' as const, label: t('response.answer', 'Answer'), enabled: true } : null,
    { key: 'data' as const, label: t('response.data', 'Data'), enabled: columns.length > 0 },
    { key: 'chart' as const, label: t('response.chart', 'Chart'), enabled: columns.length > 0 && rows.length > 0 },
    { key: 'sql' as const, label: t('response.sql', 'SQL'), enabled: Boolean(lastExecutedSql || editableSql || queryError) },
  ].filter(Boolean) as { key: ResponseTab; label: string; enabled: boolean }[]

  useEffect(() => {
    setUserSelectedTab(false)
    setUserEditedSql(false)
    setRunSqlConfirmOpen(false)
    if (!userSelectedTab) setActiveTab(preferredTab)
    if (!userEditedSql) setEditableSql(initialSql)
    setLastExecutedSql(initialSql)
    setLocalResult(null)
    setQueryError(null)
    setChartSpecOverride(null)
    setIsChartEditMode(false)
    setPinDialogOpen(false)
  }, [response.id])

  useEffect(() => {
    if (!userSelectedTab) setActiveTab(preferredTab)
  }, [preferredTab])

  useEffect(() => {
    if (!userEditedSql) setEditableSql(initialSql)
    if (!localResult) setLastExecutedSql(initialSql)
  }, [initialSql, localResult, userEditedSql])

  const handleAdjustSql = () => {
    setEditableSql(lastExecutedSql || editableSql)
    setUserEditedSql(false)
    setActiveTab('sql')
    setUserSelectedTab(true)
    setQueryError(null)
  }

  const executeSql = async (sql: string) => {
    const projectId = currentProject?.id
    if (!projectId || !sql || runningSql) return
    setRunningSql(true)
    setQueryError(null)
    try {
      const result = await queryApi.execute(sql, projectId, previewRowLimit)
      setLocalResult(result)
      setLastExecutedSql(sql)
      setEditableSql(sql)
      setUserEditedSql(false)
      setChartSpecOverride(null)
      setIsChartEditMode(false)
      setPinDialogOpen(false)
      setActiveTab('answer')
      setUserSelectedTab(true)
      if (typeof projectId === 'number' && projectId > 0) {
        queryClient.invalidateQueries({ queryKey: queryMetricsQueryKey(projectId) })
      }
    } catch (err) {
      setQueryError(err instanceof Error ? err.message : String(err))
      setActiveTab('sql')
    } finally {
      setRunningSql(false)
    }
  }

  const handleRunSql = () => {
    if (!currentProject?.id || !executableSql || runningSql) return
    if (isSqlDirty) {
      setRunSqlConfirmOpen(true)
      return
    }
    void executeSql(executableSql)
  }

  const handleConfirmRunSql = () => {
    if (!executableSql || runningSql) return
    setRunSqlConfirmOpen(false)
    void executeSql(executableSql)
  }

  const handleResetChart = () => {
    setChartSpecOverride(null)
    setIsChartEditMode(false)
  }

  const chartMarkType = chartSpec?.mark?.type ?? chartSpec?.mark ?? 'bar'
  const normalizedChartSpec = chartMarkType === 'scatter'
    ? { ...chartSpec, mark: { ...(typeof chartSpec?.mark === 'object' ? chartSpec.mark : {}), type: 'point' } }
    : chartSpec
  const sqlHeight = sqlEditorHeight(editableSql || lastExecutedSql)
  const visibleWarning = localResult?.warning ?? (answerDetail?.status === 'FINISHED' && rows.length > 0 ? answerDetail?.error : undefined)
  return (
    <div className={cn('rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-950', className)}>
      <div className="flex items-start gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-medium text-white">
          U
        </div>
        <div className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-gray-50 p-3 text-sm text-gray-900 dark:bg-gray-900 dark:text-gray-100">
          {response.question}
        </div>
      </div>

      <div className="mt-4 flex items-start gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-200 text-sm font-medium text-gray-600 dark:bg-gray-700 dark:text-gray-300">
          AI
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          {error && answerDetail?.status === 'FAILED' && !initialSql ? (
            <div className="rounded-md bg-error-50 p-3 text-sm text-error-700 dark:bg-error-900/30 dark:text-error-400">
              {error}
            </div>
          ) : (
            <>
              {askingTask && (
                <div className="flex flex-wrap gap-2 text-xs text-gray-500 dark:text-gray-400">
                  <span className="rounded-full bg-gray-100 px-2 py-1 dark:bg-gray-800">{t('response.type.' + (askingTask.type || 'ASK'), askingTask.type || 'ASK')}</span>
                  <span className="rounded-full bg-gray-100 px-2 py-1 dark:bg-gray-800">{t('response.status.' + (askingTask.status || 'UNKNOWN'), askingTask.status || 'UNKNOWN')}</span>
                  {askingTask.retrievedTables && askingTask.retrievedTables.length > 0 && (
                    <span className="rounded-full bg-gray-100 px-2 py-1 dark:bg-gray-800">
                      {t('response.tables', 'Tables')}: {askingTask.retrievedTables.join(', ')}
                    </span>
                  )}
                </div>
              )}

              <ThinkingSteps askingTask={askingTask} breakdownDetail={breakdownDetail} isPending={isPending} liveSteps={liveSteps} />

              {hasResultView && (
                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">
                        {t('response.resultView', 'Result')}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {tabs.find((tab) => tab.key === activeTab)?.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 rounded-md bg-white p-1 dark:bg-gray-900">
                      {tabs.map((tab) => (
                        <button
                          key={tab.key}
                          type="button"
                          onClick={() => { setActiveTab(tab.key); setUserSelectedTab(true) }}
                          disabled={!tab.enabled}
                          className={cn(
                            'rounded px-2 py-1 text-xs font-medium',
                            activeTab === tab.key ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800',
                            !tab.enabled && 'cursor-not-allowed opacity-50',
                          )}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {activeTab === 'answer' && summary ? (
                    <div className="p-4 text-sm leading-relaxed text-gray-700 dark:text-gray-300">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        urlTransform={safeMarkdownUrl}
                        components={{
                          h3: ({ children }) => <h3 className="mb-2 mt-4 first:mt-0 text-sm font-semibold text-gray-900 dark:text-gray-100">{children}</h3>,
                          p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
                          strong: ({ children }) => <strong className="font-semibold text-gray-900 dark:text-gray-100">{children}</strong>,
                          ul: ({ children }) => <ul className="mb-3 space-y-1 pl-4">{children}</ul>,
                          li: ({ children }) => <li className="list-disc">{children}</li>,
                          table: ({ children }) => <div className="mb-4 overflow-x-auto rounded-md border border-gray-200 dark:border-gray-700"><table className="min-w-full divide-y divide-gray-200 text-xs dark:divide-gray-700">{children}</table></div>,
                          thead: ({ children }) => <thead className="bg-gray-50 dark:bg-gray-900">{children}</thead>,
                          th: ({ children }) => <th className="px-3 py-2 text-left font-semibold text-gray-600 dark:text-gray-300">{children}</th>,
                          td: ({ children }) => <td className="px-3 py-2 text-gray-700 dark:text-gray-300">{children}</td>,
                        }}
                      >
                        {summary}
                      </ReactMarkdown>
                    </div>
                  ) : activeTab === 'sql' ? (
                    <div className="space-y-3 p-3">
                      <textarea
                        value={editableSql}
                        onChange={(event) => { setEditableSql(event.target.value); setUserEditedSql(true) }}
                        style={{ height: sqlHeight, maxHeight: 520 }}
                        className="w-full resize-y overflow-auto rounded-md border border-gray-300 bg-gray-950 p-3 font-mono text-xs leading-relaxed text-gray-100 outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-700"
                        spellCheck={false}
                      />
                      {queryError && (
                        <div className="rounded-md bg-error-50 p-3 text-xs text-error-700 dark:bg-error-900/30 dark:text-error-300">
                          {queryError}
                        </div>
                      )}
                      {isSqlDirty && (
                        <div className="rounded-md border border-warning-200 bg-warning-50 p-3 text-xs text-warning-700 dark:border-warning-900/40 dark:bg-warning-900/20 dark:text-warning-300">
                          {t('response.sqlDirtyHint', 'You have unexecuted SQL edits. Confirm run to refresh Answer, Data, and Chart.')}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        <Button size="sm" onClick={handleRunSql} loading={runningSql} disabled={!executableSql || !currentProject?.id}>
                          {t('response.runSql', 'Run SQL')}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditableSql(lastExecutedSql)
                            setUserEditedSql(false)
                            setQueryError(null)
                          }}
                          disabled={!isSqlDirty}
                        >
                          {t('response.resetSql', 'Reset')}
                        </Button>
                      </div>
                    </div>
                  ) : activeTab === 'chart' && columns.length > 0 ? (
                    <div className="p-4">
                      {normalizedChartSpec ? (
                        <div className="relative h-[min(68vh,620px)] min-h-[360px] overflow-hidden rounded-xl border border-gray-200 bg-gradient-to-br from-white to-gray-50 p-3 shadow-sm dark:border-gray-700 dark:from-gray-950 dark:to-gray-900">
                          <div className="absolute right-3 top-3 z-20 flex items-center gap-1 rounded-full border border-white/70 bg-white/75 p-1 shadow-lg backdrop-blur-md dark:border-gray-700/70 dark:bg-gray-900/75">
                            <button
                              type="button"
                              onClick={() => setIsChartEditMode((value) => !value)}
                              disabled={!chartSpec}
                              className="rounded-full px-2.5 py-1 text-xs font-medium text-gray-700 transition hover:bg-primary hover:text-white disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-200"
                            >
                              {isChartEditMode ? t('response.doneEditingChart', 'Done') : t('response.editChart', 'Edit')}
                            </button>
                            <button
                              type="button"
                              onClick={handleResetChart}
                              disabled={!chartSpecOverride}
                              className="rounded-full px-2.5 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-900 hover:text-white disabled:cursor-not-allowed disabled:opacity-40 dark:text-gray-200 dark:hover:bg-gray-100 dark:hover:text-gray-900"
                            >
                              {t('response.resetChart', 'Reset')}
                            </button>
                            <button
                              type="button"
                              onClick={() => setPinDialogOpen(true)}
                              disabled={!normalizedChartSpec}
                              className="rounded-full bg-primary px-2.5 py-1 text-xs font-medium text-white shadow-sm transition hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {t('response.pinToDashboardShort', 'Pin')}
                            </button>
                          </div>

                          {pinDialogOpen && (
                            <div className="absolute inset-0 z-30 flex items-center justify-center bg-gray-950/20 p-4 backdrop-blur-sm">
                              <div className="w-full max-w-sm rounded-xl border border-white/80 bg-white p-4 shadow-2xl dark:border-gray-700 dark:bg-gray-950">
                                <div className="mb-3">
                                  <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{t('response.pinToDashboard', 'Pin to dashboard')}</h3>
                                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{t('response.pinToDashboardHint', 'Choose a dashboard to save this chart.')}</p>
                                </div>
                                <select
                                  value={selectedDashboardId}
                                  onChange={(event) => setSelectedDashboardId(event.target.value ? Number(event.target.value) : '')}
                                  disabled={dashboardsLoading}
                                  className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
                                >
                                  <option value="">{t('response.selectDashboard', 'Select dashboard')}</option>
                                  {dashboards.map((dashboard: any) => (
                                    <option key={dashboard.id} value={dashboard.id}>{dashboard.name || `Dashboard #${dashboard.id}`}</option>
                                  ))}
                                </select>
                                {dashboardsLoading && (
                                  <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                                    {t('response.loadingDashboards', 'Loading dashboards...')}
                                  </p>
                                )}
                                {!dashboardsLoading && dashboards.length === 0 && (
                                  <p className="mt-2 text-xs text-warning-600 dark:text-warning-400">{t('response.noDashboard', 'No dashboard is available for the current project.')}</p>
                                )}
                                <div className="mt-4 flex justify-end gap-2">
                                  <Button size="sm" variant="ghost" onClick={() => setPinDialogOpen(false)}>
                                    {t('common.cancel', 'Cancel')}
                                  </Button>
                                  <Button
                                    size="sm"
                                    onClick={() => { if (selectedDashboardId !== '') pinChart.mutate(Number(selectedDashboardId)) }}
                                    loading={pinChart.isPending}
                                    disabled={!selectedDashboardId || !normalizedChartSpec || dashboardsLoading}
                                  >
                                    {t('response.pinConfirm', 'Pin')}
                                  </Button>
                                </div>
                              </div>
                            </div>
                          )}

                          {isChartEditMode && (
                            <div className="absolute right-3 top-14 z-10 max-h-[calc(100%-4.5rem)] w-[min(420px,calc(100%-1.5rem))] overflow-y-auto rounded-xl border border-white/70 bg-white/90 p-3 shadow-2xl backdrop-blur-md dark:border-gray-700/70 dark:bg-gray-950/90">
                              <ChartEditor fields={chartFields} spec={normalizedChartSpec} data={chartRows} onChange={setChartSpecOverride} showPreview={false} />
                            </div>
                          )}

                          <div className="h-full w-full pt-8">
                            <ChartContainer spec={normalizedChartSpec} data={chartRows} />
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-md border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500 dark:border-gray-600 dark:text-gray-400">
                          {t('response.noChart', 'No suitable chart could be inferred from these columns.')}
                        </div>
                      )}
                    </div>
                  ) : activeTab === 'data' && columns.length > 0 ? (
                    <div className="max-w-full">
                      {visibleWarning && (
                        <div className="border-b border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700 dark:border-warning-900/40 dark:bg-warning-900/20 dark:text-warning-300">
                          {visibleWarning}
                        </div>
                      )}
                      <div className="max-h-[52vh] max-w-full overflow-auto">
                        <table className="min-w-max divide-y divide-gray-200 dark:divide-gray-700">
                          <thead className="sticky top-0 z-10 bg-gray-50 dark:bg-gray-800">
                            <tr>
                              {columns.map((col) => (
                                <th key={col} className="bg-gray-50 px-3 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                                  {col}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
                            {rows.slice(0, previewRowLimit).map((row, i) => (
                              <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                                {columns.map((col) => (
                                  <td key={col} className="whitespace-nowrap px-3 py-2 text-sm text-gray-700 dark:text-gray-300">
                                    {String(row[col] ?? '-')}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-2 bg-gray-50 px-3 py-2 text-xs text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                        <span>
                          {t('response.showingRows', `Showing ${Math.min(rows.length, previewRowLimit)} of ${totalRows} rows`)}
                          {executionTimeMs ? ` · ${executionTimeMs} ms` : ''}
                        </span>
                        {rows.length > previewRowLimit && <span>{t('response.tableTruncated', `Preview limited to ${previewRowLimit} rows`)}</span>}
                      </div>
                    </div>
                  ) : (
                    <div className="p-3 text-sm text-gray-500 dark:text-gray-400">
                      {t('response.noResultRows', 'No result rows are available yet. Switch to SQL view to adjust and run the query.')}
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-2 border-t border-gray-200 px-3 py-2 dark:border-gray-700">
                    {activeTab !== 'answer' && (lastExecutedSql || editableSql) && (
                      <Button variant="ghost" size="sm" onClick={handleAdjustSql}>
                        {t('response.adjustSQL', 'Adjust SQL')}
                      </Button>
                    )}
                    {activeTab === 'answer' && onReRun && (
                      <Button variant="ghost" size="sm" onClick={() => onReRun(response.id)}>
                        {t('response.reRun', 'Re-run')}
                      </Button>
                    )}
                    {onSaveAsView && columns.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onSaveAsView(response.id, {
                          sql: lastExecutedSql || undefined,
                          answerContent: summary,
                          columns,
                        })}
                      >
                        {t('response.saveAsView', 'Save as View')}
                      </Button>
                    )}
                    {onSaveAsPair && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onSaveAsPair(response.id, {
                          sql: lastExecutedSql || undefined,
                          answerContent: summary,
                        })}
                      >
                        {t('response.saveAsPair', 'Save as Pair')}
                      </Button>
                    )}
                  </div>
                </div>
              )}

              {!hasResultView && <div className="flex flex-wrap items-center gap-2">
                {onReRun && (
                  <Button variant="ghost" size="sm" onClick={() => onReRun(response.id)}>
                    {t('response.reRun', 'Re-run')}
                  </Button>
                )}
                {onSaveAsPair && (
                  <Button variant="ghost" size="sm" onClick={() => onSaveAsPair(response.id)}>
                    {t('response.saveAsPair', 'Save as Pair')}
                  </Button>
                )}
              </div>}
            </>
          )}
        </div>
      </div>
      <ConfirmDialog
        open={runSqlConfirmOpen}
        onClose={() => setRunSqlConfirmOpen(false)}
        onConfirm={handleConfirmRunSql}
        title={t('response.runSqlConfirmTitle', 'Run edited SQL?')}
        message={t('response.runSqlConfirmMessage', 'This will execute the edited SQL and refresh the Answer, Data, and Chart tabs with the latest result.')}
        confirmLabel={t('response.runSqlConfirmAction', 'Run and refresh')}
        cancelLabel={t('common.cancel', 'Cancel')}
        variant="primary"
        loading={runningSql}
      />
    </div>
  )
}
