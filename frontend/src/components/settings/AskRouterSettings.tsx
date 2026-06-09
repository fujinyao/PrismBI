'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'

interface AskRouterSettingsProps {
  settings: any
  onSaveAsk: (s: Record<string, unknown>) => void
  onSaveRouter: (s: Record<string, unknown>) => void
  savingAsk?: boolean
  savingRouter?: boolean
  canSave?: boolean
}

const parseBool = (value: unknown, fallback: boolean): boolean => {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') {
    const v = value.trim().toLowerCase()
    if (v === 'true' || v === '1') return true
    if (v === 'false' || v === '0') return false
  }
  return fallback
}

const parseNum = (value: unknown, fallback: number): number => {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

export function AskRouterSettings({
  settings,
  onSaveAsk,
  onSaveRouter,
  savingAsk,
  savingRouter,
  canSave = true,
}: AskRouterSettingsProps) {
  const t = useI18nStore((s) => s.t)

  const [askMaxSqlRows, setAskMaxSqlRows] = useState(200)
  const [askDefaultPreviewRowLimit, setAskDefaultPreviewRowLimit] = useState(20)
  const [askMinPreviewRowLimit, setAskMinPreviewRowLimit] = useState(5)
  const [askMaxPreviewRowLimit, setAskMaxPreviewRowLimit] = useState(100)
  const [askMaxSourceMaterializationRows, setAskMaxSourceMaterializationRows] = useState(5000)

  const [askAnalysisCacheMax, setAskAnalysisCacheMax] = useState(128)
  const [askAnalysisCacheTtlS, setAskAnalysisCacheTtlS] = useState(300)

  const [tier1MaxRetries, setTier1MaxRetries] = useState(1)
  const [tier2MaxRetries, setTier2MaxRetries] = useState(2)
  const [tier3MaxRetries, setTier3MaxRetries] = useState(3)
  const [adaptiveStrategyEnabled, setAdaptiveStrategyEnabled] = useState(true)
  const [adaptiveStrategyConsensusRiskThreshold, setAdaptiveStrategyConsensusRiskThreshold] = useState(4)
  const [adaptiveStrategyDecomposeRiskThreshold, setAdaptiveStrategyDecomposeRiskThreshold] = useState(7)
  const [adaptiveStrategyMinSubquestionsForDecompose, setAdaptiveStrategyMinSubquestionsForDecompose] = useState(2)
  const [tier1MaxColumnsPerModel, setTier1MaxColumnsPerModel] = useState(12)
  const [tier2MaxColumnsPerModel, setTier2MaxColumnsPerModel] = useState(15)
  const [tier3MaxColumnsPerModel, setTier3MaxColumnsPerModel] = useState(20)

  const [schemaPruningEnabled, setSchemaPruningEnabled] = useState(true)
  const [guidanceLlmAvailable, setGuidanceLlmAvailable] = useState(true)
  const [modelRefCaseSensitive, setModelRefCaseSensitive] = useState(true)
  const [metadataSummaryMaxModels, setMetadataSummaryMaxModels] = useState(10)

  const [sqlRouteV2Enabled, setSqlRouteV2Enabled] = useState(true)
  const [sqlRouteShadowMode, setSqlRouteShadowMode] = useState(false)
  const [sqlRouteEventPersistEnabled, setSqlRouteEventPersistEnabled] = useState(true)
  const [sqlRouteStrictJsonProbeEnabled, setSqlRouteStrictJsonProbeEnabled] = useState(true)
  const [sqlRouteProfileId, setSqlRouteProfileId] = useState('prismbi.default')
  const [sqlRouteProfileVersion, setSqlRouteProfileVersion] = useState('v2')

  const [maxSubQuestions, setMaxSubQuestions] = useState(5)
  const [maxSuggestedQuestions, setMaxSuggestedQuestions] = useState(5)
  const [decomposeMergeEnabled, setDecomposeMergeEnabled] = useState(true)
  const [decomposeMergeCircuitEnabled, setDecomposeMergeCircuitEnabled] = useState(true)
  const [decomposeMergeFailureThreshold, setDecomposeMergeFailureThreshold] = useState(1)
  const [decomposeMergeDisableSeconds, setDecomposeMergeDisableSeconds] = useState(3600)

  const [crossSourceMaxWorkers, setCrossSourceMaxWorkers] = useState(4)

  const [externalConnectionPoolEnabled, setExternalConnectionPoolEnabled] = useState(true)
  const [externalConnectionPoolMaxPerKey, setExternalConnectionPoolMaxPerKey] = useState(4)
  const [externalConnectionPoolIdleSeconds, setExternalConnectionPoolIdleSeconds] = useState(300)

  const [executionMetricsLogEvery, setExecutionMetricsLogEvery] = useState(25)
  const [executionMetricsLogIntervalSeconds, setExecutionMetricsLogIntervalSeconds] = useState(180)
  const [executionMetricsMaxSamples, setExecutionMetricsMaxSamples] = useState(400)

  const [routeObservabilityWindowSeconds, setRouteObservabilityWindowSeconds] = useState(1800)
  const [routeObservabilityMaxEventsPerProject, setRouteObservabilityMaxEventsPerProject] = useState(20000)
  const [routeObservabilityPersistEnabled, setRouteObservabilityPersistEnabled] = useState(true)
  const [routeObservabilityPersistIntervalSeconds, setRouteObservabilityPersistIntervalSeconds] = useState(30)
  const [routeObservabilityPersistEventDelta, setRouteObservabilityPersistEventDelta] = useState(20)
  const [routeObservabilityStrategyTrendMaxPoints, setRouteObservabilityStrategyTrendMaxPoints] = useState(24)
  const [routeObservabilityStrategyTrendPersistIntervalSeconds, setRouteObservabilityStrategyTrendPersistIntervalSeconds] = useState(60)
  const [routeObservabilityStrategyTrendPersistDecisionDelta, setRouteObservabilityStrategyTrendPersistDecisionDelta] = useState(5)

  const [settingsApplied, setSettingsApplied] = useState(false)

  useEffect(() => {
    if (settingsApplied || !settings) return

    setAskMaxSqlRows(parseNum(settings.ask_max_sql_rows, 200))
    setAskDefaultPreviewRowLimit(parseNum(settings.ask_default_preview_row_limit, 20))
    setAskMinPreviewRowLimit(parseNum(settings.ask_min_preview_row_limit, 5))
    setAskMaxPreviewRowLimit(parseNum(settings.ask_max_preview_row_limit, 100))
    setAskMaxSourceMaterializationRows(parseNum(settings.ask_max_source_materialization_rows, 5000))
    setAskAnalysisCacheMax(parseNum(settings.ask_analysis_cache_max, 128))
    setAskAnalysisCacheTtlS(parseNum(settings.ask_analysis_cache_ttl_s, 300))

    setTier1MaxRetries(parseNum(settings.router_tier1_max_retries, 1))
    setTier2MaxRetries(parseNum(settings.router_tier2_max_retries, 2))
    setTier3MaxRetries(parseNum(settings.router_tier3_max_retries, 3))
    setAdaptiveStrategyEnabled(parseBool(settings.router_adaptive_strategy_enabled, true))
    setAdaptiveStrategyConsensusRiskThreshold(parseNum(settings.router_adaptive_strategy_consensus_risk_threshold, 4))
    setAdaptiveStrategyDecomposeRiskThreshold(parseNum(settings.router_adaptive_strategy_decompose_risk_threshold, 7))
    setAdaptiveStrategyMinSubquestionsForDecompose(parseNum(settings.router_adaptive_strategy_min_subquestions_for_decompose, 2))
    setTier1MaxColumnsPerModel(parseNum(settings.router_tier1_max_columns_per_model, 12))
    setTier2MaxColumnsPerModel(parseNum(settings.router_tier2_max_columns_per_model, 15))
    setTier3MaxColumnsPerModel(parseNum(settings.router_tier3_max_columns_per_model, 20))

    setSchemaPruningEnabled(parseBool(settings.router_schema_pruning_enabled, true))
    setGuidanceLlmAvailable(parseBool(settings.router_guidance_llm_available, true))
    setModelRefCaseSensitive(parseBool(settings.router_model_ref_case_sensitive, true))
    setMetadataSummaryMaxModels(parseNum(settings.router_metadata_summary_max_models, 10))

    setSqlRouteV2Enabled(parseBool(settings.router_sql_route_v2_enabled, true))
    setSqlRouteShadowMode(parseBool(settings.router_sql_route_shadow_mode, false))
    setSqlRouteEventPersistEnabled(parseBool(settings.router_sql_route_event_persist_enabled, true))
    setSqlRouteStrictJsonProbeEnabled(parseBool(settings.router_sql_route_strict_json_probe_enabled, true))
    setSqlRouteProfileId(String(settings.router_sql_route_profile_id || 'prismbi.default'))
    setSqlRouteProfileVersion(String(settings.router_sql_route_profile_version || 'v2'))

    setMaxSubQuestions(parseNum(settings.router_max_sub_questions, 5))
    setMaxSuggestedQuestions(parseNum(settings.router_max_suggested_questions, 5))
    setDecomposeMergeEnabled(parseBool(settings.router_decompose_merge_enabled, true))
    setDecomposeMergeCircuitEnabled(parseBool(settings.router_decompose_merge_circuit_enabled, true))
    setDecomposeMergeFailureThreshold(parseNum(settings.router_decompose_merge_failure_threshold, 1))
    setDecomposeMergeDisableSeconds(parseNum(settings.router_decompose_merge_disable_seconds, 3600))

    setCrossSourceMaxWorkers(parseNum(settings.router_cross_source_max_workers, 4))

    setExternalConnectionPoolEnabled(parseBool(settings.router_external_connection_pool_enabled, true))
    setExternalConnectionPoolMaxPerKey(parseNum(settings.router_external_connection_pool_max_per_key, 4))
    setExternalConnectionPoolIdleSeconds(parseNum(settings.router_external_connection_pool_idle_seconds, 300))

    setExecutionMetricsLogEvery(parseNum(settings.router_execution_metrics_log_every, 25))
    setExecutionMetricsLogIntervalSeconds(parseNum(settings.router_execution_metrics_log_interval_seconds, 180))
    setExecutionMetricsMaxSamples(parseNum(settings.router_execution_metrics_max_samples, 400))

    setRouteObservabilityWindowSeconds(parseNum(settings.router_route_observability_window_seconds, 1800))
    setRouteObservabilityMaxEventsPerProject(parseNum(settings.router_route_observability_max_events_per_project, 20000))
    setRouteObservabilityPersistEnabled(parseBool(settings.router_route_observability_persist_enabled, true))
    setRouteObservabilityPersistIntervalSeconds(parseNum(settings.router_route_observability_persist_interval_seconds, 30))
    setRouteObservabilityPersistEventDelta(parseNum(settings.router_route_observability_persist_event_delta, 20))
    setRouteObservabilityStrategyTrendMaxPoints(parseNum(settings.router_route_observability_strategy_trend_max_points, 24))
    setRouteObservabilityStrategyTrendPersistIntervalSeconds(parseNum(settings.router_route_observability_strategy_trend_persist_interval_seconds, 60))
    setRouteObservabilityStrategyTrendPersistDecisionDelta(parseNum(settings.router_route_observability_strategy_trend_persist_decision_delta, 5))

    setSettingsApplied(true)
  }, [settings, settingsApplied])

  const askPayload = () => ({
    max_sql_rows: askMaxSqlRows,
    default_preview_row_limit: askDefaultPreviewRowLimit,
    min_preview_row_limit: askMinPreviewRowLimit,
    max_preview_row_limit: askMaxPreviewRowLimit,
    max_source_materialization_rows: askMaxSourceMaterializationRows,
    analysis_cache_max: askAnalysisCacheMax,
    analysis_cache_ttl_s: askAnalysisCacheTtlS,
  })

  const routerPayload = () => ({
    adaptive_strategy_enabled: adaptiveStrategyEnabled,
    adaptive_strategy_consensus_risk_threshold: Math.max(1, Math.round(adaptiveStrategyConsensusRiskThreshold)),
    adaptive_strategy_decompose_risk_threshold: Math.max(
      Math.max(1, Math.round(adaptiveStrategyConsensusRiskThreshold)),
      Math.round(adaptiveStrategyDecomposeRiskThreshold),
    ),
    adaptive_strategy_min_subquestions_for_decompose: Math.max(1, Math.round(adaptiveStrategyMinSubquestionsForDecompose)),
    tier1_max_retries: tier1MaxRetries,
    tier2_max_retries: tier2MaxRetries,
    tier3_max_retries: tier3MaxRetries,
    tier1_max_columns_per_model: tier1MaxColumnsPerModel,
    tier2_max_columns_per_model: tier2MaxColumnsPerModel,
    tier3_max_columns_per_model: tier3MaxColumnsPerModel,
    schema_pruning_enabled: schemaPruningEnabled,
    guidance_llm_available: guidanceLlmAvailable,
    model_ref_case_sensitive: modelRefCaseSensitive,
    metadata_summary_max_models: metadataSummaryMaxModels,
    sql_route_v2_enabled: sqlRouteV2Enabled,
    sql_route_shadow_mode: sqlRouteShadowMode,
    sql_route_event_persist_enabled: sqlRouteEventPersistEnabled,
    sql_route_strict_json_probe_enabled: sqlRouteStrictJsonProbeEnabled,
    sql_route_profile_id: sqlRouteProfileId,
    sql_route_profile_version: sqlRouteProfileVersion,
    max_sub_questions: maxSubQuestions,
    max_suggested_questions: maxSuggestedQuestions,
    decompose_merge_enabled: decomposeMergeEnabled,
    decompose_merge_circuit_enabled: decomposeMergeCircuitEnabled,
    decompose_merge_failure_threshold: decomposeMergeFailureThreshold,
    decompose_merge_disable_seconds: decomposeMergeDisableSeconds,
    cross_source_max_workers: crossSourceMaxWorkers,
    external_connection_pool_enabled: externalConnectionPoolEnabled,
    external_connection_pool_max_per_key: externalConnectionPoolMaxPerKey,
    external_connection_pool_idle_seconds: externalConnectionPoolIdleSeconds,
    execution_metrics_log_every: executionMetricsLogEvery,
    execution_metrics_log_interval_seconds: executionMetricsLogIntervalSeconds,
    execution_metrics_max_samples: executionMetricsMaxSamples,
    route_observability_window_seconds: routeObservabilityWindowSeconds,
    route_observability_max_events_per_project: routeObservabilityMaxEventsPerProject,
    route_observability_persist_enabled: routeObservabilityPersistEnabled,
    route_observability_persist_interval_seconds: routeObservabilityPersistIntervalSeconds,
    route_observability_persist_event_delta: routeObservabilityPersistEventDelta,
    route_observability_strategy_trend_max_points: routeObservabilityStrategyTrendMaxPoints,
    route_observability_strategy_trend_persist_interval_seconds: routeObservabilityStrategyTrendPersistIntervalSeconds,
    route_observability_strategy_trend_persist_decision_delta: routeObservabilityStrategyTrendPersistDecisionDelta,
  })

  const handleSubmitAsk = (e: React.FormEvent) => {
    e.preventDefault()
    onSaveAsk(askPayload())
  }

  const handleSubmitRouter = (e: React.FormEvent) => {
    e.preventDefault()
    onSaveRouter(routerPayload())
  }

  const handleSaveAll = () => {
    onSaveAsk(askPayload())
    onSaveRouter(routerPayload())
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="space-y-5">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            SQL
          </h3>

          <div>
            <h4 className="mb-3 text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
              执行限制
            </h4>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="SQL 返回行数上限" type="number" value={askMaxSqlRows} onChange={(e) => setAskMaxSqlRows(parseInt(e.target.value) || 1)} min={1} max={100000} />
              <Input label="默认预览行数" type="number" value={askDefaultPreviewRowLimit} onChange={(e) => setAskDefaultPreviewRowLimit(parseInt(e.target.value) || 1)} min={1} max={100000} />
              <Input label="最小预览行数" type="number" value={askMinPreviewRowLimit} onChange={(e) => setAskMinPreviewRowLimit(parseInt(e.target.value) || 1)} min={1} max={100000} />
              <Input label="最大预览行数" type="number" value={askMaxPreviewRowLimit} onChange={(e) => setAskMaxPreviewRowLimit(parseInt(e.target.value) || 1)} min={1} max={100000} />
              <Input label="跨源物化行数上限" type="number" value={askMaxSourceMaterializationRows} onChange={(e) => setAskMaxSourceMaterializationRows(parseInt(e.target.value) || 100)} min={100} max={200000} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                Schema 剪枝
              </h4>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={schemaPruningEnabled} onChange={(e) => setSchemaPruningEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                启用
              </label>
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="Tier 1 每模型保留列数" type="number" value={tier1MaxColumnsPerModel} onChange={(e) => setTier1MaxColumnsPerModel(parseInt(e.target.value) || 1)} min={1} max={500} />
              <Input label="Tier 2 每模型保留列数" type="number" value={tier2MaxColumnsPerModel} onChange={(e) => setTier2MaxColumnsPerModel(parseInt(e.target.value) || 1)} min={1} max={500} />
              <Input label="Tier 3 每模型保留列数" type="number" value={tier3MaxColumnsPerModel} onChange={(e) => setTier3MaxColumnsPerModel(parseInt(e.target.value) || 1)} min={1} max={500} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <h4 className="mb-3 text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
              重试
            </h4>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="Tier 1（简单问题）" type="number" value={tier1MaxRetries} onChange={(e) => setTier1MaxRetries(parseInt(e.target.value) || 1)} min={1} max={10} />
              <Input label="Tier 2（多维问题）" type="number" value={tier2MaxRetries} onChange={(e) => setTier2MaxRetries(parseInt(e.target.value) || 1)} min={1} max={10} />
              <Input label="Tier 3（复合问题）" type="number" value={tier3MaxRetries} onChange={(e) => setTier3MaxRetries(parseInt(e.target.value) || 1)} min={1} max={10} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                策略池路由
              </h4>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={adaptiveStrategyEnabled} onChange={(e) => setAdaptiveStrategyEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                启用自适应策略池
              </label>
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="一致性策略风险阈值" type="number" value={adaptiveStrategyConsensusRiskThreshold} onChange={(e) => setAdaptiveStrategyConsensusRiskThreshold(parseInt(e.target.value) || 1)} min={1} max={20} disabled={!adaptiveStrategyEnabled} />
              <Input label="分解策略风险阈值" type="number" value={adaptiveStrategyDecomposeRiskThreshold} onChange={(e) => setAdaptiveStrategyDecomposeRiskThreshold(parseInt(e.target.value) || 1)} min={1} max={20} disabled={!adaptiveStrategyEnabled} />
              <Input label="分解最小子问题数" type="number" value={adaptiveStrategyMinSubquestionsForDecompose} onChange={(e) => setAdaptiveStrategyMinSubquestionsForDecompose(parseInt(e.target.value) || 1)} min={1} max={10} disabled={!adaptiveStrategyEnabled} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <h4 className="mb-3 text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
              路由
            </h4>
            <div className="mb-3 grid grid-cols-1 gap-x-6 gap-y-2 md:grid-cols-3">
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={sqlRouteV2Enabled} onChange={(e) => setSqlRouteV2Enabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                启用 v2 路由
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={sqlRouteShadowMode} onChange={(e) => setSqlRouteShadowMode(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                影子模式（仅对比）
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={sqlRouteEventPersistEnabled} onChange={(e) => setSqlRouteEventPersistEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                持久化路由事件
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={sqlRouteStrictJsonProbeEnabled} onChange={(e) => setSqlRouteStrictJsonProbeEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                LLM 严格 JSON 探测
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={guidanceLlmAvailable} onChange={(e) => setGuidanceLlmAvailable(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                启用引导 LLM
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={modelRefCaseSensitive} onChange={(e) => setModelRefCaseSensitive(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                SQL 模型引用区分大小写
              </label>
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="路由 Profile ID" value={sqlRouteProfileId} onChange={(e) => setSqlRouteProfileId(e.target.value)} />
              <Input label="路由 Profile 版本" value={sqlRouteProfileVersion} onChange={(e) => setSqlRouteProfileVersion(e.target.value)} />
              <Input label="元数据摘要最大模型数" type="number" value={metadataSummaryMaxModels} onChange={(e) => setMetadataSummaryMaxModels(parseInt(e.target.value) || 1)} min={1} max={200} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                性能与连接
              </h4>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={externalConnectionPoolEnabled} onChange={(e) => setExternalConnectionPoolEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                启用外部数据库连接池
              </label>
            </div>
            <div className="mb-3 grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="跨源查询最大并行 Worker 数" type="number" value={crossSourceMaxWorkers} onChange={(e) => setCrossSourceMaxWorkers(parseInt(e.target.value) || 1)} min={1} max={32} />
              <Input label="每 Key 最大连接数" type="number" value={externalConnectionPoolMaxPerKey} onChange={(e) => setExternalConnectionPoolMaxPerKey(parseInt(e.target.value) || 1)} min={1} max={64} disabled={!externalConnectionPoolEnabled} />
              <Input label="空闲连接超时（秒）" type="number" value={externalConnectionPoolIdleSeconds} onChange={(e) => setExternalConnectionPoolIdleSeconds(parseFloat(e.target.value) || 30)} min={30} max={86400} disabled={!externalConnectionPoolEnabled} />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="每 N 次执行记录日志" type="number" value={executionMetricsLogEvery} onChange={(e) => setExecutionMetricsLogEvery(parseInt(e.target.value) || 1)} min={1} max={2000} />
              <Input label="日志记录间隔（秒）" type="number" value={executionMetricsLogIntervalSeconds} onChange={(e) => setExecutionMetricsLogIntervalSeconds(parseFloat(e.target.value) || 10)} min={10} max={86400} />
              <Input label="最大采样数" type="number" value={executionMetricsMaxSamples} onChange={(e) => setExecutionMetricsMaxSamples(parseInt(e.target.value) || 50)} min={50} max={10000} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                可观测性
              </h4>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={routeObservabilityPersistEnabled} onChange={(e) => setRouteObservabilityPersistEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                持久化快照
              </label>
            </div>
            <div className="mb-3 grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="路由统计窗口（秒）" type="number" value={routeObservabilityWindowSeconds} onChange={(e) => setRouteObservabilityWindowSeconds(parseInt(e.target.value) || 300)} min={300} max={86400} />
              <Input label="每项目最大事件数" type="number" value={routeObservabilityMaxEventsPerProject} onChange={(e) => setRouteObservabilityMaxEventsPerProject(parseInt(e.target.value) || 1000)} min={1000} max={200000} />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="快照持久化间隔（秒）" type="number" value={routeObservabilityPersistIntervalSeconds} onChange={(e) => setRouteObservabilityPersistIntervalSeconds(parseFloat(e.target.value) || 1)} min={1} max={3600} disabled={!routeObservabilityPersistEnabled} />
              <Input label="快照事件增量阈值" type="number" value={routeObservabilityPersistEventDelta} onChange={(e) => setRouteObservabilityPersistEventDelta(parseInt(e.target.value) || 1)} min={1} max={10000} disabled={!routeObservabilityPersistEnabled} />
              <Input label="策略趋势保留点数" type="number" value={routeObservabilityStrategyTrendMaxPoints} onChange={(e) => setRouteObservabilityStrategyTrendMaxPoints(parseInt(e.target.value) || 6)} min={6} max={240} />
              <Input label="策略趋势持久化间隔（秒）" type="number" value={routeObservabilityStrategyTrendPersistIntervalSeconds} onChange={(e) => setRouteObservabilityStrategyTrendPersistIntervalSeconds(parseFloat(e.target.value) || 1)} min={1} max={3600} disabled={!routeObservabilityPersistEnabled} />
              <Input label="策略趋势决策增量阈值" type="number" value={routeObservabilityStrategyTrendPersistDecisionDelta} onChange={(e) => setRouteObservabilityStrategyTrendPersistDecisionDelta(parseInt(e.target.value) || 1)} min={1} max={10000} disabled={!routeObservabilityPersistEnabled} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-4">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            分析缓存
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            问题分析结果的缓存。AI 对用户提问进行语义理解时，相同或相似的问题可以复用分析结果，减少 LLM 调用。
          </p>
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="缓存最大条目数" type="number" value={askAnalysisCacheMax} onChange={(e) => setAskAnalysisCacheMax(parseInt(e.target.value) || 16)} min={16} max={10000} />
            <Input label="缓存 TTL（秒）" type="number" value={askAnalysisCacheTtlS} onChange={(e) => setAskAnalysisCacheTtlS(parseFloat(e.target.value) || 10)} min={10} max={86400} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              问题处理
            </h3>
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={decomposeMergeEnabled} onChange={(e) => setDecomposeMergeEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
                启用分解-合并策略
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" checked={decomposeMergeCircuitEnabled} onChange={(e) => setDecomposeMergeCircuitEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" disabled={!decomposeMergeEnabled} />
                启用复合问题分解熔断器
              </label>
            </div>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            针对用户提问方式的处理策略，包括复合问题的分解-合并以及推荐问题的数量控制。
          </p>
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="熔断阈值（失败次数）" type="number" value={decomposeMergeFailureThreshold} onChange={(e) => setDecomposeMergeFailureThreshold(parseInt(e.target.value) || 1)} min={1} max={20} disabled={!decomposeMergeEnabled || !decomposeMergeCircuitEnabled} />
            <Input label="熔断恢复时间（秒）" type="number" value={decomposeMergeDisableSeconds} onChange={(e) => setDecomposeMergeDisableSeconds(parseFloat(e.target.value) || 30)} min={30} max={86400} disabled={!decomposeMergeEnabled || !decomposeMergeCircuitEnabled} />
            <Input label="最大子问题数" type="number" value={maxSubQuestions} onChange={(e) => setMaxSubQuestions(parseInt(e.target.value) || 1)} min={1} max={20} />
            <Input label="最大推荐问题数" type="number" value={maxSuggestedQuestions} onChange={(e) => setMaxSuggestedQuestions(parseInt(e.target.value) || 1)} min={1} max={20} />
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-end gap-3">
        <Button type="button" variant="secondary" onClick={handleSubmitAsk} loading={savingAsk} disabled={!canSave}>
          保存问答限制
        </Button>
        <Button type="button" variant="secondary" onClick={handleSubmitRouter} loading={savingRouter} disabled={!canSave}>
          保存路由设置
        </Button>
        <Button type="button" onClick={handleSaveAll} loading={savingAsk || savingRouter} disabled={!canSave}>
          保存全部
        </Button>
      </div>
    </div>
  )
}
