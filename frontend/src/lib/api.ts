import { clearAppQueryCache } from '@/lib/queryClientEvents'
import { generateId } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'

const DEFAULT_REQUEST_TIMEOUT = 120000
let _requestTimeout = DEFAULT_REQUEST_TIMEOUT

export function getRequestTimeout(): number {
  return _requestTimeout
}

export function setRequestTimeout(ms: number): void {
  _requestTimeout = ms > 0 ? ms : DEFAULT_REQUEST_TIMEOUT
}

const API_BASE = '/api'

interface ApiError {
  code: string
  message: string
  details?: unknown
}

interface ApiResponse<T> {
  data: T | null
  error: ApiError | null
}

export interface Permission {
  id?: number
  resource: string
  action: string
  description?: string
}

export interface Role {
  id: number
  name: string
  scope?: string
  description?: string
  is_system?: boolean
  permissions?: Permission[]
  member_count?: number
  userCount?: number
  permissionsCount?: number
  project_id?: number | null
  expires_at?: string | null
}

export interface User {
  id: number
  username: string
  display_name?: string
  email?: string
  status: string
  default_project_id?: number
  last_login_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  roles?: Role[]
  role_id?: number | null
  role?: string
  permissions?: Permission[]
}

export interface AuditLog {
  id: number | string
  user_id?: number | null
  event_type: string
  resource_type?: string | null
  resource_id?: string | null
  action?: string | null
  detail?: unknown
  ip_address?: string | null
  user_agent?: string | null
  status?: string | null
  created_at?: string | null
}

export interface SettingsAuditSummaryScope {
  events: number
  last_updated?: string | null
  changed_fields: Record<string, number>
  actions: Record<string, number>
}

export interface SettingsAuditSummaryItem {
  event_type: string
  scope: string
  user_id?: number | null
  resource_id?: string | null
  action?: string | null
  created_at?: string | null
  changed_fields: string[]
}

export interface SettingsAuditSummary {
  scanned_events: number
  matched_events: number
  scope?: string | null
  latest_offset: number
  latest_limit: number
  by_scope: Record<string, SettingsAuditSummaryScope>
  latest: SettingsAuditSummaryItem[]
}

export interface RowSecurityPolicy {
  id: number
  project_id: number
  role_id: number
  model_name: string
  column_name: string
  operator: string
  value?: string | null
  value_source: 'literal' | 'user_attribute' | string
  user_attribute?: string | null
  filter_expression?: string | null
  description?: string | null
  is_enabled: boolean
  created_at?: string | null
}

export interface ColumnSecurityPolicy {
  id: number
  project_id: number
  role_id: number
  model_name: string
  column_name: string
  access_type: 'HIDE' | 'MASK' | string
  mask_with?: string | null
  is_enabled: boolean
  created_at?: string | null
}

export interface BackupEntry {
  name: string
  created_at: string
  size: number
  db_size?: number | null
  project_files: number
  has_wal: boolean
  has_key: boolean
  valid: boolean
}

export interface ThreadResponseAnswerDetail {
  status?: string
  content?: string | null
  error?: string | null
  numRowsUsedInLLM?: number
  previewRowLimit?: number
  queryId?: string | null
  columns?: string[]
  rows?: Record<string, unknown>[]
  totalRows?: number
  executionTimeMs?: number
  securityPlan?: unknown
  metadataQuestionPart?: string
  nonMetadataQuestionPart?: string
  processSteps?: ChatProcessStep[]
}

export interface QueryResult {
  columns: string[]
  rows: Record<string, unknown>[]
  total_rows: number
  execution_time_ms?: number
  warning?: string
  security_plan?: unknown
}

export interface QueryExecutionMetric {
  total: number
  success: number
  warning: number
  error: number
  timeout: number
  avg_ms: number
  p95_ms: number
  avg_rows: number
  last_updated: number
}

export type QueryExecutionMetrics = Record<string, QueryExecutionMetric>

export interface QueryRouteDimensions {
  events_total: number
  route_kind: Record<string, number>
  generation_engine: Record<string, number>
  strict_json_mode: Record<string, number>
  generation_decision_total: number
  fallback_count_total: number
  fallback_count_avg: number
  fallback_count_max: number
  repair_used: number
  generation_retry_reason: Record<string, number>
  validation_issue_bucket: Record<string, number>
  llm_empty_response_retry: number
  repair_guard_blocked: number
  repair_short_circuit: number
  repair_short_circuit_reason: Record<string, number>
  schema_link_fallback_total: number
  schema_link_fallback_reason: Record<string, number>
  schema_link_fallback_rate: number
  sql_generation_fallback_total: number
  sql_generation_fallback_reason: Record<string, number>
  sql_generation_fallback_rate: number
  final_answer_fallback_total: number
  final_answer_fallback_reason: Record<string, number>
  final_answer_fallback_rate: number
  window_seconds: number
  last_updated: number
}

export interface QueryLLMHttpCircuitKeyState {
  state: string
  remaining_open_seconds: number
  consecutive_failures: number
}

export interface QueryLLMHttpCircuitSnapshot {
  total_keys: number
  open_keys: number
  keys: Record<string, QueryLLMHttpCircuitKeyState>
}

export interface QueryExecutionMetricsWithRouteDimensions {
  by_datasource: QueryExecutionMetrics
  route_dimensions: QueryRouteDimensions
  llm_http_circuit?: QueryLLMHttpCircuitSnapshot
}

export interface RouterRuntimeSnapshot {
  max_sql_rows?: number
  default_preview_row_limit?: number
  min_preview_row_limit?: number
  max_preview_row_limit?: number
  max_source_materialization_rows?: number
  analysis_cache_max?: number
  analysis_cache_ttl_s?: number
  route_observability_window_seconds?: number
  route_observability_max_events_per_project?: number
  route_observability_persist_enabled?: boolean
  route_observability_persist_interval_seconds?: number
  route_observability_persist_event_delta?: number
  sql_route_v2_enabled?: boolean
  sql_route_shadow_mode?: boolean
  sql_route_profile_id?: string
  sql_route_profile_version?: string
}

export interface ChatProcessStep {
  key?: string
  title?: string
  status?: string
  detail?: string | null
}

