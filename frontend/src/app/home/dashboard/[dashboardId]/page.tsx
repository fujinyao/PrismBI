'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { dashboardApi } from '@/lib/api'
import { useToast } from '@/components/ui/Toast'
import { useI18nStore } from '@/stores/i18nStore'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Input } from '@/components/ui/Input'
import { SkeletonCard } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { Tag } from '@/components/ui/Tag'
import { DashboardSidebar } from '@/components/dashboard/DashboardShell'
import { useProjectStore } from '@/stores/projectStore'
import { ChartContainer } from '@/components/chart/ChartContainer'
import { cn } from '@/lib/utils'

interface DashboardItemData {
  id: number
  dashboard_id: number
  title?: string
  display_name?: string
  chart_config?: Record<string, unknown>
  data_source?: string
  type?: string
  layout_x?: number | null
  layout_y?: number | null
  layout_w?: number | null
  layout_h?: number | null
  created_at?: string
}

interface DashboardDetail {
  id: number
  name: string
  display_name?: string
  project_id: number
  items?: DashboardItemData[]
  created_at?: string
}

function parseChartConfig(value: unknown): Record<string, unknown> | null {
  if (!value) return null
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
    } catch {
      return null
    }
  }
  return typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function chartTypeLabel(item: DashboardItemData, chartConfig: Record<string, unknown> | null) {
  const spec = chartConfig?.spec as any
  const configuredType = typeof chartConfig?.chart_type === 'string' ? chartConfig.chart_type : null
  return configuredType ?? spec?.mark?.type ?? spec?.mark ?? item.type ?? 'chart'
}

function normalizedChartKind(item: DashboardItemData, chartConfig: Record<string, unknown> | null) {
  const label = chartTypeLabel(item, chartConfig)
  return String(label).toLowerCase()
}

function chartProfile(item: DashboardItemData, chartConfig: Record<string, unknown> | null) {
  const kind = normalizedChartKind(item, chartConfig)
  const spec = chartConfig?.spec as any
  const mark = String(spec?.mark?.type ?? spec?.mark ?? kind).toLowerCase()
  const rows = Array.isArray(chartConfig?.rows) ? chartConfig.rows.length : 0
  const columns = Array.isArray(chartConfig?.columns) ? chartConfig.columns.length : 0
  const compact = kind.includes('metric') || mark === 'text' || (rows > 0 && rows <= 1 && columns > 0 && columns <= 2)
  const tabular = kind.includes('table') || columns >= 6
  const wide = tabular || ['area', 'line'].includes(mark) || rows > 18 || columns > 5
  const tall = tabular || mark === 'rect' || kind.includes('heatmap') || (mark === 'bar' && rows > 12)
  return { compact, wide, tall }
}

function adaptiveGridClass(count: number) {
  if (count <= 1) return 'grid grid-cols-1 gap-[5px]'
  if (count === 2) return 'grid grid-cols-1 gap-[5px] xl:grid-cols-2'
  if (count === 3) return 'grid grid-cols-1 gap-[5px] lg:grid-cols-6'
  if (count === 4) return 'grid grid-cols-1 gap-[5px] md:grid-cols-2'
  return 'grid grid-cols-1 gap-[5px] md:grid-cols-2 xl:grid-cols-3'
}

function adaptiveItemClass(count: number, index: number, profile: ReturnType<typeof chartProfile>) {
  if (count <= 1) return 'mx-auto w-full max-w-7xl'
  if (count === 3) return profile.wide && index === 0 ? 'lg:col-span-4' : 'lg:col-span-2'
  if (count === 5 && profile.wide && index === 0) return 'xl:col-span-2'
  if (count >= 6 && profile.wide && index % 5 === 0) return 'xl:col-span-2'
  return ''
}

function adaptiveContentClass(count: number, index: number, profile: ReturnType<typeof chartProfile>) {
  if (count <= 1) return profile.compact ? 'h-[360px] lg:h-[420px]' : profile.tall ? 'h-[520px] lg:h-[640px]' : 'h-[460px] lg:h-[560px]'
  if (count === 2) return profile.compact ? 'h-[320px]' : 'h-[420px] xl:h-[520px]'
  if (count === 3 && profile.wide && index === 0) return 'h-[420px] lg:h-[500px]'
  if (count <= 4) return profile.compact ? 'h-[280px]' : profile.tall ? 'h-[420px] lg:h-[460px]' : 'h-[360px] lg:h-[400px]'
  if (profile.compact) return 'h-[260px]'
  if (profile.wide || profile.tall) return 'h-[380px] lg:h-[420px]'
  return 'h-[320px] lg:h-[340px]'
}

function hasSavedLayout(item: DashboardItemData) {
  if (typeof item.layout_w !== 'number' || typeof item.layout_h !== 'number') return false
  if (item.layout_x === null || item.layout_y === null) return false
  return item.layout_w !== 3 || item.layout_h !== 2 || item.layout_x !== 0 || item.layout_y !== 0
}

