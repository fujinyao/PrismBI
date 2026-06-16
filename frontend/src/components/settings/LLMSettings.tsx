'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { useI18nStore } from '@/stores/i18nStore'
import { useToast } from '@/components/ui/Toast'
import { useQueryClient } from '@tanstack/react-query'
import { authApi, settingsApi } from '@/lib/api'
import { createSSE, type SSEClient } from '@/lib/sse'

interface LLMSettingsProps {
  settings: any
  onSave: (s: any) => Promise<any> | void
  saving?: boolean
  canSave?: boolean
}

type ProbeRunStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  github_copilot: 'GitHub Copilot',
  opencode_zen: 'opencode Zen',
  maxkb: 'MaxKB',
  ollama: 'Ollama',
  vllm: 'vLLM',
  custom: 'Custom',
}

const PROVIDER_DEFAULTS: Record<string, { endpoint: string; model: string; maxTokens: number; temperature: number }> = {
  openai: {
    endpoint: 'https://api.openai.com/v1',
    model: 'gpt-4o',
    maxTokens: 4096,
    temperature: 0.7,
  },
  anthropic: {
    endpoint: 'https://api.anthropic.com',
    model: 'claude-3-5-sonnet-latest',
    maxTokens: 4096,
    temperature: 0.7,
  },
  github_copilot: {
    endpoint: 'https://api.githubcopilot.com',
    model: 'gpt-4o-copilot',
    maxTokens: 4096,
    temperature: 0.7,
  },
  opencode_zen: {
    endpoint: 'https://opencode.ai/zen/v1',
    model: 'zen-1',
    maxTokens: 4096,
    temperature: 0.7,
  },
  maxkb: {
    endpoint: 'http://localhost:8080/v1',
    model: 'maxkb',
    maxTokens: 4096,
    temperature: 0.7,
  },
  ollama: {
    endpoint: 'http://localhost:11434/v1',
    model: 'llama3.1',
    maxTokens: 4096,
    temperature: 0.7,
  },
  vllm: {
    endpoint: 'http://localhost:8000/v1',
    model: 'qwen2.5',
    maxTokens: 4096,
    temperature: 0.7,
  },
  custom: {
    endpoint: '',
    model: '',
    maxTokens: 4096,
    temperature: 0.7,
  },
}

const DEFAULT_SYSTEM_PROMPT = `You are PrismBI, an AI business intelligence assistant that helps users analyze data through natural language.

Core behaviors:
1. When a project context is provided, answer data questions by generating accurate SQL against the project's semantic model.
2. When no project context is provided, answer as a general assistant — never invent or fabricate project data, query results, or business metrics.
3. Always distinguish between factual data (backed by query results) and explanatory context (from general knowledge).
4. If the user's question is ambiguous, briefly clarify what you assumed before answering.

Answer truthfully, concisely, and in the user's language. If you cannot answer with available data, say so clearly.`

const ADVANCED_DEFAULTS = {
  maxRetries: 3,
  retryBaseDelaySeconds: 1,
  retryMaxDelaySeconds: 10,
  httpCircuitEnabled: true,
  httpCircuitFailureThreshold: 3,
  httpCircuitOpenSeconds: 60,
  chatHistoryLimit: 5,
  generalChatHistoryLimit: 3,
}

const parseAdvancedNumber = (value: unknown, fallback: number): number => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const parseAdvancedBoolean = (value: unknown, fallback: boolean): boolean => {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (value === 1) return true
    if (value === 0) return false
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'true' || normalized === '1') return true
    if (normalized === 'false' || normalized === '0') return false
  }
  return fallback
}

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (value && typeof value === 'object') return value as Record<string, unknown>
  return null
}

const asProbeStatus = (value: unknown): ProbeRunStatus | null => {
  if (
    value === 'queued'
    || value === 'running'
    || value === 'completed'
    || value === 'failed'
    || value === 'cancelled'
    || value === 'idle'
  ) {
    return value
  }
  return null
}