export interface ThreadResponseAskingTask {
  type?: string
  status?: string
  traceId?: string | null
  queryId?: string | null
  invalidSql?: string | null
  candidates?: unknown[]
  retrievedTables?: string[]
  rephrasedQuestion?: string | null
  intentReasoning?: string | null
  sqlGenerationReasoning?: string | null
  error?: string | null
  metadataQuestionPart?: string | null
  nonMetadataQuestionPart?: string | null
  sqlEngine?: string | null
  processSteps?: ChatProcessStep[]
}

export interface ThreadResponseBreakdownDetail {
  status?: string
  description?: string | null
  error?: string | null
  queryId?: string | null
  steps?: string[]
  processSteps?: ChatProcessStep[]
}

export interface ThreadResponse {
  id: number
  thread_id?: number
  user_id?: number
  question: string
  sql?: string | null
  askingTask?: ThreadResponseAskingTask
  answerDetail?: ThreadResponseAnswerDetail | null
  breakdownDetail?: ThreadResponseBreakdownDetail | null
  chartDetail?: Record<string, unknown> | null
  adjustment?: Record<string, unknown> | null
  created_at?: string
}

export interface ThreadDetail {
  id: number
  project_id: number
  summary?: string
  summary_manual?: boolean
  response_count?: number
  preview_row_limit?: number
  created_at?: string
  updated_at?: string
  responses: ThreadResponse[]
}

let _refreshPromise: Promise<boolean> | null = null
let _isRedirecting = false

class HttpClient {
  private baseUrl: string

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl
  }

  private getToken(): string | null {
    if (typeof window === 'undefined') return null
    try {
      const token = useAuthStore.getState().token
      if (typeof token === 'string' && token.length > 0) return token
    } catch {
      /* ignore */
    }
    try {
      const raw = localStorage.getItem('auth-store')
      if (raw) {
        const parsed = JSON.parse(raw)
        const token = parsed?.state?.token ?? parsed?.token
        if (typeof token === 'string' && token.length > 0) return token
      }
    } catch {
      /* ignore */
    }
    return null
  }

  private setToken(token: string): void {
    if (typeof window === 'undefined') return
    try {
      useAuthStore.setState({ token, isAuthenticated: true })
    } catch {
      /* ignore */
    }
  }

  private async tryRefreshToken(): Promise<boolean> {
    if (_refreshPromise) return _refreshPromise
    _refreshPromise = this._doRefresh()
    try {
      const result = await _refreshPromise
      return result
    } finally {
      _refreshPromise = null
    }
  }

  private async _doRefresh(): Promise<boolean> {
    try {
      const token = this.getToken()
      if (!token) return false
      const res = await fetch(`${this.baseUrl}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
      })
      if (!res.ok) return false
      const json = await res.json()
      const newToken: string | undefined = json?.data?.token
      if (!newToken) return false
      this.setToken(newToken)
      return true
    } catch {
      return false
    }
  }

  private requestId(): string {
    return `req-${generateId()}`
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    params?: Record<string, string | number | boolean | undefined>,
    isRetry = false,
    extraHeaders?: Record<string, string>,
  ): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`, typeof window !== 'undefined' ? window.location.origin : 'http://localhost')
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined) url.searchParams.set(k, String(v))
      })
    }

    const isFormData = typeof FormData !== 'undefined' && body instanceof FormData
    const headers: Record<string, string> = {
      'X-Request-Id': this.requestId(),
    }
    if (!isFormData) headers['Content-Type'] = 'application/json'

    const token = this.getToken()
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    if (extraHeaders) {
      Object.assign(headers, extraHeaders)
    }

    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
    const timeoutToken = '__PRISMBI_TIMEOUT__'
    let timeout: ReturnType<typeof setTimeout> | null = null

    let res: Response
    try {
      const fetchPromise = fetch(url.toString(), {
        method,
        headers,
        signal: controller?.signal,
        body: body ? (isFormData ? body : JSON.stringify(body)) : undefined,
      })
      const timeoutPromise = new Promise<Response>((_, reject) => {
        timeout = setTimeout(() => {
          controller?.abort()
          reject(new Error(timeoutToken))
        }, _requestTimeout)
      })
      res = await Promise.race([fetchPromise, timeoutPromise])
    } catch (err) {
      if (err instanceof Error && err.message === timeoutToken) {
        throw new ApiClientError('TIMEOUT', 'Request timed out')
      }
      throw err
    } finally {
      if (timeout) clearTimeout(timeout)
    }

    if (res.status === 401 && !isRetry) {
      const refreshed = await this.tryRefreshToken()
      if (refreshed) {
        return this.request<T>(method, path, body, params, true, extraHeaders)
      }
      if (typeof window !== 'undefined' && !_isRedirecting) {
        _isRedirecting = true
        localStorage.removeItem('auth-store')
        clearAppQueryCache()
        const currentPath = window.location.pathname + window.location.search
        const safeRedirect = (currentPath !== '/login' && !currentPath.startsWith('//')) ? `?redirect=${encodeURIComponent(currentPath)}` : ''
        window.location.href = `/login${safeRedirect}`
        setTimeout(() => { _isRedirecting = false }, 5000)
      }
      throw new ApiClientError('UNAUTHORIZED', 'Token expired')
    }

    let json: (ApiResponse<T> & { detail?: unknown }) | null = null
    let responseText: string | null = null
    try {
      json = await res.json()
    } catch {
      try {
        responseText = await res.text()
      } catch { /* ignore */ }
      json = null
    }

    if (!res.ok) {
      const detail = json?.detail ?? json?.error?.details
      let message =
        json?.error?.message ??
        (typeof json?.detail === 'string' ? json.detail : undefined) ??
        res.statusText
      if (res.status === 422 && Array.isArray(json?.detail)) {
        const validationErrors = json.detail
          .map((e: { loc?: string[]; msg?: string; type?: string }) => {
            const field = e.loc?.slice(-1)[0] ?? ''
            return field ? `${field}: ${e.msg ?? 'validation error'}` : (e.msg ?? 'validation error')
          })
          .join('; ')
        message = validationErrors || message
      }
      if (!json && responseText) {
        message = `${res.status} ${res.statusText}: ${responseText.slice(0, 200)}`
      }
      throw new ApiClientError(json?.error?.code ?? String(res.status), message, detail)
    }

    if (json?.error) {
      throw new ApiClientError(json.error.code, json.error.message, json.error.details)
    }

    if (json?.data === undefined) {
      if (res.status === 204 || res.headers.get('content-length') === '0') {
        return undefined as unknown as T
      }
      throw new ApiClientError('EMPTY_RESPONSE', 'Server returned an empty response')
    }

    return json.data as T
  }

  get<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
    return this.request<T>('GET', path, undefined, params)
  }

  post<T>(path: string, body?: unknown, extraHeaders?: Record<string, string>) {
    return this.request<T>('POST', path, body, undefined, false, extraHeaders)
  }

  put<T>(path: string, body?: unknown) {
    return this.request<T>('PUT', path, body)
  }

  delete<T>(path: string, body?: unknown) {
    return this.request<T>('DELETE', path, body)
  }

  deleteWithParams<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
    return this.request<T>('DELETE', path, undefined, params)
  }

  getAuthHeaders(): Record<string, string> {
    const token = this.getToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }
}