function savedLayoutItemClass(item: DashboardItemData) {
  const width = Math.max(1, Math.min(12, item.layout_w ?? 3))
  const span = width >= 10 ? 3 : width >= 7 ? 2 : 1
  return span === 3 ? 'xl:col-span-3' : span === 2 ? 'xl:col-span-2' : ''
}

function savedLayoutContentClass(item: DashboardItemData) {
  const height = Math.max(2, item.layout_h ?? 2)
  if (height >= 5) return 'h-[520px] lg:h-[600px]'
  if (height >= 4) return 'h-[420px] lg:h-[500px]'
  if (height >= 3) return 'h-[360px] lg:h-[420px]'
  return 'h-[300px]'
}

const DASHBOARD_SHORT_CACHE_MS = 5000

export default function DashboardPageClient() {
  const params = useParams<{ dashboardId: string }>()
  const router = useRouter()
  const dashboardId = Number(params.dashboardId) || 0
  const { toast } = useToast()
  const t = useI18nStore((s) => s.t)
  const queryClient = useQueryClient()
  const currentProject = useProjectStore((s) => s.currentProject)

  const [addWidgetOpen, setAddWidgetOpen] = useState(false)
  const [widgetTitle, setWidgetTitle] = useState('')
  const [widgetType, setWidgetType] = useState('bar')
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)

  const { data: dashboard, isLoading, isError, refetch } = useQuery({
    queryKey: ['dashboard', dashboardId],
    queryFn: () => dashboardApi.get(dashboardId) as Promise<DashboardDetail>,
    enabled: dashboardId > 0,
    staleTime: DASHBOARD_SHORT_CACHE_MS,
    gcTime: DASHBOARD_SHORT_CACHE_MS * 20,
    refetchOnWindowFocus: false,
  })

  const { data: dashboards } = useQuery({
    queryKey: ['dashboards', currentProject?.id],
    queryFn: () => {
      const id = currentProject?.id
      if (!id) return []
      return dashboardApi.list({ project_id: id }) as Promise<DashboardDetail[]>
    },
    enabled: Boolean(currentProject?.id),
    staleTime: DASHBOARD_SHORT_CACHE_MS,
    gcTime: DASHBOARD_SHORT_CACHE_MS * 20,
    refetchOnWindowFocus: false,
  })

  const addWidgetMutation = useMutation({
    mutationFn: (data: { title: string; chart_config: Record<string, unknown> }) =>
      dashboardApi.items.create({
        dashboard_id: dashboardId,
        title: data.title,
        chart_config: { ...data.chart_config, chart_type: widgetType },
        type: widgetType === 'table' ? 'TABLE' : 'CHART',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId] })
      toast(t('dashboard.widgetAdded', 'Widget added'), 'success')
      setAddWidgetOpen(false)
      setWidgetTitle('')
      setWidgetType('bar')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.widgetAddFailed', 'Failed to add widget'), 'error'),
  })

  const deleteItemMutation = useMutation({
    mutationFn: (id: number) => dashboardApi.items.delete(dashboardId, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard', dashboardId] })
      toast(t('dashboard.widgetRemoved', 'Widget removed'), 'success')
      setDeleteConfirm(null)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.widgetRemoveFailed', 'Failed to remove widget'), 'error'),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => dashboardApi.update(id, { name }),
    onSuccess: (_result, variables) => {
      queryClient.invalidateQueries({ queryKey: ['dashboards', currentProject?.id] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', variables.id] })
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.renameFailed', 'Failed to rename dashboard'), 'error'),
  })

  const deleteDashboardMutation = useMutation({
    mutationFn: (id: number) => dashboardApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboards', currentProject?.id] })
      toast(t('dashboard.deleted', 'Dashboard deleted'), 'success')
      router.push('/home/dashboard')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('dashboard.deleteFailed', 'Failed to delete dashboard'), 'error'),
  })

  const handleAddWidget = () => {
    if (!widgetTitle.trim()) {
      toast(t('dashboard.widgetTitleRequired', 'Please enter a widget title'), 'warning')
      return
    }
    addWidgetMutation.mutate({ title: widgetTitle.trim(), chart_config: {} })
  }

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-[5px] md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    )
  }

  if (isError || !dashboard) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          message={t('dashboard.loadError', 'Failed to load dashboard')}
          description={t('dashboard.loadErrorDesc', 'The dashboard could not be loaded. It may have been deleted.')}
          action={{ label: t('common.goBack', 'Go back'), onClick: () => window.history.back() }}
        />
      </div>
    )
  }

  const items = dashboard.items ?? []
  const useSavedLayout = items.some(hasSavedLayout)

  return (
    <div className="flex min-h-full gap-[5px]">
      <DashboardSidebar
        dashboards={dashboards ?? []}
        selectedDashboardId={dashboardId}
        canCreate={Boolean(currentProject)}
        onCreate={() => router.push('/home/dashboard')}
        onSelect={(id) => router.push(`/home/dashboard/${id}`)}
        onRename={(id, name) => renameMutation.mutate({ id, name })}
        onDelete={(id) => {
          if (confirm(t('dashboard.deleteConfirm', 'Are you sure you want to delete this dashboard? This action cannot be undone.'))) {
            deleteDashboardMutation.mutate(id)
          }
        }}
      />
      <section className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          onClick={() => router.push('/home/dashboard')}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-50 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-300"
          aria-label={t('common.back', 'Back')}
        >
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
        </button>
        <Button onClick={() => setAddWidgetOpen(true)}>{t('dashboard.addWidget', 'Add Widget')}</Button>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title={t('dashboard.emptyDashboard', 'Empty dashboard')}
          description={t('dashboard.emptyDashboardDesc', 'Add widgets to populate your dashboard.')}
          action={{ label: t('dashboard.addWidget', 'Add Widget'), onClick: () => setAddWidgetOpen(true) }}
        />
      ) : (
        <div className={useSavedLayout ? 'grid grid-cols-1 gap-[5px] md:grid-cols-2 xl:grid-cols-3' : adaptiveGridClass(items.length)}>
          {items.map((item, index) => {
            const chartConfig = parseChartConfig(item.chart_config)
            const spec = chartConfig?.spec
            const rows = chartConfig?.rows
            const profile = chartProfile(item, chartConfig)
            const itemClass = useSavedLayout ? savedLayoutItemClass(item) : adaptiveItemClass(items.length, index, profile)
            const contentClass = useSavedLayout ? savedLayoutContentClass(item) : adaptiveContentClass(items.length, index, profile)
            return (
            <Card key={item.id} className={cn('flex flex-col rounded-xl', itemClass)}>
              <CardHeader>
                <div className="flex items-center gap-2 min-w-0">
                  <CardTitle className="truncate">{item.title ?? item.display_name ?? t('dashboard.fallbackWidget', 'Widget')}</CardTitle>
                  <Tag variant="default" size="sm">
                    {chartTypeLabel(item, chartConfig)}
                  </Tag>
                </div>
                <button
                  onClick={() => setDeleteConfirm(item.id)}
                  className="text-gray-400 hover:text-error transition-colors shrink-0"
                  aria-label={t('dashboard.removeWidgetAria', 'Remove widget')}
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </CardHeader>
              <CardContent className={cn('flex-1 p-3', contentClass)}>
                {spec && Array.isArray(rows) ? (
                  <ChartContainer spec={spec} data={rows as any[]} />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-2 text-gray-300 dark:text-gray-600">
                    <svg className="h-16 w-16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 19v-6m4 6V7m4 10v-3M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                    <span className="text-sm">{t('dashboard.noChartData', 'No chart data saved')}</span>
                  </div>
                )}
              </CardContent>
            </Card>
            )
          })}
        </div>
      )}

      <Modal
        open={addWidgetOpen}
        onClose={() => {
          setAddWidgetOpen(false)
          setWidgetTitle('')
          setWidgetType('bar')
        }}
        title={t('dashboard.addWidget', 'Add Widget')}
      >
        <div className="space-y-4">
          <Input
            label={t('dashboard.widgetTitleLabel', 'Widget Title')}
            value={widgetTitle}
            onChange={(e) => setWidgetTitle(e.target.value)}
            placeholder={t('dashboard.widgetTitlePlaceholder', 'e.g. Monthly Sales')}
            autoFocus
          />
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('dashboard.chartType', 'Chart Type')}
            </label>
            <select
              value={widgetType}
              onChange={(e) => setWidgetType(e.target.value)}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            >
              <option value="bar">{t('dashboard.chartType.bar', 'Bar')}</option>
              <option value="line">{t('dashboard.chartType.line', 'Line')}</option>
              <option value="pie">{t('dashboard.chartType.pie', 'Pie')}</option>
              <option value="area">{t('dashboard.chartType.area', 'Area')}</option>
              <option value="table">{t('dashboard.chartType.table', 'Table')}</option>
              <option value="metric">{t('dashboard.chartType.metric', 'Metric')}</option>
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setAddWidgetOpen(false)
                setWidgetTitle('')
                setWidgetType('bar')
              }}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={handleAddWidget} loading={addWidgetMutation.isPending}>
              {t('common.add', 'Add')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={deleteConfirm !== null}
        onClose={() => setDeleteConfirm(null)}
        title={t('dashboard.removeWidget', 'Remove Widget')}
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {t('dashboard.removeWidgetConfirm', 'Are you sure you want to remove this widget from the dashboard?')}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleteConfirm(null)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (deleteConfirm !== null) deleteItemMutation.mutate(deleteConfirm)
              }}
              loading={deleteItemMutation.isPending}
            >
              {t('common.remove', 'Remove')}
            </Button>
          </div>
        </div>
      </Modal>
      </section>
    </div>
  )
}
