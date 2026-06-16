'use client'

import { useEffect, useRef, useState } from 'react'
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

function SectionHeader({ title }: { title: string }) {
  return (
    <h4 className="mb-3 text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
      {title}
    </h4>
  )
}

function ToggleRow({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
      />
      {label}
    </label>
  )
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
  const [tier3MaxRetries, setTier3MaxRetries] = useState(2)
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

  const [maxSubQuestions, setMaxSubQuestions] = useState(3)
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

  const [requestTimeoutMs, setRequestTimeoutMs] = useState(120000)
  const [llmReadTimeoutS, setLlmReadTimeoutS] = useState(120)
  const [llmConnectTimeoutS, setLlmConnectTimeoutS] = useState(30)
  const [llmWriteTimeoutS, setLlmWriteTimeoutS] = useState(30)
  const [llmPoolTimeoutS, setLlmPoolTimeoutS] = useState(30)
  const [dbConnectTimeoutS, setDbConnectTimeoutS] = useState(10)
  const [modelListTimeoutS, setModelListTimeoutS] = useState(30)
  const [sqlGenerationTimeoutCapS, setSqlGenerationTimeoutCapS] = useState(120)
  const [jsonReaskTimeoutCapS, setJsonReaskTimeoutCapS] = useState(20)
  const [llmSubQueryTimeoutS, setLlmSubQueryTimeoutS] = useState(90)
  const [llmMergeTimeoutS, setLlmMergeTimeoutS] = useState(120)

  const [routeObservabilityWindowSeconds, setRouteObservabilityWindowSeconds] = useState(1800)
  const [routeObservabilityMaxEventsPerProject, setRouteObservabilityMaxEventsPerProject] = useState(20000)
  const [routeObservabilityPersistEnabled, setRouteObservabilityPersistEnabled] = useState(true)
  const [routeObservabilityPersistIntervalSeconds, setRouteObservabilityPersistIntervalSeconds] = useState(30)
  const [routeObservabilityPersistEventDelta, setRouteObservabilityPersistEventDelta] = useState(20)
  const [routeObservabilityStrategyTrendMaxPoints, setRouteObservabilityStrategyTrendMaxPoints] = useState(24)
  const [routeObservabilityStrategyTrendPersistIntervalSeconds, setRouteObservabilityStrategyTrendPersistIntervalSeconds] = useState(60)
  const [routeObservabilityStrategyTrendPersistDecisionDelta, setRouteObservabilityStrategyTrendPersistDecisionDelta] = useState(5)
  const [routeAlertRepairTimeoutShortCircuitWarningRate, setRouteAlertRepairTimeoutShortCircuitWarningRate] = useState(0.25)
  const [routeAlertRepairTimeoutShortCircuitCriticalRate, setRouteAlertRepairTimeoutShortCircuitCriticalRate] = useState(0.45)
  const [routeAlertRepairTimeoutShortCircuitMinWarningEvents, setRouteAlertRepairTimeoutShortCircuitMinWarningEvents] = useState(6)
  const [routeAlertRepairTimeoutShortCircuitMinCriticalEvents, setRouteAlertRepairTimeoutShortCircuitMinCriticalEvents] = useState(12)
  const [routeAlertRepairBudgetLowShortCircuitWarningRate, setRouteAlertRepairBudgetLowShortCircuitWarningRate] = useState(0.2)
  const [routeAlertRepairBudgetLowShortCircuitCriticalRate, setRouteAlertRepairBudgetLowShortCircuitCriticalRate] = useState(0.35)
  const [routeAlertRepairBudgetLowShortCircuitMinWarningEvents, setRouteAlertRepairBudgetLowShortCircuitMinWarningEvents] = useState(6)
  const [routeAlertRepairBudgetLowShortCircuitMinCriticalEvents, setRouteAlertRepairBudgetLowShortCircuitMinCriticalEvents] = useState(12)
  const [routeAlertJsonReaskWarningRate, setRouteAlertJsonReaskWarningRate] = useState(0.2)
  const [routeAlertJsonReaskCriticalRate, setRouteAlertJsonReaskCriticalRate] = useState(0.4)
  const [routeAlertJsonReaskMinWarningDecisions, setRouteAlertJsonReaskMinWarningDecisions] = useState(10)
  const [routeAlertJsonReaskMinCriticalDecisions, setRouteAlertJsonReaskMinCriticalDecisions] = useState(20)
  const [routeAlertDecomposeCancelledWarningRate, setRouteAlertDecomposeCancelledWarningRate] = useState(0.15)
  const [routeAlertDecomposeCancelledCriticalRate, setRouteAlertDecomposeCancelledCriticalRate] = useState(0.3)
  const [routeAlertDecomposeCancelledMinWarningEvents, setRouteAlertDecomposeCancelledMinWarningEvents] = useState(6)
  const [routeAlertDecomposeCancelledMinCriticalEvents, setRouteAlertDecomposeCancelledMinCriticalEvents] = useState(12)

  const [decomposeMergeStageBudgetS, setDecomposeMergeStageBudgetS] = useState(30)
  const [sqlGenerationTotalBudgetS, setSqlGenerationTotalBudgetS] = useState(120)
  const [sqlGenerationTimeoutMinS, setSqlGenerationTimeoutMinS] = useState(10)
  const [jsonReaskTimeoutMinS, setJsonReaskTimeoutMinS] = useState(5)
  const [duckdbDidYouMeanFixEnabled, setDuckdbDidYouMeanFixEnabled] = useState(true)
  const [duckdbDidYouMeanAllowInternalTables, setDuckdbDidYouMeanAllowInternalTables] = useState(false)
  const [duckdbDidYouMeanMaxRetries, setDuckdbDidYouMeanMaxRetries] = useState(2)
  const [sqlRouteAllowlistProjects, setSqlRouteAllowlistProjects] = useState('')

  const initialAskRef = useRef<Record<string, unknown>>({})
  const initialRouterRef = useRef<Record<string, unknown>>({})

  const [settingsApplied, setSettingsApplied] = useState(false)

  const settingValue = (plainKey: string, prefixedKey: string): unknown => {
    if (!settings || typeof settings !== 'object') return undefined
    const source = settings as Record<string, unknown>
    return source[plainKey] ?? source[prefixedKey]
  }

  useEffect(() => {
    if (settingsApplied || !settings) return

    setAskMaxSqlRows(parseNum(settingValue('max_sql_rows', 'ask_max_sql_rows'), 200))
    setAskDefaultPreviewRowLimit(parseNum(settingValue('default_preview_row_limit', 'ask_default_preview_row_limit'), 20))
    setAskMinPreviewRowLimit(parseNum(settingValue('min_preview_row_limit', 'ask_min_preview_row_limit'), 5))
    setAskMaxPreviewRowLimit(parseNum(settingValue('max_preview_row_limit', 'ask_max_preview_row_limit'), 100))
    setAskMaxSourceMaterializationRows(parseNum(settingValue('max_source_materialization_rows', 'ask_max_source_materialization_rows'), 5000))
    setAskAnalysisCacheMax(parseNum(settingValue('analysis_cache_max', 'ask_analysis_cache_max'), 128))
    setAskAnalysisCacheTtlS(parseNum(settingValue('analysis_cache_ttl_s', 'ask_analysis_cache_ttl_s'), 300))

    setTier1MaxRetries(parseNum(settingValue('tier1_max_retries', 'router_tier1_max_retries'), 1))
    setTier2MaxRetries(parseNum(settingValue('tier2_max_retries', 'router_tier2_max_retries'), 2))
    setTier3MaxRetries(parseNum(settingValue('tier3_max_retries', 'router_tier3_max_retries'), 2))
    setAdaptiveStrategyEnabled(parseBool(settingValue('adaptive_strategy_enabled', 'router_adaptive_strategy_enabled'), true))
    setAdaptiveStrategyConsensusRiskThreshold(parseNum(settingValue('adaptive_strategy_consensus_risk_threshold', 'router_adaptive_strategy_consensus_risk_threshold'), 4))
    setAdaptiveStrategyDecomposeRiskThreshold(parseNum(settingValue('adaptive_strategy_decompose_risk_threshold', 'router_adaptive_strategy_decompose_risk_threshold'), 7))
    setAdaptiveStrategyMinSubquestionsForDecompose(parseNum(settingValue('adaptive_strategy_min_subquestions_for_decompose', 'router_adaptive_strategy_min_subquestions_for_decompose'), 2))
    setTier1MaxColumnsPerModel(parseNum(settingValue('tier1_max_columns_per_model', 'router_tier1_max_columns_per_model'), 12))
    setTier2MaxColumnsPerModel(parseNum(settingValue('tier2_max_columns_per_model', 'router_tier2_max_columns_per_model'), 15))
    setTier3MaxColumnsPerModel(parseNum(settingValue('tier3_max_columns_per_model', 'router_tier3_max_columns_per_model'), 20))

    setSchemaPruningEnabled(parseBool(settingValue('schema_pruning_enabled', 'router_schema_pruning_enabled'), true))
    setGuidanceLlmAvailable(parseBool(settingValue('guidance_llm_available', 'router_guidance_llm_available'), true))
    setModelRefCaseSensitive(parseBool(settingValue('model_ref_case_sensitive', 'router_model_ref_case_sensitive'), true))
    setMetadataSummaryMaxModels(parseNum(settingValue('metadata_summary_max_models', 'router_metadata_summary_max_models'), 10))

    setSqlRouteV2Enabled(parseBool(settingValue('sql_route_v2_enabled', 'router_sql_route_v2_enabled'), true))
    setSqlRouteShadowMode(parseBool(settingValue('sql_route_shadow_mode', 'router_sql_route_shadow_mode'), false))
    setSqlRouteEventPersistEnabled(parseBool(settingValue('sql_route_event_persist_enabled', 'router_sql_route_event_persist_enabled'), true))
    setSqlRouteStrictJsonProbeEnabled(parseBool(settingValue('sql_route_strict_json_probe_enabled', 'router_sql_route_strict_json_probe_enabled'), true))
    setSqlRouteProfileId(String(settingValue('sql_route_profile_id', 'router_sql_route_profile_id') || 'prismbi.default'))
    setSqlRouteProfileVersion(String(settingValue('sql_route_profile_version', 'router_sql_route_profile_version') || 'v2'))

    setMaxSubQuestions(parseNum(settingValue('max_sub_questions', 'router_max_sub_questions'), 3))
    setMaxSuggestedQuestions(parseNum(settingValue('max_suggested_questions', 'router_max_suggested_questions'), 5))
    setDecomposeMergeEnabled(parseBool(settingValue('decompose_merge_enabled', 'router_decompose_merge_enabled'), true))
    setDecomposeMergeCircuitEnabled(parseBool(settingValue('decompose_merge_circuit_enabled', 'router_decompose_merge_circuit_enabled'), true))
    setDecomposeMergeFailureThreshold(parseNum(settingValue('decompose_merge_failure_threshold', 'router_decompose_merge_failure_threshold'), 1))
    setDecomposeMergeDisableSeconds(parseNum(settingValue('decompose_merge_disable_seconds', 'router_decompose_merge_disable_seconds'), 3600))

    setCrossSourceMaxWorkers(parseNum(settingValue('cross_source_max_workers', 'router_cross_source_max_workers'), 4))

    setExternalConnectionPoolEnabled(parseBool(settingValue('external_connection_pool_enabled', 'router_external_connection_pool_enabled'), true))
    setExternalConnectionPoolMaxPerKey(parseNum(settingValue('external_connection_pool_max_per_key', 'router_external_connection_pool_max_per_key'), 4))
    setExternalConnectionPoolIdleSeconds(parseNum(settingValue('external_connection_pool_idle_seconds', 'router_external_connection_pool_idle_seconds'), 300))

    setExecutionMetricsLogEvery(parseNum(settingValue('execution_metrics_log_every', 'router_execution_metrics_log_every'), 25))
    setExecutionMetricsLogIntervalSeconds(parseNum(settingValue('execution_metrics_log_interval_seconds', 'router_execution_metrics_log_interval_seconds'), 180))
    setExecutionMetricsMaxSamples(parseNum(settingValue('execution_metrics_max_samples', 'router_execution_metrics_max_samples'), 400))

    setRequestTimeoutMs(parseNum(settingValue('request_timeout_ms', 'timeout_request_ms'), 120000))
    setLlmReadTimeoutS(parseNum(settingValue('llm_read_timeout_s', 'timeout_llm_read_s'), 120))
    setLlmConnectTimeoutS(parseNum(settingValue('llm_connect_timeout_s', 'timeout_llm_connect_s'), 30))
    setLlmWriteTimeoutS(parseNum(settingValue('llm_write_timeout_s', 'timeout_llm_write_s'), 30))
    setLlmPoolTimeoutS(parseNum(settingValue('llm_pool_timeout_s', 'timeout_llm_pool_s'), 30))
    setDbConnectTimeoutS(parseNum(settingValue('db_connect_timeout_s', 'timeout_db_connect_s'), 10))
    setModelListTimeoutS(parseNum(settingValue('model_list_timeout_s', 'timeout_model_list_s'), 30))
    setSqlGenerationTimeoutCapS(parseNum(settingValue('sql_generation_timeout_cap_s', 'router_sql_generation_timeout_cap_s'), 120))
    setJsonReaskTimeoutCapS(parseNum(settingValue('json_reask_timeout_cap_s', 'router_json_reask_timeout_cap_s'), 20))
    setLlmSubQueryTimeoutS(parseNum(settingValue('llm_sub_query_timeout_s', 'router_llm_sub_query_timeout_s'), 90))
    setLlmMergeTimeoutS(parseNum(settingValue('llm_merge_timeout_s', 'router_llm_merge_timeout_s'), 120))

    setRouteObservabilityWindowSeconds(parseNum(settingValue('route_observability_window_seconds', 'router_route_observability_window_seconds'), 1800))
    setRouteObservabilityMaxEventsPerProject(parseNum(settingValue('route_observability_max_events_per_project', 'router_route_observability_max_events_per_project'), 20000))
    setRouteObservabilityPersistEnabled(parseBool(settingValue('route_observability_persist_enabled', 'router_route_observability_persist_enabled'), true))
    setRouteObservabilityPersistIntervalSeconds(parseNum(settingValue('route_observability_persist_interval_seconds', 'router_route_observability_persist_interval_seconds'), 30))
    setRouteObservabilityPersistEventDelta(parseNum(settingValue('route_observability_persist_event_delta', 'router_route_observability_persist_event_delta'), 20))
    setRouteObservabilityStrategyTrendMaxPoints(parseNum(settingValue('route_observability_strategy_trend_max_points', 'router_route_observability_strategy_trend_max_points'), 24))
    setRouteObservabilityStrategyTrendPersistIntervalSeconds(parseNum(settingValue('route_observability_strategy_trend_persist_interval_seconds', 'router_route_observability_strategy_trend_persist_interval_seconds'), 60))
    setRouteObservabilityStrategyTrendPersistDecisionDelta(parseNum(settingValue('route_observability_strategy_trend_persist_decision_delta', 'router_route_observability_strategy_trend_persist_decision_delta'), 5))
    setRouteAlertRepairTimeoutShortCircuitWarningRate(parseNum(settingValue('route_alert_repair_timeout_short_circuit_warning_rate', 'router_route_alert_repair_timeout_short_circuit_warning_rate'), 0.25))
    setRouteAlertRepairTimeoutShortCircuitCriticalRate(parseNum(settingValue('route_alert_repair_timeout_short_circuit_critical_rate', 'router_route_alert_repair_timeout_short_circuit_critical_rate'), 0.45))
    setRouteAlertRepairTimeoutShortCircuitMinWarningEvents(parseNum(settingValue('route_alert_repair_timeout_short_circuit_min_warning_events', 'router_route_alert_repair_timeout_short_circuit_min_warning_events'), 6))
    setRouteAlertRepairTimeoutShortCircuitMinCriticalEvents(parseNum(settingValue('route_alert_repair_timeout_short_circuit_min_critical_events', 'router_route_alert_repair_timeout_short_circuit_min_critical_events'), 12))
    setRouteAlertRepairBudgetLowShortCircuitWarningRate(parseNum(settingValue('route_alert_repair_budget_low_short_circuit_warning_rate', 'router_route_alert_repair_budget_low_short_circuit_warning_rate'), 0.2))
    setRouteAlertRepairBudgetLowShortCircuitCriticalRate(parseNum(settingValue('route_alert_repair_budget_low_short_circuit_critical_rate', 'router_route_alert_repair_budget_low_short_circuit_critical_rate'), 0.35))
    setRouteAlertRepairBudgetLowShortCircuitMinWarningEvents(parseNum(settingValue('route_alert_repair_budget_low_short_circuit_min_warning_events', 'router_route_alert_repair_budget_low_short_circuit_min_warning_events'), 6))
    setRouteAlertRepairBudgetLowShortCircuitMinCriticalEvents(parseNum(settingValue('route_alert_repair_budget_low_short_circuit_min_critical_events', 'router_route_alert_repair_budget_low_short_circuit_min_critical_events'), 12))
    setRouteAlertJsonReaskWarningRate(parseNum(settingValue('route_alert_json_reask_warning_rate', 'router_route_alert_json_reask_warning_rate'), 0.2))
    setRouteAlertJsonReaskCriticalRate(parseNum(settingValue('route_alert_json_reask_critical_rate', 'router_route_alert_json_reask_critical_rate'), 0.4))
    setRouteAlertJsonReaskMinWarningDecisions(parseNum(settingValue('route_alert_json_reask_min_warning_decisions', 'router_route_alert_json_reask_min_warning_decisions'), 10))
    setRouteAlertJsonReaskMinCriticalDecisions(parseNum(settingValue('route_alert_json_reask_min_critical_decisions', 'router_route_alert_json_reask_min_critical_decisions'), 20))
    setRouteAlertDecomposeCancelledWarningRate(parseNum(settingValue('route_alert_decompose_cancelled_warning_rate', 'router_route_alert_decompose_cancelled_warning_rate'), 0.15))
    setRouteAlertDecomposeCancelledCriticalRate(parseNum(settingValue('route_alert_decompose_cancelled_critical_rate', 'router_route_alert_decompose_cancelled_critical_rate'), 0.3))
    setRouteAlertDecomposeCancelledMinWarningEvents(parseNum(settingValue('route_alert_decompose_cancelled_min_warning_events', 'router_route_alert_decompose_cancelled_min_warning_events'), 6))
    setRouteAlertDecomposeCancelledMinCriticalEvents(parseNum(settingValue('route_alert_decompose_cancelled_min_critical_events', 'router_route_alert_decompose_cancelled_min_critical_events'), 12))

    setDecomposeMergeStageBudgetS(parseNum(settingValue('decompose_merge_stage_budget_s', 'router_decompose_merge_stage_budget_s'), 30))
    setSqlGenerationTotalBudgetS(parseNum(settingValue('sql_generation_total_budget_s', 'router_sql_generation_total_budget_s'), 120))
    setSqlGenerationTimeoutMinS(parseNum(settingValue('sql_generation_timeout_min_s', 'router_sql_generation_timeout_min_s'), 10))
    setJsonReaskTimeoutMinS(parseNum(settingValue('json_reask_timeout_min_s', 'router_json_reask_timeout_min_s'), 5))
    setDuckdbDidYouMeanFixEnabled(parseBool(settingValue('duckdb_did_you_mean_fix_enabled', 'router_duckdb_did_you_mean_fix_enabled'), true))
    setDuckdbDidYouMeanAllowInternalTables(parseBool(settingValue('duckdb_did_you_mean_allow_internal_tables', 'router_duckdb_did_you_mean_allow_internal_tables'), false))
    setDuckdbDidYouMeanMaxRetries(parseNum(settingValue('duckdb_did_you_mean_max_retries', 'router_duckdb_did_you_mean_max_retries'), 2))
    const rawAllowlist = settingValue('sql_route_allowlist_projects', 'router_sql_route_allowlist_projects')
    setSqlRouteAllowlistProjects(Array.isArray(rawAllowlist) ? rawAllowlist.join(', ') : String(rawAllowlist || ''))

    initialAskRef.current = {
      max_sql_rows: parseNum(settingValue('max_sql_rows', 'ask_max_sql_rows'), 200),
      default_preview_row_limit: parseNum(settingValue('default_preview_row_limit', 'ask_default_preview_row_limit'), 20),
      min_preview_row_limit: parseNum(settingValue('min_preview_row_limit', 'ask_min_preview_row_limit'), 5),
      max_preview_row_limit: parseNum(settingValue('max_preview_row_limit', 'ask_max_preview_row_limit'), 100),
      max_source_materialization_rows: parseNum(settingValue('max_source_materialization_rows', 'ask_max_source_materialization_rows'), 5000),
      analysis_cache_max: parseNum(settingValue('analysis_cache_max', 'ask_analysis_cache_max'), 128),
      analysis_cache_ttl_s: parseNum(settingValue('analysis_cache_ttl_s', 'ask_analysis_cache_ttl_s'), 300),
    }
    initialRouterRef.current = {
      tier1_max_retries: parseNum(settingValue('tier1_max_retries', 'router_tier1_max_retries'), 1),
      tier2_max_retries: parseNum(settingValue('tier2_max_retries', 'router_tier2_max_retries'), 2),
      tier3_max_retries: parseNum(settingValue('tier3_max_retries', 'router_tier3_max_retries'), 2),
      adaptive_strategy_enabled: parseBool(settingValue('adaptive_strategy_enabled', 'router_adaptive_strategy_enabled'), true),
      adaptive_strategy_consensus_risk_threshold: parseNum(settingValue('adaptive_strategy_consensus_risk_threshold', 'router_adaptive_strategy_consensus_risk_threshold'), 4),
      adaptive_strategy_decompose_risk_threshold: parseNum(settingValue('adaptive_strategy_decompose_risk_threshold', 'router_adaptive_strategy_decompose_risk_threshold'), 7),
      adaptive_strategy_min_subquestions_for_decompose: parseNum(settingValue('adaptive_strategy_min_subquestions_for_decompose', 'router_adaptive_strategy_min_subquestions_for_decompose'), 2),
      tier1_max_columns_per_model: parseNum(settingValue('tier1_max_columns_per_model', 'router_tier1_max_columns_per_model'), 12),
      tier2_max_columns_per_model: parseNum(settingValue('tier2_max_columns_per_model', 'router_tier2_max_columns_per_model'), 15),
      tier3_max_columns_per_model: parseNum(settingValue('tier3_max_columns_per_model', 'router_tier3_max_columns_per_model'), 20),
      schema_pruning_enabled: parseBool(settingValue('schema_pruning_enabled', 'router_schema_pruning_enabled'), true),
      guidance_llm_available: parseBool(settingValue('guidance_llm_available', 'router_guidance_llm_available'), true),
      model_ref_case_sensitive: parseBool(settingValue('model_ref_case_sensitive', 'router_model_ref_case_sensitive'), true),
      metadata_summary_max_models: parseNum(settingValue('metadata_summary_max_models', 'router_metadata_summary_max_models'), 10),
      sql_route_v2_enabled: parseBool(settingValue('sql_route_v2_enabled', 'router_sql_route_v2_enabled'), true),
      sql_route_shadow_mode: parseBool(settingValue('sql_route_shadow_mode', 'router_sql_route_shadow_mode'), false),
      sql_route_event_persist_enabled: parseBool(settingValue('sql_route_event_persist_enabled', 'router_sql_route_event_persist_enabled'), true),
      sql_route_strict_json_probe_enabled: parseBool(settingValue('sql_route_strict_json_probe_enabled', 'router_sql_route_strict_json_probe_enabled'), true),
      sql_route_profile_id: String(settingValue('sql_route_profile_id', 'router_sql_route_profile_id') || 'prismbi.default'),
      sql_route_profile_version: String(settingValue('sql_route_profile_version', 'router_sql_route_profile_version') || 'v2'),
      max_sub_questions: parseNum(settingValue('max_sub_questions', 'router_max_sub_questions'), 3),
      max_suggested_questions: parseNum(settingValue('max_suggested_questions', 'router_max_suggested_questions'), 5),
      decompose_merge_enabled: parseBool(settingValue('decompose_merge_enabled', 'router_decompose_merge_enabled'), true),
      decompose_merge_circuit_enabled: parseBool(settingValue('decompose_merge_circuit_enabled', 'router_decompose_merge_circuit_enabled'), true),
      decompose_merge_failure_threshold: parseNum(settingValue('decompose_merge_failure_threshold', 'router_decompose_merge_failure_threshold'), 1),
      decompose_merge_disable_seconds: parseNum(settingValue('decompose_merge_disable_seconds', 'router_decompose_merge_disable_seconds'), 3600),
      cross_source_max_workers: parseNum(settingValue('cross_source_max_workers', 'router_cross_source_max_workers'), 4),
      external_connection_pool_enabled: parseBool(settingValue('external_connection_pool_enabled', 'router_external_connection_pool_enabled'), true),
      external_connection_pool_max_per_key: parseNum(settingValue('external_connection_pool_max_per_key', 'router_external_connection_pool_max_per_key'), 4),
      external_connection_pool_idle_seconds: parseNum(settingValue('external_connection_pool_idle_seconds', 'router_external_connection_pool_idle_seconds'), 300),
      execution_metrics_log_every: parseNum(settingValue('execution_metrics_log_every', 'router_execution_metrics_log_every'), 25),
      execution_metrics_log_interval_seconds: parseNum(settingValue('execution_metrics_log_interval_seconds', 'router_execution_metrics_log_interval_seconds'), 180),
      execution_metrics_max_samples: parseNum(settingValue('execution_metrics_max_samples', 'router_execution_metrics_max_samples'), 400),
      request_timeout_ms: parseNum(settingValue('request_timeout_ms', 'timeout_request_ms'), 120000),
      llm_connect_timeout_s: parseNum(settingValue('llm_connect_timeout_s', 'timeout_llm_connect_s'), 30),
      llm_read_timeout_s: parseNum(settingValue('llm_read_timeout_s', 'timeout_llm_read_s'), 120),
      llm_write_timeout_s: parseNum(settingValue('llm_write_timeout_s', 'timeout_llm_write_s'), 30),
      llm_pool_timeout_s: parseNum(settingValue('llm_pool_timeout_s', 'timeout_llm_pool_s'), 30),
      db_connect_timeout_s: parseNum(settingValue('db_connect_timeout_s', 'timeout_db_connect_s'), 10),
      model_list_timeout_s: parseNum(settingValue('model_list_timeout_s', 'timeout_model_list_s'), 30),
      sql_generation_timeout_cap_s: parseNum(settingValue('sql_generation_timeout_cap_s', 'router_sql_generation_timeout_cap_s'), 120),
      json_reask_timeout_cap_s: parseNum(settingValue('json_reask_timeout_cap_s', 'router_json_reask_timeout_cap_s'), 20),
      llm_sub_query_timeout_s: parseNum(settingValue('llm_sub_query_timeout_s', 'router_llm_sub_query_timeout_s'), 90),
      llm_merge_timeout_s: parseNum(settingValue('llm_merge_timeout_s', 'router_llm_merge_timeout_s'), 120),
      route_observability_window_seconds: parseNum(settingValue('route_observability_window_seconds', 'router_route_observability_window_seconds'), 1800),
      route_observability_max_events_per_project: parseNum(settingValue('route_observability_max_events_per_project', 'router_route_observability_max_events_per_project'), 20000),
      route_observability_persist_enabled: parseBool(settingValue('route_observability_persist_enabled', 'router_route_observability_persist_enabled'), true),
      route_observability_persist_interval_seconds: parseNum(settingValue('route_observability_persist_interval_seconds', 'router_route_observability_persist_interval_seconds'), 30),
      route_observability_persist_event_delta: parseNum(settingValue('route_observability_persist_event_delta', 'router_route_observability_persist_event_delta'), 20),
      route_observability_strategy_trend_max_points: parseNum(settingValue('route_observability_strategy_trend_max_points', 'router_route_observability_strategy_trend_max_points'), 24),
      route_observability_strategy_trend_persist_interval_seconds: parseNum(settingValue('route_observability_strategy_trend_persist_interval_seconds', 'router_route_observability_strategy_trend_persist_interval_seconds'), 60),
      route_observability_strategy_trend_persist_decision_delta: parseNum(settingValue('route_observability_strategy_trend_persist_decision_delta', 'router_route_observability_strategy_trend_persist_decision_delta'), 5),
      route_alert_repair_timeout_short_circuit_warning_rate: parseNum(settingValue('route_alert_repair_timeout_short_circuit_warning_rate', 'router_route_alert_repair_timeout_short_circuit_warning_rate'), 0.25),
      route_alert_repair_timeout_short_circuit_critical_rate: parseNum(settingValue('route_alert_repair_timeout_short_circuit_critical_rate', 'router_route_alert_repair_timeout_short_circuit_critical_rate'), 0.45),
      route_alert_repair_timeout_short_circuit_min_warning_events: parseNum(settingValue('route_alert_repair_timeout_short_circuit_min_warning_events', 'router_route_alert_repair_timeout_short_circuit_min_warning_events'), 6),
      route_alert_repair_timeout_short_circuit_min_critical_events: parseNum(settingValue('route_alert_repair_timeout_short_circuit_min_critical_events', 'router_route_alert_repair_timeout_short_circuit_min_critical_events'), 12),
      route_alert_repair_budget_low_short_circuit_warning_rate: parseNum(settingValue('route_alert_repair_budget_low_short_circuit_warning_rate', 'router_route_alert_repair_budget_low_short_circuit_warning_rate'), 0.2),
      route_alert_repair_budget_low_short_circuit_critical_rate: parseNum(settingValue('route_alert_repair_budget_low_short_circuit_critical_rate', 'router_route_alert_repair_budget_low_short_circuit_critical_rate'), 0.35),
      route_alert_repair_budget_low_short_circuit_min_warning_events: parseNum(settingValue('route_alert_repair_budget_low_short_circuit_min_warning_events', 'router_route_alert_repair_budget_low_short_circuit_min_warning_events'), 6),
      route_alert_repair_budget_low_short_circuit_min_critical_events: parseNum(settingValue('route_alert_repair_budget_low_short_circuit_min_critical_events', 'router_route_alert_repair_budget_low_short_circuit_min_critical_events'), 12),
      route_alert_json_reask_warning_rate: parseNum(settingValue('route_alert_json_reask_warning_rate', 'router_route_alert_json_reask_warning_rate'), 0.2),
      route_alert_json_reask_critical_rate: parseNum(settingValue('route_alert_json_reask_critical_rate', 'router_route_alert_json_reask_critical_rate'), 0.4),
      route_alert_json_reask_min_warning_decisions: parseNum(settingValue('route_alert_json_reask_min_warning_decisions', 'router_route_alert_json_reask_min_warning_decisions'), 10),
      route_alert_json_reask_min_critical_decisions: parseNum(settingValue('route_alert_json_reask_min_critical_decisions', 'router_route_alert_json_reask_min_critical_decisions'), 20),
      route_alert_decompose_cancelled_warning_rate: parseNum(settingValue('route_alert_decompose_cancelled_warning_rate', 'router_route_alert_decompose_cancelled_warning_rate'), 0.15),
      route_alert_decompose_cancelled_critical_rate: parseNum(settingValue('route_alert_decompose_cancelled_critical_rate', 'router_route_alert_decompose_cancelled_critical_rate'), 0.3),
      route_alert_decompose_cancelled_min_warning_events: parseNum(settingValue('route_alert_decompose_cancelled_min_warning_events', 'router_route_alert_decompose_cancelled_min_warning_events'), 6),
      route_alert_decompose_cancelled_min_critical_events: parseNum(settingValue('route_alert_decompose_cancelled_min_critical_events', 'router_route_alert_decompose_cancelled_min_critical_events'), 12),
      decompose_merge_stage_budget_s: parseNum(settingValue('decompose_merge_stage_budget_s', 'router_decompose_merge_stage_budget_s'), 30),
      sql_generation_total_budget_s: parseNum(settingValue('sql_generation_total_budget_s', 'router_sql_generation_total_budget_s'), 120),
      sql_generation_timeout_min_s: parseNum(settingValue('sql_generation_timeout_min_s', 'router_sql_generation_timeout_min_s'), 10),
      json_reask_timeout_min_s: parseNum(settingValue('json_reask_timeout_min_s', 'router_json_reask_timeout_min_s'), 5),
      duckdb_did_you_mean_fix_enabled: parseBool(settingValue('duckdb_did_you_mean_fix_enabled', 'router_duckdb_did_you_mean_fix_enabled'), true),
      duckdb_did_you_mean_allow_internal_tables: parseBool(settingValue('duckdb_did_you_mean_allow_internal_tables', 'router_duckdb_did_you_mean_allow_internal_tables'), false),
      duckdb_did_you_mean_max_retries: parseNum(settingValue('duckdb_did_you_mean_max_retries', 'router_duckdb_did_you_mean_max_retries'), 2),
      sql_route_allowlist_projects: Array.isArray(rawAllowlist) ? rawAllowlist : (typeof rawAllowlist === 'string' && rawAllowlist ? String(rawAllowlist).split(',').map(Number).filter(Boolean) : []),
    }

    setSettingsApplied(true)
  }, [settings, settingsApplied])

  const askPayload = () => {
    const init = initialAskRef.current
    const p: Record<string, unknown> = {}
    if (askMaxSqlRows !== init.max_sql_rows) p.max_sql_rows = askMaxSqlRows
    if (askDefaultPreviewRowLimit !== init.default_preview_row_limit) p.default_preview_row_limit = askDefaultPreviewRowLimit
    if (askMinPreviewRowLimit !== init.min_preview_row_limit) p.min_preview_row_limit = askMinPreviewRowLimit
    if (askMaxPreviewRowLimit !== init.max_preview_row_limit) p.max_preview_row_limit = askMaxPreviewRowLimit
    if (askMaxSourceMaterializationRows !== init.max_source_materialization_rows) p.max_source_materialization_rows = askMaxSourceMaterializationRows
    if (askAnalysisCacheMax !== init.analysis_cache_max) p.analysis_cache_max = askAnalysisCacheMax
    if (askAnalysisCacheTtlS !== init.analysis_cache_ttl_s) p.analysis_cache_ttl_s = askAnalysisCacheTtlS
    return p
  }

  const routerPayload = () => {
    const init = initialRouterRef.current
    const p: Record<string, unknown> = {}
    const diff = (k: string, v: unknown) => {
      if (JSON.stringify(v) !== JSON.stringify(init[k])) p[k] = v
    }

    diff('adaptive_strategy_enabled', adaptiveStrategyEnabled)
    diff('adaptive_strategy_consensus_risk_threshold', Math.max(1, Math.round(adaptiveStrategyConsensusRiskThreshold)))
    diff('adaptive_strategy_decompose_risk_threshold', Math.max(1, Math.round(adaptiveStrategyConsensusRiskThreshold), Math.round(adaptiveStrategyDecomposeRiskThreshold)))
    diff('adaptive_strategy_min_subquestions_for_decompose', Math.max(1, Math.round(adaptiveStrategyMinSubquestionsForDecompose)))
    diff('tier1_max_retries', tier1MaxRetries)
    diff('tier2_max_retries', tier2MaxRetries)
    diff('tier3_max_retries', tier3MaxRetries)
    diff('tier1_max_columns_per_model', tier1MaxColumnsPerModel)
    diff('tier2_max_columns_per_model', tier2MaxColumnsPerModel)
    diff('tier3_max_columns_per_model', tier3MaxColumnsPerModel)
    diff('schema_pruning_enabled', schemaPruningEnabled)
    diff('guidance_llm_available', guidanceLlmAvailable)
    diff('model_ref_case_sensitive', modelRefCaseSensitive)
    diff('metadata_summary_max_models', metadataSummaryMaxModels)
    diff('sql_route_v2_enabled', sqlRouteV2Enabled)
    diff('sql_route_shadow_mode', sqlRouteShadowMode)
    diff('sql_route_event_persist_enabled', sqlRouteEventPersistEnabled)
    diff('sql_route_strict_json_probe_enabled', sqlRouteStrictJsonProbeEnabled)
    diff('sql_route_profile_id', sqlRouteProfileId)
    diff('sql_route_profile_version', sqlRouteProfileVersion)
    diff('max_sub_questions', maxSubQuestions)
    diff('max_suggested_questions', maxSuggestedQuestions)
    diff('decompose_merge_enabled', decomposeMergeEnabled)
    diff('decompose_merge_circuit_enabled', decomposeMergeCircuitEnabled)
    diff('decompose_merge_failure_threshold', decomposeMergeFailureThreshold)
    diff('decompose_merge_disable_seconds', decomposeMergeDisableSeconds)
    diff('cross_source_max_workers', crossSourceMaxWorkers)
    diff('external_connection_pool_enabled', externalConnectionPoolEnabled)
    diff('external_connection_pool_max_per_key', externalConnectionPoolMaxPerKey)
    diff('external_connection_pool_idle_seconds', externalConnectionPoolIdleSeconds)
    diff('execution_metrics_log_every', executionMetricsLogEvery)
    diff('execution_metrics_log_interval_seconds', executionMetricsLogIntervalSeconds)
    diff('execution_metrics_max_samples', executionMetricsMaxSamples)
    diff('request_timeout_ms', requestTimeoutMs)
    diff('llm_connect_timeout_s', llmConnectTimeoutS)
    diff('llm_read_timeout_s', llmReadTimeoutS)
    diff('llm_write_timeout_s', llmWriteTimeoutS)
    diff('llm_pool_timeout_s', llmPoolTimeoutS)
    diff('db_connect_timeout_s', dbConnectTimeoutS)
    diff('model_list_timeout_s', modelListTimeoutS)
    diff('sql_generation_timeout_cap_s', sqlGenerationTimeoutCapS)
    diff('json_reask_timeout_cap_s', jsonReaskTimeoutCapS)
    diff('llm_sub_query_timeout_s', llmSubQueryTimeoutS)
    diff('llm_merge_timeout_s', llmMergeTimeoutS)
    diff('route_observability_window_seconds', routeObservabilityWindowSeconds)
    diff('route_observability_max_events_per_project', routeObservabilityMaxEventsPerProject)
    diff('route_observability_persist_enabled', routeObservabilityPersistEnabled)
    diff('route_observability_persist_interval_seconds', routeObservabilityPersistIntervalSeconds)
    diff('route_observability_persist_event_delta', routeObservabilityPersistEventDelta)
    diff('route_observability_strategy_trend_max_points', routeObservabilityStrategyTrendMaxPoints)
    diff('route_observability_strategy_trend_persist_interval_seconds', routeObservabilityStrategyTrendPersistIntervalSeconds)
    diff('route_observability_strategy_trend_persist_decision_delta', routeObservabilityStrategyTrendPersistDecisionDelta)
    diff('route_alert_repair_timeout_short_circuit_warning_rate', Math.min(1, Math.max(0.01, Number(routeAlertRepairTimeoutShortCircuitWarningRate) || 0.25)))
    diff('route_alert_repair_timeout_short_circuit_critical_rate', Math.min(1, Math.max(Number(p.route_alert_repair_timeout_short_circuit_warning_rate) || 0.25, Math.min(1, Math.max(0.01, Number(routeAlertRepairTimeoutShortCircuitCriticalRate) || 0.45)))))
    diff('route_alert_repair_timeout_short_circuit_min_warning_events', Math.max(1, Math.round(Number(routeAlertRepairTimeoutShortCircuitMinWarningEvents) || 6)))
    diff('route_alert_repair_timeout_short_circuit_min_critical_events', Math.max(Number(p.route_alert_repair_timeout_short_circuit_min_warning_events) || 6, Math.max(1, Math.round(Number(routeAlertRepairTimeoutShortCircuitMinCriticalEvents) || 12))))
    diff('route_alert_repair_budget_low_short_circuit_warning_rate', Math.min(1, Math.max(0.01, Number(routeAlertRepairBudgetLowShortCircuitWarningRate) || 0.2)))
    diff('route_alert_repair_budget_low_short_circuit_critical_rate', Math.min(1, Math.max(Number(p.route_alert_repair_budget_low_short_circuit_warning_rate) || 0.2, Math.min(1, Math.max(0.01, Number(routeAlertRepairBudgetLowShortCircuitCriticalRate) || 0.35)))))
    diff('route_alert_repair_budget_low_short_circuit_min_warning_events', Math.max(1, Math.round(Number(routeAlertRepairBudgetLowShortCircuitMinWarningEvents) || 6)))
    diff('route_alert_repair_budget_low_short_circuit_min_critical_events', Math.max(Number(p.route_alert_repair_budget_low_short_circuit_min_warning_events) || 6, Math.max(1, Math.round(Number(routeAlertRepairBudgetLowShortCircuitMinCriticalEvents) || 12))))
    diff('route_alert_json_reask_warning_rate', Math.min(1, Math.max(0.01, Number(routeAlertJsonReaskWarningRate) || 0.2)))
    diff('route_alert_json_reask_critical_rate', Math.min(1, Math.max(Number(p.route_alert_json_reask_warning_rate) || 0.2, Math.min(1, Math.max(0.01, Number(routeAlertJsonReaskCriticalRate) || 0.4)))))
    diff('route_alert_json_reask_min_warning_decisions', Math.max(1, Math.round(Number(routeAlertJsonReaskMinWarningDecisions) || 10)))
    diff('route_alert_json_reask_min_critical_decisions', Math.max(Number(p.route_alert_json_reask_min_warning_decisions) || 10, Math.max(1, Math.round(Number(routeAlertJsonReaskMinCriticalDecisions) || 20))))
    diff('route_alert_decompose_cancelled_warning_rate', Math.min(1, Math.max(0.01, Number(routeAlertDecomposeCancelledWarningRate) || 0.15)))
    diff('route_alert_decompose_cancelled_critical_rate', Math.min(1, Math.max(Number(p.route_alert_decompose_cancelled_warning_rate) || 0.15, Math.min(1, Math.max(0.01, Number(routeAlertDecomposeCancelledCriticalRate) || 0.3)))))
    diff('route_alert_decompose_cancelled_min_warning_events', Math.max(1, Math.round(Number(routeAlertDecomposeCancelledMinWarningEvents) || 6)))
    diff('route_alert_decompose_cancelled_min_critical_events', Math.max(Number(p.route_alert_decompose_cancelled_min_warning_events) || 6, Math.max(1, Math.round(Number(routeAlertDecomposeCancelledMinCriticalEvents) || 12))))
    diff('decompose_merge_stage_budget_s', decomposeMergeStageBudgetS)
    diff('sql_generation_total_budget_s', sqlGenerationTotalBudgetS)
    diff('sql_generation_timeout_min_s', sqlGenerationTimeoutMinS)
    diff('json_reask_timeout_min_s', jsonReaskTimeoutMinS)
    diff('duckdb_did_you_mean_fix_enabled', duckdbDidYouMeanFixEnabled)
    diff('duckdb_did_you_mean_allow_internal_tables', duckdbDidYouMeanAllowInternalTables)
    diff('duckdb_did_you_mean_max_retries', duckdbDidYouMeanMaxRetries)

    const allowlist = sqlRouteAllowlistProjects
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
      .map(Number)
      .filter((n) => Number.isFinite(n) && n > 0)
    diff('sql_route_allowlist_projects', allowlist.length > 0 ? [...new Set(allowlist)] : [])

    return p
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
            SQL 问答限制
          </h3>
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="SQL 返回行数上限" type="number" value={askMaxSqlRows} onChange={(e) => setAskMaxSqlRows(parseInt(e.target.value) || 1)} min={1} max={100000} />
            <Input label="默认预览行数" type="number" value={askDefaultPreviewRowLimit} onChange={(e) => setAskDefaultPreviewRowLimit(parseInt(e.target.value) || 1)} min={1} max={100000} />
            <Input label="最小预览行数" type="number" value={askMinPreviewRowLimit} onChange={(e) => setAskMinPreviewRowLimit(parseInt(e.target.value) || 1)} min={1} max={100000} />
            <Input label="最大预览行数" type="number" value={askMaxPreviewRowLimit} onChange={(e) => setAskMaxPreviewRowLimit(parseInt(e.target.value) || 1)} min={1} max={100000} />
            <Input label="跨源物化行数上限" type="number" value={askMaxSourceMaterializationRows} onChange={(e) => setAskMaxSourceMaterializationRows(parseInt(e.target.value) || 100)} min={100} max={200000} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-5">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            LLM 超时设置
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            HTTP 连接超时和 AI 问答流程中各阶段的 LLM 调用超时。下限值由系统根据上限自动推导。
          </p>
          <SectionHeader title="HTTP 连接" />
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="请求超时（毫秒）" type="number" value={requestTimeoutMs} onChange={(e) => setRequestTimeoutMs(parseInt(e.target.value) || 1000)} min={1000} max={1800000} hint="所有 LLM HTTP 调用的请求超时" />
            <Input label="读取超时（秒）" type="number" value={llmReadTimeoutS} onChange={(e) => setLlmReadTimeoutS(parseInt(e.target.value) || 1)} min={1} max={3600} hint="等待 LLM 响应数据的超时" />
            <Input label="连接超时（秒）" type="number" value={llmConnectTimeoutS} onChange={(e) => setLlmConnectTimeoutS(parseInt(e.target.value) || 1)} min={1} max={3600} hint="TCP 连接超时" />
            <Input label="写入超时（秒）" type="number" value={llmWriteTimeoutS} onChange={(e) => setLlmWriteTimeoutS(parseInt(e.target.value) || 1)} min={1} max={3600} hint="LLM HTTP 写入超时" />
            <Input label="连接池超时（秒）" type="number" value={llmPoolTimeoutS} onChange={(e) => setLlmPoolTimeoutS(parseInt(e.target.value) || 1)} min={1} max={3600} />
            <Input label="模型列表查询超时（秒）" type="number" value={modelListTimeoutS} onChange={(e) => setModelListTimeoutS(parseInt(e.target.value) || 1)} min={1} max={3600} />
            <Input label="数据库连接超时（秒）" type="number" value={dbConnectTimeoutS} onChange={(e) => setDbConnectTimeoutS(parseInt(e.target.value) || 1)} min={1} max={3600} hint="外部数据库连接的建连超时" />
          </div>
          <div className="border-t border-gray-200 dark:border-gray-700" />
          <SectionHeader title="AI 问答调用" />
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="SQL 生成超时上限（秒）" type="number" step="0.1" value={sqlGenerationTimeoutCapS} onChange={(e) => setSqlGenerationTimeoutCapS(parseFloat(e.target.value) || 1)} min={1} max={300} hint="每次 SQL 生成 LLM 调用的最大等待时间" />
            <Input label="SQL 生成超时下限（秒）" type="number" step="0.1" value={sqlGenerationTimeoutMinS} onChange={(e) => setSqlGenerationTimeoutMinS(parseFloat(e.target.value) || 0.1)} min={0.1} max={60} hint="SQL 生成最小等待时间" />
            <Input label="SQL 生成总预算（秒）" type="number" step="0.1" value={sqlGenerationTotalBudgetS} onChange={(e) => setSqlGenerationTotalBudgetS(parseFloat(e.target.value) || 10)} min={10} max={900} hint="包含重试的总体 SQL 生成时间预算" />
            <Input label="JSON Re-ask 超时上限（秒）" type="number" step="0.1" value={jsonReaskTimeoutCapS} onChange={(e) => setJsonReaskTimeoutCapS(parseFloat(e.target.value) || 0.5)} min={0.5} max={120} hint="JSON 解析失败重试的最大等待时间" />
            <Input label="JSON Re-ask 超时下限（秒）" type="number" step="0.1" value={jsonReaskTimeoutMinS} onChange={(e) => setJsonReaskTimeoutMinS(parseFloat(e.target.value) || 0.1)} min={0.1} max={30} hint="JSON 解析失败重试的最小等待时间" />
            <Input label="子查询 LLM 超时（秒）" type="number" step="0.1" value={llmSubQueryTimeoutS} onChange={(e) => setLlmSubQueryTimeoutS(parseFloat(e.target.value) || 1)} min={1} max={300} hint="Decompose 子查询的 LLM 调用超时" />
            <Input label="合并 LLM 超时（秒）" type="number" step="0.1" value={llmMergeTimeoutS} onChange={(e) => setLlmMergeTimeoutS(parseFloat(e.target.value) || 1)} min={1} max={600} hint="Decompose 合并步骤的 LLM 调用超时" />
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
        <CardContent className="space-y-5">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">路由与策略</h3>
          </div>

          <div>
            <div className="mb-3 flex items-center justify-between">
              <SectionHeader title="Schema 剪枝" />
              <ToggleRow label="启用 Schema 剪枝" checked={schemaPruningEnabled} onChange={setSchemaPruningEnabled} />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="Tier 1 每模型保留列数" type="number" value={tier1MaxColumnsPerModel} onChange={(e) => setTier1MaxColumnsPerModel(parseInt(e.target.value) || 1)} min={1} max={500} />
              <Input label="Tier 2 每模型保留列数" type="number" value={tier2MaxColumnsPerModel} onChange={(e) => setTier2MaxColumnsPerModel(parseInt(e.target.value) || 1)} min={1} max={500} />
              <Input label="Tier 3 每模型保留列数" type="number" value={tier3MaxColumnsPerModel} onChange={(e) => setTier3MaxColumnsPerModel(parseInt(e.target.value) || 1)} min={1} max={500} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <SectionHeader title="重试次数" />
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="Tier 1（简单问题）" type="number" value={tier1MaxRetries} onChange={(e) => setTier1MaxRetries(parseInt(e.target.value) || 1)} min={1} max={10} />
              <Input label="Tier 2（多维问题）" type="number" value={tier2MaxRetries} onChange={(e) => setTier2MaxRetries(parseInt(e.target.value) || 1)} min={1} max={10} />
              <Input label="Tier 3（复合问题）" type="number" value={tier3MaxRetries} onChange={(e) => setTier3MaxRetries(parseInt(e.target.value) || 1)} min={1} max={10} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <div className="mb-3 flex items-center justify-between">
              <SectionHeader title="自适应策略池路由" />
              <ToggleRow label="启用自适应策略池" checked={adaptiveStrategyEnabled} onChange={setAdaptiveStrategyEnabled} />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="一致性策略风险阈值" type="number" value={adaptiveStrategyConsensusRiskThreshold} onChange={(e) => setAdaptiveStrategyConsensusRiskThreshold(parseInt(e.target.value) || 1)} min={1} max={20} disabled={!adaptiveStrategyEnabled} />
              <Input label="分解策略风险阈值" type="number" value={adaptiveStrategyDecomposeRiskThreshold} onChange={(e) => setAdaptiveStrategyDecomposeRiskThreshold(parseInt(e.target.value) || 1)} min={1} max={20} disabled={!adaptiveStrategyEnabled} />
              <Input label="分解最小子问题数" type="number" value={adaptiveStrategyMinSubquestionsForDecompose} onChange={(e) => setAdaptiveStrategyMinSubquestionsForDecompose(parseInt(e.target.value) || 1)} min={1} max={10} disabled={!adaptiveStrategyEnabled} />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <SectionHeader title="SQL 路由 v2" />
            <div className="mb-3 grid grid-cols-1 gap-x-6 gap-y-2 md:grid-cols-3">
              <ToggleRow label="启用 v2 路由" checked={sqlRouteV2Enabled} onChange={setSqlRouteV2Enabled} />
              <ToggleRow label="影子模式（仅对比）" checked={sqlRouteShadowMode} onChange={setSqlRouteShadowMode} />
              <ToggleRow label="持久化路由事件" checked={sqlRouteEventPersistEnabled} onChange={setSqlRouteEventPersistEnabled} />
              <ToggleRow label="LLM 严格 JSON 探测" checked={sqlRouteStrictJsonProbeEnabled} onChange={setSqlRouteStrictJsonProbeEnabled} />
              <ToggleRow label="启用引导 LLM" checked={guidanceLlmAvailable} onChange={setGuidanceLlmAvailable} />
              <ToggleRow label="SQL 模型引用区分大小写" checked={modelRefCaseSensitive} onChange={setModelRefCaseSensitive} />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="路由 Profile ID" value={sqlRouteProfileId} onChange={(e) => setSqlRouteProfileId(e.target.value)} />
              <Input label="路由 Profile 版本" value={sqlRouteProfileVersion} onChange={(e) => setSqlRouteProfileVersion(e.target.value)} />
              <Input label="元数据摘要最大模型数" type="number" value={metadataSummaryMaxModels} onChange={(e) => setMetadataSummaryMaxModels(parseInt(e.target.value) || 1)} min={1} max={200} />
              <Input label="路由项目白名单（逗号分隔）" value={sqlRouteAllowlistProjects} onChange={(e) => setSqlRouteAllowlistProjects(e.target.value)} hint="仅允许列表中的项目使用此路由，留空表示允许全部" />
            </div>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700" />

          <div>
            <div className="mb-3 flex items-center justify-between">
              <SectionHeader title="组合查询 (Decompose-Merge)" />
              <div className="flex items-center gap-4">
                <ToggleRow label="启用" checked={decomposeMergeEnabled} onChange={setDecomposeMergeEnabled} />
                <ToggleRow label="启用熔断器" checked={decomposeMergeCircuitEnabled} onChange={setDecomposeMergeCircuitEnabled} disabled={!decomposeMergeEnabled} />
              </div>
            </div>
            <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
              针对复杂问答的分解-合并处理策略，将复合问题拆解为多个子查询后分别处理再合并结果。
            </p>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="熔断阈值（失败次数）" type="number" value={decomposeMergeFailureThreshold} onChange={(e) => setDecomposeMergeFailureThreshold(parseInt(e.target.value) || 1)} min={1} max={20} disabled={!decomposeMergeEnabled || !decomposeMergeCircuitEnabled} />
              <Input label="熔断恢复时间（秒）" type="number" value={decomposeMergeDisableSeconds} onChange={(e) => setDecomposeMergeDisableSeconds(parseFloat(e.target.value) || 30)} min={30} max={86400} disabled={!decomposeMergeEnabled || !decomposeMergeCircuitEnabled} />
              <Input label="合并阶段超时（秒）" type="number" step="0.1" value={decomposeMergeStageBudgetS} onChange={(e) => setDecomposeMergeStageBudgetS(parseFloat(e.target.value) || 1)} min={1} max={900} disabled={!decomposeMergeEnabled} hint="Decompose-Merge 合并阶段的超时预算" />
              <Input label="最大子问题数" type="number" value={maxSubQuestions} onChange={(e) => setMaxSubQuestions(parseInt(e.target.value) || 1)} min={1} max={20} />
              <Input label="最大推荐问题数" type="number" value={maxSuggestedQuestions} onChange={(e) => setMaxSuggestedQuestions(parseInt(e.target.value) || 1)} min={1} max={20} />
            </div>
          </div>

          <div className="rounded-md border border-gray-200 p-3 dark:border-gray-700">
            <h5 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              DuckDB 自动修复
            </h5>
            <div className="mb-3 grid grid-cols-1 gap-x-6 gap-y-2 md:grid-cols-3">
              <ToggleRow label="启用 Did-You-Mean 修复" checked={duckdbDidYouMeanFixEnabled} onChange={setDuckdbDidYouMeanFixEnabled} />
              <ToggleRow label="允许内部表" checked={duckdbDidYouMeanAllowInternalTables} onChange={setDuckdbDidYouMeanAllowInternalTables} disabled={!duckdbDidYouMeanFixEnabled} />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
              <Input label="最大重试次数" type="number" value={duckdbDidYouMeanMaxRetries} onChange={(e) => setDuckdbDidYouMeanMaxRetries(parseInt(e.target.value) || 0)} min={0} max={5} disabled={!duckdbDidYouMeanFixEnabled} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-5">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">连接池与性能</h3>
            <ToggleRow label="启用外部数据库连接池" checked={externalConnectionPoolEnabled} onChange={setExternalConnectionPoolEnabled} />
          </div>

          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="跨源查询最大并行 Worker 数" type="number" value={crossSourceMaxWorkers} onChange={(e) => setCrossSourceMaxWorkers(parseInt(e.target.value) || 1)} min={1} max={32} />
            <Input label="每 Key 最大连接数" type="number" value={externalConnectionPoolMaxPerKey} onChange={(e) => setExternalConnectionPoolMaxPerKey(parseInt(e.target.value) || 1)} min={1} max={64} disabled={!externalConnectionPoolEnabled} />
            <Input label="空闲连接超时（秒）" type="number" value={externalConnectionPoolIdleSeconds} onChange={(e) => setExternalConnectionPoolIdleSeconds(parseFloat(e.target.value) || 30)} min={30} max={86400} disabled={!externalConnectionPoolEnabled} />
          </div>
          <div className="border-t border-gray-200 dark:border-gray-700" />
          <SectionHeader title="执行指标记录" />
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-3">
            <Input label="每 N 次执行记录日志" type="number" value={executionMetricsLogEvery} onChange={(e) => setExecutionMetricsLogEvery(parseInt(e.target.value) || 1)} min={1} max={2000} />
            <Input label="日志记录间隔（秒）" type="number" value={executionMetricsLogIntervalSeconds} onChange={(e) => setExecutionMetricsLogIntervalSeconds(parseFloat(e.target.value) || 10)} min={10} max={86400} />
            <Input label="最大采样数" type="number" value={executionMetricsMaxSamples} onChange={(e) => setExecutionMetricsMaxSamples(parseInt(e.target.value) || 50)} min={50} max={10000} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-5">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">可观测性</h3>
            <ToggleRow label="持久化快照" checked={routeObservabilityPersistEnabled} onChange={setRouteObservabilityPersistEnabled} />
          </div>

          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-4">
            <Input label="路由统计窗口（秒）" type="number" value={routeObservabilityWindowSeconds} onChange={(e) => setRouteObservabilityWindowSeconds(parseInt(e.target.value) || 300)} min={300} max={86400} />
            <Input label="每项目最大事件数" type="number" value={routeObservabilityMaxEventsPerProject} onChange={(e) => setRouteObservabilityMaxEventsPerProject(parseInt(e.target.value) || 1000)} min={1000} max={200000} />
            <Input label="策略趋势保留点数" type="number" value={routeObservabilityStrategyTrendMaxPoints} onChange={(e) => setRouteObservabilityStrategyTrendMaxPoints(parseInt(e.target.value) || 6)} min={6} max={240} />
            <Input label="快照持久化间隔（秒）" type="number" value={routeObservabilityPersistIntervalSeconds} onChange={(e) => setRouteObservabilityPersistIntervalSeconds(parseFloat(e.target.value) || 1)} min={1} max={3600} disabled={!routeObservabilityPersistEnabled} />
            <Input label="快照事件增量阈值" type="number" value={routeObservabilityPersistEventDelta} onChange={(e) => setRouteObservabilityPersistEventDelta(parseInt(e.target.value) || 1)} min={1} max={10000} disabled={!routeObservabilityPersistEnabled} />
            <Input label="策略趋势持久化间隔（秒）" type="number" value={routeObservabilityStrategyTrendPersistIntervalSeconds} onChange={(e) => setRouteObservabilityStrategyTrendPersistIntervalSeconds(parseFloat(e.target.value) || 1)} min={1} max={3600} disabled={!routeObservabilityPersistEnabled} />
            <Input label="策略趋势决策增量阈值" type="number" value={routeObservabilityStrategyTrendPersistDecisionDelta} onChange={(e) => setRouteObservabilityStrategyTrendPersistDecisionDelta(parseInt(e.target.value) || 1)} min={1} max={10000} disabled={!routeObservabilityPersistEnabled} />
          </div>

          <div className="rounded-md border border-gray-200 p-3 dark:border-gray-700">
            <h5 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              告警阈值 — Repair Short-Circuit（超时 / 预算不足）
            </h5>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-4">
              <Input label="超时 Warning 比例" type="number" step="0.01" value={routeAlertRepairTimeoutShortCircuitWarningRate} onChange={(e) => setRouteAlertRepairTimeoutShortCircuitWarningRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="超时 Critical 比例" type="number" step="0.01" value={routeAlertRepairTimeoutShortCircuitCriticalRate} onChange={(e) => setRouteAlertRepairTimeoutShortCircuitCriticalRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="超时 Warning 最小样本" type="number" value={routeAlertRepairTimeoutShortCircuitMinWarningEvents} onChange={(e) => setRouteAlertRepairTimeoutShortCircuitMinWarningEvents(parseInt(e.target.value) || 1)} min={1} max={10000} />
              <Input label="超时 Critical 最小样本" type="number" value={routeAlertRepairTimeoutShortCircuitMinCriticalEvents} onChange={(e) => setRouteAlertRepairTimeoutShortCircuitMinCriticalEvents(parseInt(e.target.value) || 1)} min={1} max={10000} />
              <Input label="预算不足 Warning 比例" type="number" step="0.01" value={routeAlertRepairBudgetLowShortCircuitWarningRate} onChange={(e) => setRouteAlertRepairBudgetLowShortCircuitWarningRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="预算不足 Critical 比例" type="number" step="0.01" value={routeAlertRepairBudgetLowShortCircuitCriticalRate} onChange={(e) => setRouteAlertRepairBudgetLowShortCircuitCriticalRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="预算不足 Warning 最小样本" type="number" value={routeAlertRepairBudgetLowShortCircuitMinWarningEvents} onChange={(e) => setRouteAlertRepairBudgetLowShortCircuitMinWarningEvents(parseInt(e.target.value) || 1)} min={1} max={10000} />
              <Input label="预算不足 Critical 最小样本" type="number" value={routeAlertRepairBudgetLowShortCircuitMinCriticalEvents} onChange={(e) => setRouteAlertRepairBudgetLowShortCircuitMinCriticalEvents(parseInt(e.target.value) || 1)} min={1} max={10000} />
            </div>
          </div>

          <div className="rounded-md border border-gray-200 p-3 dark:border-gray-700">
            <h5 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              告警阈值 — JSON Re-ask / Decompose Cancelled
            </h5>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-4">
              <Input label="JSON Re-ask Warning 比例" type="number" step="0.01" value={routeAlertJsonReaskWarningRate} onChange={(e) => setRouteAlertJsonReaskWarningRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="JSON Re-ask Critical 比例" type="number" step="0.01" value={routeAlertJsonReaskCriticalRate} onChange={(e) => setRouteAlertJsonReaskCriticalRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="JSON Re-ask Warning 最小决策数" type="number" value={routeAlertJsonReaskMinWarningDecisions} onChange={(e) => setRouteAlertJsonReaskMinWarningDecisions(parseInt(e.target.value) || 1)} min={1} max={10000} />
              <Input label="JSON Re-ask Critical 最小决策数" type="number" value={routeAlertJsonReaskMinCriticalDecisions} onChange={(e) => setRouteAlertJsonReaskMinCriticalDecisions(parseInt(e.target.value) || 1)} min={1} max={10000} />
              <Input label="Decompose Cancelled Warning 比例" type="number" step="0.01" value={routeAlertDecomposeCancelledWarningRate} onChange={(e) => setRouteAlertDecomposeCancelledWarningRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="Decompose Cancelled Critical 比例" type="number" step="0.01" value={routeAlertDecomposeCancelledCriticalRate} onChange={(e) => setRouteAlertDecomposeCancelledCriticalRate(parseFloat(e.target.value) || 0.01)} min={0.01} max={1} />
              <Input label="Decompose Cancelled Warning 最小样本" type="number" value={routeAlertDecomposeCancelledMinWarningEvents} onChange={(e) => setRouteAlertDecomposeCancelledMinWarningEvents(parseInt(e.target.value) || 1)} min={1} max={10000} />
              <Input label="Decompose Cancelled Critical 最小样本" type="number" value={routeAlertDecomposeCancelledMinCriticalEvents} onChange={(e) => setRouteAlertDecomposeCancelledMinCriticalEvents(parseInt(e.target.value) || 1)} min={1} max={10000} />
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-end gap-3">
        <Button type="button" onClick={handleSaveAll} loading={savingAsk || savingRouter} disabled={!canSave}>
          保存全部
        </Button>
      </div>
    </div>
  )
}