export class ApiClientError extends Error {
  constructor(
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

const api = new HttpClient(API_BASE)

// ─── Auth ──────────────────────────────────────────────────────────

export const authApi = {
  login: (username: string, password: string) =>
    api.post<{ token: string; user: User; is_first_login?: boolean }>('/auth/login', { username, password }),
  register: (username: string, password: string, displayName?: string) =>
    api.post<{ user: User }>('/auth/register', { username, password, display_name: displayName }),
  me: () => api.get<User>('/auth/me'),
  refresh: () => api.post<{ token: string }>('/auth/refresh'),
  ssoCookieToken: () => api.get<{ token: string; user: User; is_first_login?: boolean }>('/auth/sso/cookie-token'),
  wsTicket: () => api.post<{ ticket: string }>('/auth/ws-ticket'),
}

// ─── Admin Users ───────────────────────────────────────────────────

export const adminUsersApi = {
  list: (params?: { search?: string; status?: string; role?: string; page?: number; page_size?: number }) =>
    api.get<{ items: User[]; total: number; page: number; page_size: number }>('/admin/users', params as Record<string, string | number | boolean | undefined>),
  create: (data: { username: string; password: string; display_name?: string; email?: string; status?: string }) =>
    api.post<User>('/admin/users', data),
  get: (id: number) => api.get<User>(`/admin/users/${id}`),
  update: (id: number, data: { display_name?: string; email?: string; status?: string }) =>
    api.put<User>(`/admin/users/${id}`, data),
  resetPassword: (id: number, newPassword: string) =>
    api.post<unknown>(`/admin/users/${id}/reset-password`, { new_password: newPassword }),
  deactivate: (id: number) => api.post<unknown>(`/admin/users/${id}/deactivate`),
  delete: (id: number) => api.delete<unknown>(`/admin/users/${id}`),
  assignRole: (id: number, data: { role_id: number; project_id?: number; expires_at?: string }) =>
    api.post<unknown>(`/admin/users/${id}/roles`, data),
  removeRole: (userId: number, roleId: number, projectId?: number | null) =>
    api.deleteWithParams<unknown>(`/admin/users/${userId}/roles/${roleId}`, { project_id: projectId ?? undefined }),
}

// ─── Admin Roles ───────────────────────────────────────────────────

export const adminRolesApi = {
  list: (scope?: string) => api.get<{ roles: Role[]; total: number }>('/admin/roles', { scope }),
  create: (data: { name: string; scope?: string; description?: string; permissions?: number[] }) =>
    api.post<Role>('/admin/roles', data),
  get: (id: number) => api.get<Role>(`/admin/roles/${id}`),
  update: (id: number, data: { name?: string; description?: string; permissions?: number[] }) =>
    api.put<Role>(`/admin/roles/${id}`, data),
  delete: (id: number) => api.delete<unknown>(`/admin/roles/${id}`),
  updatePermissions: (id: number, permissionIds: number[]) =>
    api.put<{ success: boolean }>(`/admin/roles/${id}/permissions`, { permission_ids: permissionIds }),
}

// ─── Admin Permissions ─────────────────────────────────────────────

export const adminPermissionsApi = {
  list: () => api.get<Permission[]>('/admin/permissions'),
}

// ─── Admin Audit Logs ──────────────────────────────────────────────

export const adminAuditLogsApi = {
  list: (params?: { event_type?: string; user_id?: number; from?: string; to?: string; page?: number; page_size?: number }) =>
    api.get<{ items: AuditLog[]; total: number; page: number; page_size: number }>('/admin/audit-logs', params as Record<string, string | number | boolean | undefined>),
  export: (format: 'csv' | 'json') => api.post<AuditLog[]>('/admin/audit-logs/export', { format }),
}

export const apiHistoryApi = {
  list: (params?: { search?: string; method?: string; status_code?: number; page?: number; page_size?: number }) =>
    api.get<{ items: unknown[]; total: number; page: number; page_size: number }>('/api-history', params as Record<string, string | number | boolean | undefined>),
}

// ─── Admin SSO ─────────────────────────────────────────────────────

export const adminSSOApi = {
  get: () => api.get<unknown>('/admin/sso'),
  update: (data: { provider?: string; client_id?: string; client_secret?: string; issuer_url?: string; mapping_rules?: Record<string, unknown>; enabled?: boolean }) =>
    api.put<unknown>('/admin/sso', data),
}

// ─── Admin Security Policies ───────────────────────────────────────

export const adminSecurityPoliciesApi = {
  rls: {
    list: (params?: { project_id?: number; role_id?: number }) =>
      api.get<RowSecurityPolicy[]>('/admin/security-policies/rls', params as Record<string, string | number | boolean | undefined>),
    create: (data: Omit<RowSecurityPolicy, 'id' | 'created_at' | 'filter_expression'>) =>
      api.post<RowSecurityPolicy>('/admin/security-policies/rls', data),
    update: (id: number, data: Partial<Omit<RowSecurityPolicy, 'id' | 'created_at'>>) =>
      api.put<RowSecurityPolicy>(`/admin/security-policies/rls/${id}`, data),
    delete: (id: number) => api.delete<{ success: boolean }>(`/admin/security-policies/rls/${id}`),
  },
  cls: {
    list: (params?: { project_id?: number; role_id?: number }) =>
      api.get<ColumnSecurityPolicy[]>('/admin/security-policies/cls', params as Record<string, string | number | boolean | undefined>),
    create: (data: Omit<ColumnSecurityPolicy, 'id' | 'created_at'>) =>
      api.post<ColumnSecurityPolicy>('/admin/security-policies/cls', data),
    update: (id: number, data: Partial<Omit<ColumnSecurityPolicy, 'id' | 'created_at'>>) =>
      api.put<ColumnSecurityPolicy>(`/admin/security-policies/cls/${id}`, data),
    delete: (id: number) => api.delete<{ success: boolean }>(`/admin/security-policies/cls/${id}`),
  },
}

export const adminBackupApi = {
  list: () => api.get<BackupEntry[]>('/admin/backups'),
  create: () => api.post<BackupEntry>('/admin/backups'),
  get: (name: string) => api.get<BackupEntry>(`/admin/backups/${name}`),
  downloadUrl: (name: string) => `/api/admin/backups/${name}/download`,
  restore: (name: string) => api.post<{ success: boolean; error?: string }>('/admin/backups/restore', { name }),
  delete: (name: string) => api.delete<{ success: boolean }>(`/admin/backups/${name}`),
}

// ─── Profile ───────────────────────────────────────────────────────

export const profileApi = {
  get: () => api.get<{ id: number; username: string; display_name?: string; email?: string }>('/profile'),
  update: (data: { display_name?: string; email?: string }) =>
    api.put<unknown>('/profile', data),
  changePassword: (oldPassword: string, newPassword: string) =>
    api.post<unknown>('/profile/change-password', { old_password: oldPassword, new_password: newPassword }),
  tokens: {
    list: () => api.get<unknown[]>('/profile/tokens'),
    create: (data: { name: string; expires_at?: string; scope?: string[] }) =>
      api.post<{ id: number; token: string; name: string }>('/profile/tokens', data),
    revoke: (id: number) => api.post<unknown>(`/profile/tokens/${id}/revoke`),
  },
  sessions: {
    list: () => api.get<unknown[]>('/profile/sessions'),
    revoke: (id: string) => api.post<unknown>(`/profile/sessions/${id}/revoke`),
  },
}

// ─── Projects ──────────────────────────────────────────────────────

export const projectsApi = {
  list: () => api.get<{ items: unknown[]; total: number; page: number; page_size: number }>('/projects'),
  create: (data: { name: string; display_name?: string; description?: string; prompt?: string; type?: string; connection_info?: Record<string, unknown> }, idempotencyKey?: string) =>
    api.post<{ id: number }>('/projects', data, idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined),
  get: (id: number) => api.get<unknown>(`/projects/${id}`),
  update: (id: number, data: { name?: string; display_name?: string; description?: string; prompt?: string; connection_info?: Record<string, unknown>; language?: string }) =>
    api.put<unknown>(`/projects/${id}`, data),
  delete: (id: number) => api.delete<unknown>(`/projects/${id}`),
  switch: (id: number) => api.post<{ success: boolean }>(`/projects/${id}/switch`),
  members: {
    list: (projectId: number) => api.get<unknown[]>(`/projects/${projectId}/members`),
    add: (projectId: number, data: { user_id: number; role_id: number; expires_at?: string }) =>
      api.post<unknown>(`/projects/${projectId}/members`, data),
    update: (projectId: number, memberId: number, data: { role_id: number }) =>
      api.put<unknown>(`/projects/${projectId}/members/${memberId}`, data),
    remove: (projectId: number, memberId: number) =>
      api.delete<unknown>(`/projects/${projectId}/members/${memberId}`),
  },
  datasources: {
    list: (projectId: number) => api.get<unknown[]>(`/projects/${projectId}/datasources`),
    bind: (projectId: number, data: { datasource_id: number; alias?: string }) =>
      api.post<{ bindingId: number }>(`/projects/${projectId}/datasources`, data),
    unbind: (projectId: number, bindingId: number) =>
      api.delete<unknown>(`/projects/${projectId}/datasources/${bindingId}`),
    register: (projectId: number, data: { name: string; type: string; properties: Record<string, unknown> }) =>
      api.post<{ id: number; bindingId: number }>(`/projects/${projectId}/datasources/register`, data),
    tables: (projectId: number, bindingId: number) =>
      api.get<{
        tables: string[]
        table_details?: {
          name: string
          schema?: string | null
          reference?: string
          table_type?: string | null
          description?: string | null
          display_name?: string | null
          columns?: { name: string; type: string; is_primary_key?: boolean; display_name?: string | null; description?: string | null }[]
        }[]
        warning?: string
      }>(`/projects/${projectId}/datasources/${bindingId}/tables`),
    sync: (projectId: number, bindingId: number) =>
      api.post<{ tables_discovered: number; tables_removed: number }>(`/projects/${projectId}/datasources/${bindingId}/sync`),
  },
  exportProject: (projectId: number, format: 'yaml' | 'json' = 'yaml') => {
    const url = `/api/projects/${projectId}/export?format=${format}`
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
    const timeoutMs = getRequestTimeout() * 3 || 120000
    const timeoutToken = '__PRISMBI_EXPORT_TIMEOUT__'
    let timeout: ReturnType<typeof setTimeout> | null = null

    const fetchPromise = fetch(url, {
      headers: api.getAuthHeaders(),
      signal: controller?.signal,
    })
    const timeoutPromise = new Promise<Response>((_, reject) => {
      timeout = setTimeout(() => {
        controller?.abort()
        reject(new Error(timeoutToken))
      }, timeoutMs)
    })

    return Promise.race([fetchPromise, timeoutPromise]).then((res) => {
      if (timeout) clearTimeout(timeout)
      if (res.status === 401) {
        if (typeof window !== 'undefined' && !_isRedirecting) {
          _isRedirecting = true
          localStorage.removeItem('auth-store')
          window.location.href = '/login'
          setTimeout(() => { _isRedirecting = false }, 5000)
        }
        throw new Error('Authentication required')
      }
      if (res.ok && (res.status === 204 || res.headers.get('content-length') === '0')) return null
      if (!res.ok) throw new Error(`Export failed: ${res.status}`)
      return res.blob()
    }).catch((err) => {
      if (timeout) clearTimeout(timeout)
      if (err instanceof Error && err.message === timeoutToken) throw new Error('Export timed out')
      if ((err as { name?: string })?.name === 'AbortError') throw new Error('Export timed out')
      throw err
    })
  },
  importProject: (file: File, format: 'yaml' | 'json' = 'yaml') => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post<{ project: Record<string, unknown>; project_id: number; stats: Record<string, number | boolean> }>(
      `/projects/import/file?format=${format}`,
      formData,
    )
  },
  migrateFromSqlite: (file: File, defaultUserId?: number) => {
    const formData = new FormData()
    formData.append('file', file)
    const userId = defaultUserId ?? 1
    return api.post<Record<string, unknown>>(`/projects/migrate/sqlite?default_user_id=${userId}`, formData)
  },
}

// ─── Query ─────────────────────────────────────────────────────────

export const queryApi = {
  execute: (sql: string, projectId: number, limit?: number, dryRun?: boolean) =>
    api.post<QueryResult>('/query', { sql, project_id: projectId, limit, dry_run: dryRun }),
  dryPlan: (sql: string, projectId: number) =>
    api.post<{ planned_sql?: string; model_refs: string[] }>('/query/dry-plan', { sql, project_id: projectId }),
  metrics: async (projectId: number) => {
    const data = await api.get<QueryExecutionMetrics | QueryExecutionMetricsWithRouteDimensions>('/query/metrics', {
      project_id: projectId,
    })
    if (data && typeof data === 'object' && 'by_datasource' in data) {
      return (data as QueryExecutionMetricsWithRouteDimensions).by_datasource
    }
    return data as QueryExecutionMetrics
  },
  metricsWithRouteDimensions: (projectId: number) =>
    api.get<QueryExecutionMetricsWithRouteDimensions>('/query/metrics', {
      project_id: projectId,
      include_route_dimensions: true,
    }),
}

// ─── Ask / NL→SQL ─────────────────────────────────────────────────

export const askApi = {
  ask: (
    question: string,
    threadId?: number,
    previousQuestions?: string[],
    previousAnswers?: string[],
    language?: string,
    previewRowLimit?: number,
    temporary?: boolean,
    clientRequestId?: string,
  ) =>
    api.post<{ sql?: string; summary?: string; thread_id?: number; response?: ThreadResponse }>('/ask', {
      question,
      thread_id: threadId,
      previous_questions: previousQuestions,
      previous_answers: previousAnswers,
      language,
      preview_row_limit: previewRowLimit,
      temporary,
      client_request_id: clientRequestId,
    }),
}

// ─── Threads ───────────────────────────────────────────────────────

export const threadsApi = {
  create: (projectId?: number, summary?: string, previewRowLimit?: number) =>
    api.post<{ id: number; preview_row_limit?: number }>('/threads', { project_id: projectId, summary, preview_row_limit: previewRowLimit }),
  list: (params?: { project_id?: number; page?: number; page_size?: number }) =>
    api.get<{ items: unknown[]; total: number; page: number; page_size: number }>('/threads', params as Record<string, string | number | boolean | undefined>),
  get: (id: number) => api.get<ThreadDetail>(`/threads/${id}`),
  update: (id: number, data: { summary?: string }) => api.put<ThreadDetail>(`/threads/${id}`, data),
  delete: (id: number) => api.delete<unknown>(`/threads/${id}`),
  responses: {
    create: (threadId: number, data: { question: string; sql?: string }) =>
      api.post<ThreadResponse>(`/threads/${threadId}/responses`, data),
    list: (threadId: number) => api.get<ThreadResponse[]>(`/threads/${threadId}/responses`),
  },
  cleanupResponses: (before?: string, projectId?: number) =>
    api.deleteWithParams<unknown>('/responses', { before, project_id: projectId }),
  cleanupHistory: (before?: string, statusCode?: number) =>
    api.deleteWithParams<unknown>('/history', { before, status_code: statusCode }),
}

// ─── Modeling ──────────────────────────────────────────────────────

export interface ApiModelDef {
  id: number
  project_id: number
  name: string
  display_name?: string
  table_reference?: string
  model_type?: string
  source_binding_id?: number
  description?: string
  column_defs?: { name: string; type: string; is_primary_key?: boolean; expression?: string; display_name?: string; description?: string }[]
  fields?: { name: string; type: string; primaryKey?: boolean; display_name?: string; description?: string }[]
  relation_defs?: { id: number; name: string; display_name?: string; source_model_id: number; target_model_id: number; relation_type: string; description?: string }[]
  created_at?: string
  updated_at?: string
}

export interface ApiViewDef {
  id: number
  project_id: number
  name: string
  display_name?: string
  model_id?: number
  description?: string
  sql?: string
  source_response_id?: number
  column_defs?: { name: string; type: string; display_name?: string; description?: string }[]
  fields?: { name: string; type: string; display_name?: string; description?: string }[]
  created_at?: string
  updated_at?: string
}

export interface ApiRelationDef {
  id: number
  project_id: number
  name?: string
  source_model_id: number
  source_column: string
  target_model_id: number
  target_column: string
  relation_type?: string
  /** @deprecated Use relation_type instead */
  type?: string
  description?: string | null
  created_at?: string
}

export interface ApiCalculatedFieldDef {
  id: number
  project_id: number
  name: string
  display_name?: string
  model_id: number
  expression: string
  result_type?: string
}

export const modelingApi = {
  diagram: (projectId?: number) =>
    projectId
      ? api.get<{ models: ApiModelDef[]; views: ApiViewDef[]; relations: ApiRelationDef[]; calculated_fields: ApiCalculatedFieldDef[] }>(`/modeling/${projectId}/diagram`)
      : Promise.resolve<{ models: ApiModelDef[]; views: ApiViewDef[]; relations: ApiRelationDef[]; calculated_fields: ApiCalculatedFieldDef[] }>({ models: [], views: [], relations: [], calculated_fields: [] }),
  models: {
    list: (projectId?: number) =>
      projectId ? api.get<ApiModelDef[]>(`/modeling/${projectId}/models`) : Promise.resolve([]),
    get: (projectId: number, id: number) => api.get<ApiModelDef>(`/modeling/${projectId}/models/${id}`),
    create: (projectId: number, data: {
      name: string
      display_name?: string
      description?: string
      table_reference?: string
      model_type?: string
      source_binding_id?: number
      columns?: unknown[]
    }) =>
      api.post<{ id: number }>(`/modeling/${projectId}/models`, data),
    update: (projectId: number, id: number, data: { name?: string; display_name?: string; description?: string; model_type?: string; source_binding_id?: number; columns?: unknown[] }) =>
      api.put<ApiModelDef>(`/modeling/${projectId}/models/${id}`, data),
    delete: (projectId: number, id: number) => api.delete<unknown>(`/modeling/${projectId}/models/${id}`),
  },
  views: {
    list: (projectId?: number) =>
      projectId ? api.get<ApiViewDef[]>(`/modeling/${projectId}/views`) : Promise.resolve([]),
    create: (projectId: number, data: { name: string; display_name?: string; description?: string; model_id?: number; columns?: unknown[]; sql?: string; source_response_id?: number }) =>
      api.post<{ id: number }>(`/modeling/${projectId}/views`, data),
    update: (projectId: number, id: number, data: { name?: string; display_name?: string; description?: string; columns?: unknown[] }) =>
      api.put<ApiViewDef>(`/modeling/${projectId}/views/${id}`, data),
    delete: (projectId: number, id: number) => api.delete<unknown>(`/modeling/${projectId}/views/${id}`),
  },
  relations: {
    list: (projectId?: number) =>
      projectId ? api.get<ApiRelationDef[]>(`/modeling/${projectId}/relations`) : Promise.resolve([]),
    create: (projectId: number, data: {
      name: string
      description?: string
      source_model_id: number
      source_column: string
      target_model_id: number
      target_column: string
      relation_type?: string
    }) =>
      api.post<{ id: number }>(`/modeling/${projectId}/relations`, data),
    update: (projectId: number, id: number, data: {
      name?: string
      description?: string
      source_column?: string
      target_column?: string
      relation_type?: string
    }) =>
      api.put<ApiRelationDef>(`/modeling/${projectId}/relations/${id}`, data),
    delete: (projectId: number, id: number) => api.delete<unknown>(`/modeling/${projectId}/relations/${id}`),
  },
  calculatedFields: {
    list: (projectId?: number) =>
      projectId ? api.get<ApiCalculatedFieldDef[]>(`/modeling/${projectId}/calculated-fields`) : Promise.resolve([]),
    create: (projectId: number, data: { name: string; model_id: number; expression: string; result_type?: string; display_name?: string; description?: string }) =>
      api.post<{ id: number }>(`/modeling/${projectId}/calculated-fields`, data),
    update: (projectId: number, id: number, data: { name?: string; expression?: string; display_name?: string; description?: string; result_type?: string }) =>
      api.put<ApiCalculatedFieldDef>(`/modeling/${projectId}/calculated-fields/${id}`, data),
    delete: (projectId: number, id: number) => api.delete<unknown>(`/modeling/${projectId}/calculated-fields/${id}`),
  },
  bindingStatus: (projectId: number) =>
    api.get<{
      project_id: number
      total_models: number
      bound_models: number
      unbound_models: number
      valid_bindings: number
      models: {
        bound: Array<{ id: number; name: string; display_name?: string; table_reference?: string; source_binding_id: number; status: string }>
        unbound: Array<{ id: number; name: string; display_name?: string; table_reference?: string; source_binding_id?: number; status: string; issue: string }>
      }
    }>(`/modeling/${projectId}/models/binding-status`),
}

// ─── Dashboard ─────────────────────────────────────────────────────

export const dashboardApi = {
  list: (params?: { project_id?: number }) => api.get<unknown[]>('/dashboards', params as Record<string, string | number | boolean | undefined>),
  get: (id: number) => api.get<unknown>(`/dashboards/${id}`),
  create: (data: { name: string; project_id: number; cache_enabled?: boolean; schedule_frequency?: string; schedule_timezone?: string; schedule_cron?: string }) =>
    api.post<{ id: number }>('/dashboards', data),
  update: (id: number, data: { name?: string; cache_enabled?: boolean; schedule_frequency?: string; schedule_timezone?: string; schedule_cron?: string }) =>
    api.put<unknown>(`/dashboards/${id}`, data),
  delete: (id: number) => api.delete<unknown>(`/dashboards/${id}`),
  items: {
    create: (data: { dashboard_id: number; title?: string; chart_config?: Record<string, unknown>; data_source?: string; response_id?: number; type?: string }) =>
      api.post<{ id: number }>(`/dashboards/${data.dashboard_id}/items`, { display_name: data.title, chart_config: data.chart_config, data_source: data.data_source, response_id: data.response_id, type: data.type ?? 'CHART' }),
    update: (dashboardId: number, id: number, data: { title?: string; chart_config?: Record<string, unknown>; data_source?: string }) =>
      api.put<unknown>(`/dashboards/${dashboardId}/items/${id}`, { display_name: data.title, chart_config: data.chart_config, data_source: data.data_source }),
    delete: (dashboardId: number, id: number) => api.delete<unknown>(`/dashboards/${dashboardId}/items/${id}`),
    updateLayouts: (layouts: { item_id: number; x: number; y: number; w: number; h: number }[]) =>
      api.put<unknown>('/dashboards/items/layouts', { layouts }),
    preview: (id: number) => api.post<{ columns: string[]; rows: Record<string, unknown>[] }>(`/dashboards/items/${id}/preview`),
  },
  schedule: (id: number, data: { frequency: string; timezone?: string; cron?: string }) =>
    api.post<{ success: boolean }>(`/dashboards/${id}/schedule`, data),
}

// ─── Knowledge ─────────────────────────────────────────────────────

export const knowledgeApi = {
  instructions: {
    list: (params?: { project_id?: number; search?: string; sort?: string; page?: number; page_size?: number }) =>
      api.get<{ items: unknown[]; total: number }>('/knowledge/instructions', params as Record<string, string | number | boolean | undefined>),
    create: (data: { project_id: number; text: string; category?: string; scope?: string; priority?: number }) =>
      api.post<{ id: number }>('/knowledge/instructions', data),
    update: (id: number, data: { text?: string; category?: string; scope?: string; priority?: number }) =>
      api.put<unknown>(`/knowledge/instructions/${id}`, data),
    delete: (id: number) => api.delete<unknown>(`/knowledge/instructions/${id}`),
  },
  sqlPairs: {
    list: (params?: { project_id?: number; search?: string; sort?: string; page?: number; page_size?: number }) =>
      api.get<{ items: unknown[]; total: number }>('/knowledge/sql-pairs', params as Record<string, string | number | boolean | undefined>),
    create: (data: { project_id: number; question: string; sql: string; description?: string; category?: string; scope?: string }) =>
      api.post<{ id: number }>('/knowledge/sql-pairs', data),
    update: (id: number, data: { question?: string; sql?: string; description?: string; category?: string; scope?: string }) =>
      api.put<unknown>(`/knowledge/sql-pairs/${id}`, data),
    delete: (id: number) => api.delete<unknown>(`/knowledge/sql-pairs/${id}`),
  },
}

// ─── Recommendations ───────────────────────────────────────────────

export interface RecommendationRouteSignals {
  available: boolean
  project_id: number | null
  dominant_route_kind: string
  mixed_ratio: number
  avg_metadata_clause_count: number
  sql_success_rate: number
  route_kind_counts: Record<string, number>
}

export interface RecommendationStatistics {
  total_catalogs: number
  total_hints: number
  top_queries: unknown[]
  layer_performance?: Record<string, unknown>
  score_distribution?: Record<string, unknown>
  route_signals?: RecommendationRouteSignals
  weight_history: unknown[]
}

export interface RecommendationBootstrapStatus {
  project_id: number
  status: string
  is_bootstrapping: boolean
  ready: boolean
  recommendation_count: number
  active_recommendations: number
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string | null
}

export const recommendationsApi = {
  list: (params?: { project_id?: number; context?: string; max_results?: number; types?: string; language?: string; include_generated?: boolean; refresh_generated?: boolean }) => {
    const { project_id, ...rest } = params || {}
    if (!project_id) return Promise.resolve({ recommendations: [] })
    return api.get<{ id: number; title: string; description?: string; category?: string; scope?: string; source_type?: string; confidence?: number; metadata?: Record<string, unknown> | string }[]>(
      `/recommendations/${project_id}`,
      rest as Record<string, string | number | boolean | undefined>,
    ).then(
      (rows) => ({
        recommendations: (rows || []).map((r) => ({
          id: r.id,
          question: r.title,
          type: r.category || 'aggregation',
          source: r.source_type || r.scope || '',
          score: r.confidence || 0,
          model_names: (typeof r.metadata === 'object' && r.metadata && (r.metadata as Record<string, unknown>).model_names) as string[] | undefined,
        })),
      }),
    )
  },
  bootstrapStatus: (projectId: number) =>
    api.get<RecommendationBootstrapStatus>(`/recommendations/${projectId}/bootstrap-status`),
  onboarding: (params?: { project_id?: number; max_results?: number; language?: string }) =>
    params?.project_id
      ? api.get<{ id: number; title: string; description?: string; category?: string; scope?: string; source_type?: string; confidence?: number; metadata?: Record<string, unknown> | string }[]>(
          `/recommendations/${params.project_id}/onboarding`,
          { max_results: params.max_results, language: params.language },
        ).then(
          (rows) => ({
            questions: (rows || []).map((r) => ({
              question: r.title,
              category: r.category || '',
              model_names: (typeof r.metadata === 'object' && r.metadata && (r.metadata as Record<string, unknown>).model_names) as string[] | undefined,
            })),
          }),
        )
      : Promise.resolve({ questions: [] as { question: string; category: string; model_names?: string[] }[] }),
  sampleQuestions: (params: { project_id: number; language?: string }) =>
    api.get<{ question: string; label: string }[]>(`/recommendations/${params.project_id}/sample-questions`, { language: params.language }).then(
      (rows) => ({ questions: rows || [] }),
    ).catch(() => ({ questions: [] as { question: string; label: string }[] })),
  catalog: {
    list: (params?: { project_id?: number; search?: string; sort?: string }) =>
      params?.project_id
        ? api.get<unknown[]>(`/recommendations/${params.project_id}/catalog`, { search: params.search, sort: params.sort }).then((entries) => ({ entries }))
        : Promise.resolve({ entries: [] }),
    create: (projectId: number, data: { question: string; sql: string; metadata?: Record<string, unknown> }) =>
      api.post<{ id: number }>(`/recommendations/${projectId}/catalog`, data),
    update: (projectId: number, id: number, data: { question?: string; sql?: string; metadata?: Record<string, unknown> }) =>
      api.put<{ success: boolean }>(`/recommendations/${projectId}/catalog/${id}`, data),
    delete: (projectId: number, id: number) =>
      api.delete<{ success: boolean }>(`/recommendations/${projectId}/catalog/${id}`),
  },
  hints: {
    list: (projectId?: number) =>
      projectId
        ? api.get<unknown[]>(`/recommendations/${projectId}/hints`).then((hints) => ({ hints }))
        : Promise.resolve({ hints: [] }),
    create: (projectId: number, data: { hint_text: string; source_query?: string; confidence?: number }) =>
      api.post<{ id: number }>(`/recommendations/${projectId}/hints`, data),
    update: (projectId: number, id: number, data: { hint_text?: string; source_query?: string; confidence?: number }) =>
      api.put<{ success: boolean }>(`/recommendations/${projectId}/hints/${id}`, data),
    delete: (projectId: number, id: number) =>
      api.delete<{ success: boolean }>(`/recommendations/${projectId}/hints/${id}`),
  },
  feedback: (data: { recommendation_id: number; action: 'accept' | 'dismiss'; context?: number | string }) =>
    data.context != null
      ? data.action === 'accept'
        ? api.post<{ success: boolean }>(`/recommendations/${data.context}/accept/${data.recommendation_id}`)
        : api.post<{ success: boolean }>(`/recommendations/${data.context}/dismiss/${data.recommendation_id}`)
      : Promise.resolve({ success: false }),
  rate: (data: { recommendation_id: number; score: number; context?: number | string }) =>
    data.context != null ? api.post<{ id: number }>(`/recommendations/${data.context}/rate/${data.recommendation_id}`, { rating: data.score }).then(() => ({ success: true })) : Promise.resolve({ success: false }),
  ratings: (params?: { project_id?: number; source_layer?: string; from?: string; to?: string }) =>
    params?.project_id
      ? api.get<unknown[]>(`/recommendations/${params.project_id}/scores`, params as Record<string, string | number | boolean | undefined>).then((ratings) => ({ ratings }))
      : Promise.resolve({ ratings: [] }),
  ratingDetail: (id: number) =>
    api.get<{ avg_score: number; total_ratings: number; distribution: Record<string, number> }>(`/recommendations/${id}/rating`),
  statistics: (params?: { project_id?: number }) =>
    api.get<RecommendationStatistics>('/recommendations/statistics', params as Record<string, string | number | boolean | undefined>),
  weightHistory: () => api.get<{ history: { layer: string; weight: number; adjusted_at: string; reason?: string }[] }>('/recommendations/statistics/weight-history'),
  lowScoreAlerts: () => api.get<{ alerts: { source_layer: string; consecutive_low: number; last_score: number; timestamp: string }[] }>('/recommendations/statistics/low-score-alerts'),
}

// ─── System Datasources ────────────────────────────────────────────

export const systemDatasourcesApi = {
  list: () => api.get<unknown[]>('/system/datasources'),
  create: (data: { name: string; type: string; properties: Record<string, unknown> }) =>
    api.post<{ id: number }>('/system/datasources', data),
  update: (id: number, data: { name?: string; properties?: Record<string, unknown> }) =>
    api.put<unknown>(`/system/datasources/${id}`, data),
  delete: (id: number) => api.delete<unknown>(`/system/datasources/${id}`),
  test: (id: number) => api.post<{ success: boolean; latency_ms?: number; error?: string }>(`/system/datasources/${id}/test`),
}

// ─── Settings ──────────────────────────────────────────────────────

export const settingsApi = {
  getAll: () => api.get<{ settings: Record<string, unknown> } & Record<string, unknown>>('/settings'),
  getPublic: () => api.get<{ settings: Record<string, unknown> } & Record<string, unknown>>('/settings/public'),
  branding: (data: { app_name?: string; app_description?: string; logo?: string; icon?: string }) =>
    api.put<{ success: boolean }>('/settings/branding', data),
  theme: (data: { mode?: 'light' | 'dark' | 'system'; primary_color?: string; border_radius?: string; font?: string }) =>
    api.put<{ success: boolean }>('/settings/theme', data),
  llm: (data: { provider?: string; api_key?: string; model?: string; endpoint?: string; max_tokens?: number; temperature?: number; extra_params?: Record<string, unknown>; system_prompt?: string }) =>
    api.put<{ success: boolean }>('/settings/llm', data),
  llmTest: (data: { provider: string; api_key?: string; model: string; endpoint?: string }) =>
    api.post<{ success: boolean; latency_ms?: number; error?: string }>('/settings/llm/test', data),
  llmModels: (data: { provider: string; api_key?: string; endpoint?: string }) =>
    api.post<{ models: string[]; error?: string }>('/settings/llm/models', data),
  llmWhitelist: () =>
    api.get<{ enabled: boolean; prefixes: string[]; defaults: string[] }>('/settings/llm/whitelist'),
  llmWhitelistUpdate: (data: { enabled?: boolean; prefixes?: string[] }) =>
    api.put<{ success: boolean }>('/settings/llm/whitelist', data),
  llmAdvanced: () =>
    api.get<{
      max_retries?: number | null
      retry_base_delay_s?: number | null
      retry_max_delay_s?: number | null
      http_circuit_enabled?: boolean | null
      http_circuit_failure_threshold?: number | null
      http_circuit_open_seconds?: number | null
      chat_history_limit?: number | null
      general_chat_history_limit?: number | null
    }>('/settings/llm/advanced'),
  llmAdvancedUpdate: (data: {
    max_retries?: number
    retry_base_delay_s?: number
    retry_max_delay_s?: number
    http_circuit_enabled?: boolean
    http_circuit_failure_threshold?: number
    http_circuit_open_seconds?: number
    chat_history_limit?: number
    general_chat_history_limit?: number
  }) =>
    api.put<{ success: boolean }>('/settings/llm/advanced', data),
  askSettings: () =>
    api.get<Record<string, unknown>>('/settings/ask'),
  askSettingsUpdate: (data: Record<string, unknown>) =>
    api.put<{ success: boolean }>('/settings/ask', data),
  routerSettings: () =>
    api.get<Record<string, unknown>>('/settings/router'),
  routerSettingsUpdate: (data: Record<string, unknown>) =>
    api.put<{ success: boolean }>('/settings/router', data),
  routerRuntimeReload: () =>
    api.post<{ success: boolean; runtime?: RouterRuntimeSnapshot }>('/settings/router/reload', {}),
  securitySettings: () =>
    api.get<Record<string, unknown>>('/settings/security'),
  securitySettingsUpdate: (data: Record<string, unknown>) =>
    api.put<{ success: boolean }>('/settings/security', data),
  general: (data: {
    language?: string
    default_page?: string
    telemetry?: boolean
    timezone?: string
    date_format?: string
    session_timeout?: number
    route_observability_window_minutes?: number
    request_timeout_ms?: number
    llm_connect_timeout_s?: number
    llm_read_timeout_s?: number
    llm_write_timeout_s?: number
    llm_pool_timeout_s?: number
    db_connect_timeout_s?: number
    model_list_timeout_s?: number
    route_observability_persist_enabled?: boolean
    route_observability_persist_interval_seconds?: number
    route_observability_persist_event_delta?: number
    model_ref_case_sensitive?: boolean
  }) =>
    api.put<{ success: boolean }>('/settings/general', data),
  auditSummary: (params?: {
    from?: string
    to?: string
    scope?: string
    max_events?: number
    latest_limit?: number
    latest_offset?: number
  }) =>
    api.get<SettingsAuditSummary>('/settings/audit-summary', params as Record<string, string | number | boolean | undefined>),
  getTimeouts: () => api.get<Record<string, number | null>>('/settings/timeouts'),
  setTimeouts: (data: Record<string, number>) =>
    api.put<{ success: boolean }>('/settings/timeouts', data),
  appInfo: () => api.get<{ version: string; platforms: string[] }>('/settings/app-info'),
  getRecommendations: () => api.get<Record<string, unknown>>('/settings/recommendations'),
  recommendations: (data: Record<string, unknown>) =>
    api.put<{ success: boolean }>('/settings/recommendations', data),
}

export { api }

export default api