export function LLMSettings({ settings, onSave, saving, canSave = true }: LLMSettingsProps) {
  const t = useI18nStore((s) => s.t)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [provider, setProvider] = useState(settings.llm_provider ?? settings.provider ?? 'openai')
  const [apiKey, setApiKey] = useState(settings.llm_api_key ?? settings.apiKey ?? '')
  const [showKey, setShowKey] = useState(false)
  const [model, setModel] = useState(settings.llm_model ?? settings.model ?? 'gpt-4o')
  const [systemPrompt, setSystemPrompt] = useState(settings.llm_system_prompt ?? settings.systemPrompt ?? DEFAULT_SYSTEM_PROMPT)
  const [temperature, setTemperature] = useState(settings.llm_temperature ?? settings.temperature ?? 0.7)
  const [maxTokens, setMaxTokens] = useState(settings.llm_max_tokens ?? settings.maxTokens ?? 4096)
  const [baseUrl, setBaseUrl] = useState(settings.llm_endpoint ?? settings.baseUrl ?? '')
  const [whitelistEnabled, setWhitelistEnabled] = useState(false)
  const [whitelistPrefixes, setWhitelistPrefixes] = useState<string[]>([])
  const [whitelistDefaults, setWhitelistDefaults] = useState<string[]>([])
  const [newPrefix, setNewPrefix] = useState('')
  const [whitelistSaving, setWhitelistSaving] = useState(false)
  const [whitelistLoaded, setWhitelistLoaded] = useState(false)
  const modelRef = useRef(model)
  const probeSseRef = useRef<SSEClient | null>(null)
  const [modelList, setModelList] = useState<string[]>([])
  const [modelListError, setModelListError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'success' | 'error' | null>(null)
  const [testErrorMsg, setTestErrorMsg] = useState<string | null>(null)
  const [probeMode, setProbeMode] = useState<'fast' | 'full'>('fast')
  const [probeSessionId, setProbeSessionId] = useState<string | null>(null)
  const [probeStatus, setProbeStatus] = useState<ProbeRunStatus>('idle')
  const [probeStep, setProbeStep] = useState<string | null>(null)
  const [capabilitySaved, setCapabilitySaved] = useState(false)
  const [testProbe, setTestProbe] = useState<{
    tier?: string
    modelKey?: string
    probeLevel?: string
    probedAt?: string
    capabilities?: Record<string, unknown>
  } | null>(null)

  const [llmMaxRetries, setLlmMaxRetries] = useState(ADVANCED_DEFAULTS.maxRetries)
  const [retryBaseDelaySeconds, setRetryBaseDelaySeconds] = useState(ADVANCED_DEFAULTS.retryBaseDelaySeconds)
  const [retryMaxDelaySeconds, setRetryMaxDelaySeconds] = useState(ADVANCED_DEFAULTS.retryMaxDelaySeconds)
  const [httpCircuitEnabled, setHttpCircuitEnabled] = useState(ADVANCED_DEFAULTS.httpCircuitEnabled)
  const [httpCircuitFailureThreshold, setHttpCircuitFailureThreshold] = useState(ADVANCED_DEFAULTS.httpCircuitFailureThreshold)
  const [httpCircuitOpenSeconds, setHttpCircuitOpenSeconds] = useState(ADVANCED_DEFAULTS.httpCircuitOpenSeconds)
  const [chatHistoryLimit, setChatHistoryLimit] = useState(ADVANCED_DEFAULTS.chatHistoryLimit)
  const [generalChatHistoryLimit, setGeneralChatHistoryLimit] = useState(ADVANCED_DEFAULTS.generalChatHistoryLimit)
  const [advancedLoaded, setAdvancedLoaded] = useState(false)
  const [advancedSaving, setAdvancedSaving] = useState(false)

  const [settingsLoaded, setSettingsLoaded] = useState(false)

  useEffect(() => {
    modelRef.current = model
  }, [model])

  const closeProbeStream = useCallback(() => {
    if (probeSseRef.current) {
      probeSseRef.current.close()
      probeSseRef.current = null
    }
  }, [])

  const applyProbeEvent = useCallback((raw: unknown) => {
    const payload = asRecord(raw)
    if (!payload) return

    const payloadStatus = asProbeStatus(payload.status)
    if (payloadStatus) {
      setProbeStatus(payloadStatus)
      if (payloadStatus === 'failed') {
        setTestResult('error')
      }
      if (payloadStatus === 'cancelled') {
        setTestResult('error')
        setTestErrorMsg((prev) => prev || t('settings.llm.probeCancelled', 'Probe was cancelled'))
      }
      if (payloadStatus === 'completed' || payloadStatus === 'failed' || payloadStatus === 'cancelled') {
        closeProbeStream()
      }
    }
    if (typeof payload.error === 'string' && payload.error) {
      setTestErrorMsg(payload.error)
    }
    if (typeof payload.capability_saved === 'boolean') {
      setCapabilitySaved(payload.capability_saved)
    }
    if (typeof payload.session_id === 'string' && payload.session_id) {
      setProbeSessionId(payload.session_id)
    }

    const probeName = typeof payload.probe === 'string' ? payload.probe : null
    const stage = typeof payload.stage === 'number' ? payload.stage : null
    const stageTotal = typeof payload.stage_total === 'number' ? payload.stage_total : null
    if (probeName) {
      if (stage !== null && stageTotal !== null) {
        setProbeStep(`${probeName} (${stage}/${stageTotal})`)
      } else {
        setProbeStep(probeName)
      }
    }

    const payloadTier = typeof payload.tier === 'string' ? payload.tier : undefined
    const payloadProbeLevel = typeof payload.probe_level === 'string' ? payload.probe_level : undefined
    const payloadProbedAt = typeof payload.probed_at === 'string' ? payload.probed_at : undefined
    const payloadModelKey = typeof payload.model_key === 'string' ? payload.model_key : undefined

    const caps = asRecord(payload.capabilities)
    if (caps) {
      const probeMeta = asRecord(caps.probe_meta)
      const metaModelKey = probeMeta && typeof probeMeta.model_key === 'string' ? probeMeta.model_key : undefined
      const metaProbeLevel = probeMeta && typeof probeMeta.probe_level === 'string' ? probeMeta.probe_level : undefined
      const metaProbedAt = probeMeta && typeof probeMeta.probed_at === 'string' ? probeMeta.probed_at : undefined
      setTestProbe((prev) => ({
        tier: payloadTier ?? prev?.tier,
        modelKey: payloadModelKey ?? metaModelKey ?? prev?.modelKey,
        probeLevel: payloadProbeLevel ?? metaProbeLevel ?? prev?.probeLevel,
        probedAt: payloadProbedAt ?? metaProbedAt ?? prev?.probedAt,
        capabilities: caps,
      }))
      if (payloadStatus === 'completed') {
        setTestResult('success')
      }
      return
    }

    if (payloadTier || payloadProbeLevel || payloadProbedAt || payloadModelKey) {
      setTestProbe((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          tier: payloadTier ?? prev.tier,
          modelKey: payloadModelKey ?? prev.modelKey,
          probeLevel: payloadProbeLevel ?? prev.probeLevel,
          probedAt: payloadProbedAt ?? prev.probedAt,
        }
      })
    }
  }, [closeProbeStream, t])

  const openProbeStream = useCallback(async (sessionId: string) => {
    if (!sessionId) return
    closeProbeStream()
    try {
      const ticketResp = await authApi.wsTicket()
      const ticket = ticketResp?.ticket
      if (!ticket) {
        throw new Error('Missing stream ticket')
      }
      const streamUrl = `/api/settings/llm/test/stream?session_id=${encodeURIComponent(sessionId)}&ticket=${encodeURIComponent(ticket)}`
      const stream = createSSE(streamUrl, () => null)
      probeSseRef.current = stream
      const eventNames = [
        'session_state',
        'probe_queued',
        'probe_started',
        'stage_started',
        'stage_completed',
        'probe_completed',
        'probe_done',
        'probe_failed',
        'probe_cancelled',
        'progress',
        'message',
      ]
      eventNames.forEach((name) => {
        stream.onEvent(name, applyProbeEvent)
      })
      stream.onMessage(applyProbeEvent)
    } catch (err) {
      setProbeStatus('failed')
      setTestErrorMsg(err instanceof Error ? err.message : 'Failed to open probe stream')
    }
  }, [applyProbeEvent, closeProbeStream])

  useEffect(() => () => {
    closeProbeStream()
  }, [closeProbeStream])

  useEffect(() => {
    closeProbeStream()
    setTestResult(null)
    setTestErrorMsg(null)
    setTestProbe(null)
    setProbeSessionId(null)
    setProbeStatus('idle')
    setProbeStep(null)
    setCapabilitySaved(false)
  }, [provider, model, baseUrl, apiKey, closeProbeStream])

  useEffect(() => {
    if (settingsLoaded || !settings) return
    setProvider(settings.llm_provider ?? settings.provider ?? 'openai')
    setApiKey(settings.llm_api_key ?? settings.apiKey ?? '')
    setModel(settings.llm_model ?? settings.model ?? 'gpt-4o')
    setSystemPrompt(settings.llm_system_prompt ?? settings.systemPrompt ?? DEFAULT_SYSTEM_PROMPT)
    setTemperature(settings.llm_temperature ?? settings.temperature ?? 0.7)
    setMaxTokens(settings.llm_max_tokens ?? settings.maxTokens ?? 4096)
    setBaseUrl(settings.llm_endpoint ?? settings.baseUrl ?? '')
    setSettingsLoaded(true)
  }, [settings, settingsLoaded])

  useEffect(() => {
    queryClient
      .fetchQuery({
        queryKey: ['settings', 'llm-whitelist'],
        queryFn: () => settingsApi.llmWhitelist(),
        staleTime: 30_000,
      })
      .then((data) => {
        setWhitelistEnabled(data.enabled)
        setWhitelistPrefixes(data.prefixes)
        setWhitelistDefaults(data.defaults)
        setWhitelistLoaded(true)
      })
      .catch(() => {
        setWhitelistLoaded(true)
      })
  }, [queryClient])

  useEffect(() => {
    if (advancedLoaded) return
    let active = true

    const applyAdvancedSettings = (data: Record<string, unknown>) => {
      setLlmMaxRetries(parseAdvancedNumber(data.max_retries, ADVANCED_DEFAULTS.maxRetries))
      setRetryBaseDelaySeconds(parseAdvancedNumber(data.retry_base_delay_s, ADVANCED_DEFAULTS.retryBaseDelaySeconds))
      setRetryMaxDelaySeconds(parseAdvancedNumber(data.retry_max_delay_s, ADVANCED_DEFAULTS.retryMaxDelaySeconds))
      setHttpCircuitEnabled(parseAdvancedBoolean(data.http_circuit_enabled, ADVANCED_DEFAULTS.httpCircuitEnabled))
      setHttpCircuitFailureThreshold(
        parseAdvancedNumber(data.http_circuit_failure_threshold, ADVANCED_DEFAULTS.httpCircuitFailureThreshold),
      )
      setHttpCircuitOpenSeconds(parseAdvancedNumber(data.http_circuit_open_seconds, ADVANCED_DEFAULTS.httpCircuitOpenSeconds))
      setChatHistoryLimit(parseAdvancedNumber(data.chat_history_limit, ADVANCED_DEFAULTS.chatHistoryLimit))
      setGeneralChatHistoryLimit(
        parseAdvancedNumber(data.general_chat_history_limit, ADVANCED_DEFAULTS.generalChatHistoryLimit),
      )
    }

    const fallbackData: Record<string, unknown> = {
      max_retries: settings.llm_max_retries,
      retry_base_delay_s: settings.llm_retry_base_delay_s,
      retry_max_delay_s: settings.llm_retry_max_delay_s,
      http_circuit_enabled: settings.llm_http_circuit_enabled,
      http_circuit_failure_threshold: settings.llm_http_circuit_failure_threshold,
      http_circuit_open_seconds: settings.llm_http_circuit_open_seconds,
      chat_history_limit: settings.llm_chat_history_limit,
      general_chat_history_limit: settings.llm_general_chat_history_limit,
    }

    queryClient
      .fetchQuery({
        queryKey: ['settings', 'llm-advanced'],
        queryFn: () => settingsApi.llmAdvanced(),
        staleTime: 30_000,
      })
      .then((data) => {
        if (!active) return
        applyAdvancedSettings(data as Record<string, unknown>)
      })
      .catch(() => {
        if (!active) return
        applyAdvancedSettings(fallbackData)
      })
      .finally(() => {
        if (!active) return
        setAdvancedLoaded(true)
      })

    return () => {
      active = false
    }
  }, [advancedLoaded, queryClient, settings])

  const fetchModels = useCallback(async () => {
    setModelListError(null)
    try {
      const result = await settingsApi.llmModels({ provider, api_key: apiKey, endpoint: baseUrl })
      if (result.models?.length) {
        setModelList(result.models)
        const currentModel = modelRef.current
        if (!result.models.includes(currentModel)) {
          setModel(result.models[0])
        }
      } else {
        setModelList([])
        setModelListError(result.error || 'No models returned')
      }
    } catch {
      setModelListError('Failed to fetch models')
    }
  }, [provider, apiKey, baseUrl])

  const handleProviderChange = (nextProvider: string) => {
    const defaults = PROVIDER_DEFAULTS[nextProvider]
    closeProbeStream()
    setProvider(nextProvider)
    setApiKey('')
    setModelList([])
    setModelListError(null)
    if (defaults) {
      setBaseUrl(defaults.endpoint)
      setModel(defaults.model)
      setMaxTokens(defaults.maxTokens)
      setTemperature(defaults.temperature)
    }
    setTestResult(null)
    setTestErrorMsg(null)
    setTestProbe(null)
    setProbeSessionId(null)
    setProbeStatus('idle')
    setProbeStep(null)
    setCapabilitySaved(false)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const payload: Record<string, unknown> = { provider, model, temperature, max_tokens: maxTokens, endpoint: baseUrl, system_prompt: systemPrompt }
    if (apiKey) payload.api_key = apiKey
    if (probeSessionId) payload.probe_session_id = probeSessionId
    if (testResult === 'success' && testProbe?.capabilities) {
      payload.probed_capabilities = testProbe.capabilities
    }
    try {
      const response = await onSave(payload)
      const result = asRecord(response)
      if (!result) return
      if (typeof result.capability_saved === 'boolean') {
        setCapabilitySaved(result.capability_saved)
      }
      if (typeof result.probe_session_id === 'string' && result.probe_session_id) {
        setProbeSessionId(result.probe_session_id)
      }
      const caps = asRecord(result.capabilities)
      if (caps) {
        const probeMeta = asRecord(caps.probe_meta)
        const metaModelKey = probeMeta && typeof probeMeta.model_key === 'string' ? probeMeta.model_key : undefined
        const metaProbeLevel = probeMeta && typeof probeMeta.probe_level === 'string' ? probeMeta.probe_level : undefined
        const metaProbedAt = probeMeta && typeof probeMeta.probed_at === 'string' ? probeMeta.probed_at : undefined
        setTestProbe((prev) => ({
          tier: typeof result.tier === 'string' ? result.tier : prev?.tier,
          modelKey: metaModelKey ?? prev?.modelKey,
          probeLevel: metaProbeLevel ?? prev?.probeLevel,
          probedAt: metaProbedAt ?? prev?.probedAt,
          capabilities: caps,
        }))
      }
    } catch {
      // mutation already handles user-facing error feedback
    }
  }

  const handleTest = async () => {
    closeProbeStream()
    setTesting(true)
    setTestResult(null)
    setTestErrorMsg(null)
    setTestProbe(null)
    setProbeSessionId(null)
    setProbeStatus('idle')
    setProbeStep(null)
    setCapabilitySaved(false)
    try {
      const result = await settingsApi.llmTest({
        provider,
        api_key: apiKey,
        model,
        endpoint: baseUrl,
        probe_level: probeMode,
      })
      if (result.success) {
        setTestResult('success')
        const incomingStatus = asProbeStatus(result.probe_status)
        setProbeStatus(incomingStatus ?? 'running')
        setCapabilitySaved(Boolean(result.capability_saved))
        const incomingSessionId = typeof result.probe_session_id === 'string' ? result.probe_session_id : null
        setProbeSessionId(incomingSessionId)
        setProbeStep(incomingStatus === 'completed' ? null : t('settings.llm.probeRunning', 'Probing...'))
        setTestProbe({
          tier: result.tier,
          modelKey: result.model_key,
          probeLevel: result.probe_level,
          probedAt: result.probed_at,
          capabilities: result.capabilities,
        })
        if (incomingSessionId) {
          void openProbeStream(incomingSessionId)
        }
      } else {
        setTestResult('error')
        setProbeStatus('failed')
        setTestErrorMsg(result.error || 'Unknown error')
      }
    } catch (err) {
      setTestResult('error')
      setProbeStatus('failed')
      setTestErrorMsg(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setTesting(false)
    }
  }

  const handleSaveWhitelist = async () => {
    setWhitelistSaving(true)
    try {
      await settingsApi.llmWhitelistUpdate({ enabled: whitelistEnabled, prefixes: whitelistPrefixes })
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'llm-whitelist'] })
      toast(t('toast.whitelistSaved', 'Whitelist settings saved'), 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('toast.whitelistSaveFailed', 'Failed to save whitelist'), 'error')
    } finally {
      setWhitelistSaving(false)
    }
  }

  const handleSaveAdvanced = async () => {
    if (!canSave) return
    setAdvancedSaving(true)
    try {
      await settingsApi.llmAdvancedUpdate({
        max_retries: Math.min(10, Math.max(1, Math.round(llmMaxRetries))),
        retry_base_delay_s: Math.min(60, Math.max(0, retryBaseDelaySeconds)),
        retry_max_delay_s: Math.min(300, Math.max(0.1, retryMaxDelaySeconds)),
        http_circuit_enabled: httpCircuitEnabled,
        http_circuit_failure_threshold: Math.min(100, Math.max(1, Math.round(httpCircuitFailureThreshold))),
        http_circuit_open_seconds: Math.min(3600, Math.max(1, httpCircuitOpenSeconds)),
        chat_history_limit: Math.min(50, Math.max(1, Math.round(chatHistoryLimit))),
        general_chat_history_limit: Math.min(50, Math.max(1, Math.round(generalChatHistoryLimit))),
      })
      queryClient.invalidateQueries({ queryKey: ['settings', 'private'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'audit-summary'] })
      queryClient.invalidateQueries({ queryKey: ['settings', 'llm-advanced'] })
      toast(t('toast.llmAdvancedSaved', 'LLM advanced settings saved'), 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : t('toast.llmAdvancedSaveFailed', 'Failed to save LLM advanced settings'), 'error')
    } finally {
      setAdvancedSaving(false)
    }
  }

  const handleAddPrefix = () => {
    const p = newPrefix.trim()
    if (p && !whitelistPrefixes.includes(p)) {
      setWhitelistPrefixes([...whitelistPrefixes, p])
      setNewPrefix('')
    }
  }

  const handleRemovePrefix = (prefix: string) => {
    setWhitelistPrefixes(whitelistPrefixes.filter(p => p !== prefix))
  }

  const handleResetWhitelist = () => {
    setWhitelistPrefixes([...whitelistDefaults])
  }

  const capabilityBool = (section: string, key: string): string | null => {
    const caps = testProbe?.capabilities
    if (!caps || typeof caps !== 'object') return null
    const part = (caps as Record<string, unknown>)[section]
    if (!part || typeof part !== 'object') return null
    const raw = (part as Record<string, unknown>)[key]
    if (typeof raw !== 'boolean') return null
    return raw ? t('common.yes', 'Yes') : t('common.no', 'No')
  }

  const capabilityText = (section: string, key: string): string | null => {
    const caps = testProbe?.capabilities
    if (!caps || typeof caps !== 'object') return null
    const part = (caps as Record<string, unknown>)[section]
    if (!part || typeof part !== 'object') return null
    const raw = (part as Record<string, unknown>)[key]
    if (raw == null) return null
    if (typeof raw === 'number') {
      return Number.isFinite(raw) ? String(raw) : null
    }
    if (typeof raw === 'string') return raw
    return null
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <Card>
        <CardContent className="space-y-4">
          <Card>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.llm.provider', 'Provider')}
                </label>
                <select
                  value={provider}
                  onChange={(e) => handleProviderChange(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  <option value="openai">{t('settings.llm.providerOpenai', 'OpenAI')}</option>
                  <option value="anthropic">{t('settings.llm.providerAnthropic', 'Anthropic')}</option>
                  <option value="github_copilot">{t('settings.llm.providerGithubCopilot', 'GitHub Copilot')}</option>
                  <option value="opencode_zen">{t('settings.llm.providerOpencodeZen', 'opencode Zen')}</option>
                  <option value="maxkb">{t('settings.llm.providerMaxKB', 'MaxKB')}</option>
                  <option value="ollama">{t('settings.llm.providerOllama', 'Ollama')}</option>
                  <option value="vllm">{t('settings.llm.providerVllm', 'vLLM')}</option>
                  <option value="custom">{t('settings.llm.providerCustom', 'Custom')}</option>
                </select>
              </div>

              <div className="relative">
                <Input
                  label={t('settings.llm.apiKey', 'API Key')}
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={t('settings.llm.apiKeyPlaceholder', 'sk-...')}
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  className="absolute right-3 top-[34px] text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  {showKey ? t('settings.llm.hide', 'Hide') : t('settings.llm.show', 'Show')}
                </button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.llm.modelName', 'Model Name')}
                </label>
                {modelList.length > 0 ? (
                  <select
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                  >
                    {modelList.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                ) : (
                  <Input
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    onFocus={() => { if (baseUrl) fetchModels() }}
                    placeholder={t('settings.llm.modelPlaceholder', 'gpt-4o')}
                  />
                )}
                {modelListError && (
                  <p className="mt-1 text-xs text-error-500">{modelListError}</p>
                )}
              </div>

              <div>
                <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.llm.temperature', 'Temperature: ')}{temperature.toFixed(1)}
                </label>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={temperature}
                  onChange={(e) => setTemperature(parseFloat(e.target.value))}
                  className="w-full accent-primary"
                />
                <div className="flex justify-between text-xs text-gray-400">
                  <span>{t('settings.llm.precise', '0 (Precise)')}</span>
                  <span>{t('settings.llm.creative', '2 (Creative)')}</span>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Input
                label={t('settings.llm.maxTokens', 'Max Tokens')}
                type="number"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value) || 0)}
                min={1}
                max={128000}
              />

              <Input
                label={t('settings.llm.baseUrl', 'Base URL')}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={t('settings.llm.baseUrlPlaceholder', 'https://api.example.com/v1')}
              />
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                {t('settings.llm.systemPrompt', 'System Prompt')}
              </label>
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={7}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                placeholder={DEFAULT_SYSTEM_PROMPT}
              />
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {t('settings.llm.systemPromptVars', 'Variables: {{app_name}}, {{language}}, {{timezone}}, {{date_format}}, {{llm_provider}}, {{llm_model}}, {{current_date}}, {{current_datetime}}')}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <label className="text-xs font-medium text-gray-700 dark:text-gray-300">
                  {t('settings.llm.probeMode', 'Probe Mode')}
                </label>
                <select
                  value={probeMode}
                  onChange={(e) => setProbeMode((e.target.value === 'full' ? 'full' : 'fast'))}
                  className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                >
                  <option value="fast">{t('settings.llm.probeModeFast', 'Fast (recommended)')}</option>
                  <option value="full">{t('settings.llm.probeModeFull', 'Full')}</option>
                </select>
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  {probeMode === 'fast'
                    ? t('settings.llm.probeModeFastHint', 'Fast: metadata + JSON + single latency probe.')
                    : t('settings.llm.probeModeFullHint', 'Full: includes system/SQL/repair checks + 3 latency probes.')}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={handleTest}
                  loading={testing}
                  disabled={testing || probeStatus === 'running' || probeStatus === 'queued'}
                >
                  {t('settings.llm.testConnection', 'Test Connection')}
                </Button>
                <div className="ml-auto">
                  <Button type="submit" loading={saving} disabled={!canSave}>{t('settings.llm.save', 'Save LLM Settings')}</Button>
                </div>
              </div>
              {testResult === 'success' && (
                <div className="space-y-2 rounded-md border border-success-200 bg-success-50 p-3 dark:border-success-900/40 dark:bg-success-900/10">
                  <span className="text-sm font-medium text-success-700 dark:text-success-300">
                    {t('settings.llm.connectionSuccess', 'Connection successful!')}
                  </span>
                  {testProbe && (
                    <div className="space-y-2 text-xs text-success-800 dark:text-success-200">
                      <div className="grid gap-1 md:grid-cols-2">
                        <div>
                          <span className="font-medium">{t('settings.llm.probeTier', 'Tier')}:</span> {testProbe.tier || '-'}
                        </div>
                        <div>
                          <span className="font-medium">{t('settings.llm.probeStatus', 'Probe Status')}:</span> {probeStatus}
                        </div>
                        <div>
                          <span className="font-medium">{t('settings.llm.probeLevel', 'Probe Level')}:</span> {testProbe.probeLevel || '-'}
                        </div>
                        <div>
                          <span className="font-medium">{t('settings.llm.capabilitySaved', 'Capabilities Saved')}:</span> {capabilitySaved ? t('common.yes', 'Yes') : t('common.no', 'No')}
                        </div>
                        <div className="md:col-span-2 break-all">
                          <span className="font-medium">{t('settings.llm.modelKey', 'Model Key')}:</span> {testProbe.modelKey || '-'}
                        </div>
                        <div className="md:col-span-2 break-all">
                          <span className="font-medium">{t('settings.llm.probeSession', 'Probe Session')}:</span> {probeSessionId || '-'}
                        </div>
                        <div className="md:col-span-2">
                          <span className="font-medium">{t('settings.llm.probeStep', 'Current Step')}:</span> {probeStep || '-'}
                        </div>
                        <div className="md:col-span-2">
                          <span className="font-medium">{t('settings.llm.probedAt', 'Probed At')}:</span> {testProbe.probedAt || '-'}
                        </div>
                      </div>

                      <div className="grid gap-1 md:grid-cols-2">
                        <div>
                          <span className="font-medium">JSON Schema:</span> {capabilityBool('structured_output', 'supports_json_schema') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">JSON Object:</span> {capabilityBool('structured_output', 'supports_json_object') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">JSON Reliable:</span> {capabilityBool('structured_output', 'json_mode_reliable') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">SQL Tier:</span> {capabilityText('sql_quality', 'sql_accuracy_tier') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">Hallucination Risk:</span> {capabilityText('sql_quality', 'sql_hallucination_risk') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">Repair Capable:</span> {capabilityBool('repair', 'repair_capability') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">Streaming:</span> {capabilityBool('performance', 'supports_streaming') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">Vision:</span> {capabilityBool('performance', 'supports_vision') ?? '-'}
                        </div>
                        <div>
                          <span className="font-medium">Tool Calling:</span> {capabilityBool('performance', 'supports_tool_calling') ?? '-'}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
              {testResult === 'error' && (
                <div>
                  <span className="text-sm font-medium text-error-600 dark:text-error-400">
                    {t('settings.llm.connectionFailed', 'Connection failed. Check your settings.')}
                  </span>
                  {testErrorMsg && (
                    <p className="mt-1 text-xs text-error-500 break-all">{testErrorMsg}</p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.llm.endpointWhitelist', 'Endpoint Whitelist')}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={whitelistEnabled}
                onChange={(e) => setWhitelistEnabled(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
              />
              {t('settings.llm.whitelistEnabled', 'Enable endpoint whitelist')}
            </label>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {t('settings.llm.whitelistHint', 'When enabled, only whitelisted URL prefixes are allowed for LLM endpoints.')}
            </span>
          </div>

          {whitelistEnabled && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {whitelistPrefixes.map((prefix) => (
                  <span
                    key={prefix}
                    className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-800 dark:bg-gray-700 dark:text-gray-200"
                  >
                    {prefix}
                    <button
                      type="button"
                      onClick={() => handleRemovePrefix(prefix)}
                      className="ml-1 text-gray-400 hover:text-error-500 dark:hover:text-error-400"
                    >
                      &times;
                    </button>
                  </span>
                ))}
              </div>

              <div className="flex gap-2">
                <Input
                  placeholder={t('settings.llm.whitelistPlaceholder', 'e.g. http://192.168.1.')}
                  value={newPrefix}
                  onChange={(e) => setNewPrefix(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddPrefix() } }}
                />
                <Button type="button" variant="secondary" onClick={handleAddPrefix} disabled={!newPrefix.trim()}>
                  {t('settings.llm.addPrefix', 'Add')}
                </Button>
              </div>

              <div className="flex gap-2">
                <Button type="button" variant="secondary" onClick={handleResetWhitelist} disabled={!whitelistLoaded}>
                  {t('settings.llm.resetDefaults', 'Reset Defaults')}
                </Button>
                <Button type="button" onClick={handleSaveWhitelist} loading={whitelistSaving} disabled={!canSave || !whitelistLoaded}>
                  {t('settings.llm.saveWhitelist', 'Save Whitelist')}
                </Button>
              </div>
            </div>
          )}

          {!whitelistEnabled && (
            <p className="text-xs text-warning-600 dark:text-warning-400">
              {t('settings.llm.whitelistDisabledWarning', 'Warning: All HTTP endpoints are allowed when whitelist is disabled. This may pose a security risk.')}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.llm.advancedTitle', 'LLM Advanced')}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Input
              label={t('settings.llm.maxRetries', 'Max Retries')}
              type="number"
              value={llmMaxRetries}
              onChange={(e) => setLlmMaxRetries(parseInt(e.target.value, 10) || 1)}
              min={1}
              max={10}
              disabled={!advancedLoaded}
            />
            <Input
              label={t('settings.llm.retryBaseDelay', 'Retry Base Delay (seconds)')}
              type="number"
              value={retryBaseDelaySeconds}
              onChange={(e) => setRetryBaseDelaySeconds(parseFloat(e.target.value) || 0)}
              min={0}
              max={60}
              step={0.1}
              disabled={!advancedLoaded}
            />
            <Input
              label={t('settings.llm.retryMaxDelay', 'Retry Max Delay (seconds)')}
              type="number"
              value={retryMaxDelaySeconds}
              onChange={(e) => setRetryMaxDelaySeconds(parseFloat(e.target.value) || 0.1)}
              min={0.1}
              max={300}
              step={0.1}
              disabled={!advancedLoaded}
            />
            <Input
              label={t('settings.llm.chatHistoryLimit', 'Chat History Limit')}
              type="number"
              value={chatHistoryLimit}
              onChange={(e) => setChatHistoryLimit(parseInt(e.target.value, 10) || 1)}
              min={1}
              max={50}
              disabled={!advancedLoaded}
            />
            <Input
              label={t('settings.llm.generalChatHistoryLimit', 'General Chat History Limit')}
              type="number"
              value={generalChatHistoryLimit}
              onChange={(e) => setGeneralChatHistoryLimit(parseInt(e.target.value, 10) || 1)}
              min={1}
              max={50}
              disabled={!advancedLoaded}
            />
            <div className="rounded-md border border-gray-200 px-3 py-2 dark:border-gray-700">
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  checked={httpCircuitEnabled}
                  onChange={(e) => setHttpCircuitEnabled(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                  disabled={!advancedLoaded}
                />
                {t('settings.llm.httpCircuitEnabled', 'Enable LLM HTTP circuit breaker')}
              </label>
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                {t(
                  'settings.llm.httpCircuitHint',
                  'When opened, transient upstream failures fast-fail for a cooldown window to avoid retry amplification.',
                )}
              </p>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Input
              label={t('settings.llm.httpCircuitFailureThreshold', 'Circuit Failure Threshold')}
              type="number"
              value={httpCircuitFailureThreshold}
              onChange={(e) => setHttpCircuitFailureThreshold(parseInt(e.target.value, 10) || 1)}
              min={1}
              max={100}
              disabled={!advancedLoaded || !httpCircuitEnabled}
            />
            <Input
              label={t('settings.llm.httpCircuitOpenSeconds', 'Circuit Open Duration (seconds)')}
              type="number"
              value={httpCircuitOpenSeconds}
              onChange={(e) => setHttpCircuitOpenSeconds(parseFloat(e.target.value) || 1)}
              min={1}
              max={3600}
              step={1}
              disabled={!advancedLoaded || !httpCircuitEnabled}
            />
          </div>

          <div className="flex justify-end">
            <Button
              type="button"
              onClick={handleSaveAdvanced}
              loading={advancedSaving}
              disabled={!canSave || !advancedLoaded}
            >
              {t('settings.llm.saveAdvanced', 'Save Advanced Settings')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </form>
  )
}
