from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    data: Optional[T] = None
    error: Optional[dict] = None


class ApiError(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int


# ─── Auth ────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    status: str = "ACTIVE"
    default_project_id: Optional[int] = None
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ─── Admin: Users ────────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = None
    email: Optional[str] = None
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"ACTIVE", "INACTIVE", "SUSPENDED"}
        if v.upper() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.upper()


class UpdateUserRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        allowed = {"ACTIVE", "INACTIVE", "SUSPENDED"}
        if v.upper() not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v.upper()


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class AssignRoleRequest(BaseModel):
    role_id: int
    project_id: Optional[int] = None
    expires_at: Optional[datetime] = None


class UserDetailResponse(UserResponse):
    roles: List["RoleResponse"] = []


# ─── Admin: Roles ────────────────────────────────────────────────────


class CreateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: str = "SYSTEM"
    description: Optional[str] = None
    permissions: List[int] = []


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[int]] = None


class RoleResponse(BaseModel):
    id: int
    name: str
    scope: str
    description: Optional[str] = None
    is_system: bool = False
    created_at: Optional[datetime] = None
    permissions: List["PermissionResponse"] = []
    member_count: Optional[int] = None


class RoleListResponse(BaseModel):
    roles: List[RoleResponse]
    total: int


# ─── Admin: Permissions ──────────────────────────────────────────────


class PermissionResponse(BaseModel):
    id: int
    resource: str
    action: str
    description: Optional[str] = None


class BatchUpdatePermissionsRequest(BaseModel):
    permission_ids: List[int]


# ─── Admin: Audit Logs ───────────────────────────────────────────────


class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    event_type: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action: Optional[str] = None
    detail: Optional[Any] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class AuditLogExportRequest(BaseModel):
    format: str = "csv"
    event_type: Optional[str] = None
    user_id: Optional[int] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None


# ─── Admin: SSO ──────────────────────────────────────────────────────


class SSOConfigResponse(BaseModel):
    provider: Optional[str] = None
    client_id: Optional[str] = None
    issuer_url: Optional[str] = None
    mapping_rules: Optional[dict] = None
    enabled: bool = False


class SSOConfigUpdate(BaseModel):
    provider: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    issuer_url: Optional[str] = None
    mapping_rules: Optional[dict] = None
    enabled: Optional[bool] = None


class SSOLoginRequest(BaseModel):
    code: Optional[str] = None
    redirect_uri: Optional[str] = None
    id_token: Optional[str] = None
    nonce: Optional[str] = None


# ─── Profile ─────────────────────────────────────────────────────────


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ApiTokenResponse(BaseModel):
    id: int
    name: str
    token_prefix: str
    scope: List[str] = []
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    is_revoked: bool = False
    created_at: Optional[datetime] = None


class CreateApiTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_at: Optional[datetime] = None
    scope: Optional[List[str]] = None


class CreateApiTokenResponse(BaseModel):
    id: int
    token: str
    name: str


class SessionResponse(BaseModel):
    id: str
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    is_revoked: bool = False


# ─── Projects ────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None
    type: Optional[str] = None
    connection_info: Optional[dict] = None
    language: Optional[str] = "EN"
    sample_dataset: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None
    connection_info: Optional[dict] = None
    language: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None
    type: Optional[str] = None
    connection_info: Optional[dict] = None
    language: str = "EN"
    version: str = "1.0"
    is_current: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    datasource_count: Optional[int] = None
    member_count: Optional[int] = None


class ProjectExportRequest(BaseModel):
    format: str = "yaml"


class ProjectMemberResponse(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    display_name: Optional[str] = None
    role_id: int
    role_name: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AddProjectMemberRequest(BaseModel):
    user_id: int
    role_id: int
    expires_at: Optional[datetime] = None


class UpdateProjectMemberRequest(BaseModel):
    role_id: int


# ─── Query ───────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    sql: str
    project_id: int
    limit: Optional[int] = Field(default=None, ge=1, le=10000)
    dry_run: Optional[bool] = False


class QueryResponse(BaseModel):
    columns: List[str] = []
    rows: List[dict] = []
    total_rows: int = 0
    execution_time_ms: Optional[float] = None


class DryPlanRequest(BaseModel):
    sql: str
    project_id: int


class DryPlanResponse(BaseModel):
    planned_sql: Optional[str] = None
    model_refs: List[str] = []


# ─── Security Policies ───────────────────────────────────────────────


class RowSecurityPolicyCreate(BaseModel):
    project_id: int
    role_id: int
    model_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)
    operator: str = "="
    value: Optional[str] = None
    value_source: str = "literal"
    user_attribute: Optional[str] = None
    description: Optional[str] = None
    is_enabled: bool = True


class RowSecurityPolicyUpdate(BaseModel):
    role_id: Optional[int] = None
    model_name: Optional[str] = None
    column_name: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[str] = None
    value_source: Optional[str] = None
    user_attribute: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None


class ColumnSecurityPolicyCreate(BaseModel):
    project_id: int
    role_id: int
    model_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)
    access_type: str = "HIDE"
    mask_with: Optional[str] = None
    is_enabled: bool = True


class ColumnSecurityPolicyUpdate(BaseModel):
    role_id: Optional[int] = None
    model_name: Optional[str] = None
    column_name: Optional[str] = None
    access_type: Optional[str] = None
    mask_with: Optional[str] = None
    is_enabled: Optional[bool] = None


# ─── Threads ─────────────────────────────────────────────────────────


class ThreadCreate(BaseModel):
    project_id: Optional[int] = None
    summary: Optional[str] = None
    preview_row_limit: Optional[int] = None


class ThreadListItem(BaseModel):
    id: int
    project_id: int
    summary: Optional[str] = None
    summary_manual: bool = False
    user_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    response_count: Optional[int] = None


class ThreadDetail(BaseModel):
    id: int
    project_id: int
    summary: Optional[str] = None
    summary_manual: bool = False
    user_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    responses: List["ThreadResponseItem"] = []


class ThreadResponseItem(BaseModel):
    id: int
    thread_id: int
    user_id: Optional[int] = None
    question: str
    sql: Optional[str] = None
    asking_task_id: Optional[str] = None
    breakdown_detail: Optional[Any] = None
    answer_detail: Optional[Any] = None
    chart_detail: Optional[Any] = None
    adjustment: Optional[Any] = None
    created_at: Optional[datetime] = None


class CreateResponseRequest(BaseModel):
    question: str
    sql: Optional[str] = None


class CleanupRequest(BaseModel):
    before: Optional[datetime] = None
    project_id: Optional[int] = None


class CleanupHistoryRequest(BaseModel):
    before: Optional[datetime] = None
    status_code: Optional[int] = None


# ─── Ask / NL → SQL ─────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8000)
    thread_id: Optional[int] = None
    client_request_id: Optional[str] = Field(None, min_length=1, max_length=128)
    previous_questions: Optional[List[str]] = None
    previous_answers: Optional[List[str]] = None
    language: Optional[str] = None
    preview_row_limit: Optional[int] = Field(None, ge=1, le=1000)
    temporary: Optional[bool] = False


class AskResponse(BaseModel):
    sql: Optional[str] = None
    summary: Optional[str] = None
    thread_id: Optional[int] = None


# ─── Modeling ────────────────────────────────────────────────────────


class DiagramResponse(BaseModel):
    models: List["ModelResponse"] = []
    views: List["ViewResponse"] = []
    relations: List["RelationResponse"] = []
    calculated_fields: List["CalculatedFieldResponse"] = []


class ColumnDef(BaseModel):
    name: str
    type: str
    is_primary_key: bool = False
    expression: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = None
    description: Optional[str] = None
    table_reference: Optional[str] = None
    model_type: Optional[str] = None
    source_binding_id: Optional[int] = None
    columns: List[ColumnDef] = []


class ModelUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    table_reference: Optional[str] = None
    model_type: Optional[str] = None
    source_binding_id: Optional[int] = None
    columns: Optional[List[ColumnDef]] = None


class ModelResponse(BaseModel):
    id: int
    project_id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    table_reference: Optional[str] = None
    model_type: Optional[str] = None
    source_binding_id: Optional[int] = None
    column_defs: List[ColumnDef] = []
    fields: List[dict] = []
    relation_defs: Optional[List[dict]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = None
    description: Optional[str] = None
    model_id: Optional[int] = None
    columns: Optional[List[ColumnDef]] = None
    sql: Optional[str] = None
    source_response_id: Optional[int] = None


class ViewUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    columns: Optional[List[ColumnDef]] = None
    sql: Optional[str] = None


class ViewResponse(BaseModel):
    id: int
    project_id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    model_id: Optional[int] = None
    column_defs: Optional[List[ColumnDef]] = None
    fields: List[dict] = []
    sql: Optional[str] = None
    source_response_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RelationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None
    source_model_id: int
    source_column: str
    target_model_id: int
    target_column: str
    relation_type: str = "MANY_TO_ONE"


class RelationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_column: Optional[str] = None
    target_column: Optional[str] = None
    relation_type: Optional[str] = None


class RelationResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: Optional[str] = None
    source_model_id: int
    source_column: str
    target_model_id: int
    target_column: str
    relation_type: str = "MANY_TO_ONE"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CalculatedFieldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = None
    description: Optional[str] = None
    model_id: int
    expression: str
    result_type: Optional[str] = None

    @field_validator('result_type', mode='before')
    @classmethod
    def normalize_result_type(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


class CalculatedFieldUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    expression: Optional[str] = None
    result_type: Optional[str] = None


class CalculatedFieldResponse(BaseModel):
    id: int
    project_id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    model_id: int
    expression: str
    result_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ─── Dashboard ───────────────────────────────────────────────────────


class DashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    project_id: int
    cache_enabled: Optional[bool] = False
    schedule_frequency: Optional[str] = None
    schedule_timezone: Optional[str] = "UTC"
    schedule_cron: Optional[str] = None


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    cache_enabled: Optional[bool] = None
    schedule_frequency: Optional[str] = None
    schedule_timezone: Optional[str] = None
    schedule_cron: Optional[str] = None


class DashboardResponse(BaseModel):
    id: int
    project_id: int
    name: str
    cache_enabled: bool = False
    schedule_frequency: Optional[str] = None
    schedule_timezone: Optional[str] = "UTC"
    schedule_cron: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List["DashboardItemResponse"] = []


class DashboardListItem(BaseModel):
    id: int
    project_id: int
    name: str
    cache_enabled: bool = False
    created_at: Optional[datetime] = None
    item_count: Optional[int] = None


class DashboardItemCreate(BaseModel):
    title: Optional[str] = None
    display_name: Optional[str] = None
    chart_config: Optional[dict] = None
    data_source: Optional[str] = None
    type: str = "TABLE"
    response_id: Optional[int] = None


class DashboardItemUpdate(BaseModel):
    title: Optional[str] = None
    display_name: Optional[str] = None
    chart_config: Optional[dict] = None
    data_source: Optional[str] = None
    type: Optional[str] = None


class DashboardItemResponse(BaseModel):
    id: int
    dashboard_id: int
    type: str
    display_name: Optional[str] = None
    response_id: Optional[int] = None
    chart_config: Optional[dict] = None
    layout_x: int = 0
    layout_y: int = 0
    layout_w: int = 3
    layout_h: int = 2
    cache_data: Optional[Any] = None
    cache_created_at: Optional[datetime] = None


class DashboardItemLayout(BaseModel):
    item_id: int
    x: int
    y: int
    w: int
    h: int


class DashboardItemLayoutsUpdate(BaseModel):
    layouts: List[DashboardItemLayout]


class DashboardScheduleRequest(BaseModel):
    frequency: str = Field(..., pattern=r"^(daily|weekly|monthly|hourly|none)$")
    timezone: str = "UTC"
    cron: Optional[str] = Field(None, max_length=100)


class DashboardItemPreviewResponse(BaseModel):
    columns: List[str] = []
    rows: List[dict] = []


# ─── Knowledge ───────────────────────────────────────────────────────


class KnowledgeInstructionCreate(BaseModel):
    project_id: int
    text: str = Field(min_length=1)
    category: Optional[str] = None
    scope: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=100)


class KnowledgeInstructionUpdate(BaseModel):
    text: Optional[str] = None
    category: Optional[str] = None
    scope: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=100)


class KnowledgeInstructionResponse(BaseModel):
    id: int
    project_id: int
    text: str
    category: Optional[str] = None
    scope: Optional[str] = None
    priority: Optional[int] = 0
    questions: Optional[List[str]] = None
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class KnowledgeSqlPairCreate(BaseModel):
    project_id: int
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    description: Optional[str] = None
    category: Optional[str] = None
    scope: Optional[str] = None


class KnowledgeSqlPairUpdate(BaseModel):
    question: Optional[str] = None
    sql: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    scope: Optional[str] = None


class KnowledgeSqlPairResponse(BaseModel):
    id: int
    project_id: int
    question: str
    sql: str
    description: Optional[str] = None
    category: Optional[str] = None
    scope: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ─── Recommendations ─────────────────────────────────────────────────


class RecommendationResponse(BaseModel):
    id: int
    question: str
    type: str
    source: str
    score: float
    llm_explanation: Optional[str] = None
    model_names: Optional[List[str]] = None


class RecommendationListResponse(BaseModel):
    recommendations: List[RecommendationResponse] = []


class OnboardingResponse(BaseModel):
    questions: List[dict] = []


class CatalogEntryResponse(BaseModel):
    id: int
    project_id: int
    question: str
    sql_text: str
    frequency: int = 1
    last_used: Optional[datetime] = None
    metadata: Optional[dict] = None
    verified: bool = False


class CatalogEntryCreate(BaseModel):
    question: str
    sql: str
    metadata: Optional[dict] = None


class CatalogEntryUpdate(BaseModel):
    question: Optional[str] = None
    sql: Optional[str] = None
    metadata: Optional[dict] = None


class HintResponse(BaseModel):
    id: int
    hint_text: str
    source_query: Optional[str] = None
    confidence: float = 1.0
    created_at: Optional[datetime] = None


class HintCreate(BaseModel):
    hint_text: str
    source_query: Optional[str] = None


class FeedbackCreate(BaseModel):
    recommendation_id: int
    action: str = Field(pattern=r"^(accept|dismiss|hover)$")
    context: Optional[str] = None


class RateCreate(BaseModel):
    recommendation_id: int
    score: int = Field(ge=1, le=5)
    context: Optional[str] = None


class RatingResponse(BaseModel):
    id: int
    recommendation_id: Optional[int] = None
    score: int
    source_layer: Optional[str] = None
    recommend_type: Optional[str] = None
    created_at: Optional[datetime] = None


class RatingDetailResponse(BaseModel):
    avg_score: float
    total_ratings: int
    distribution: dict = {}


class RecommendationStatisticsResponse(BaseModel):
    total_catalogs: int = 0
    total_hints: int = 0
    top_queries: List[dict] = []
    layer_performance: Optional[dict] = None
    score_distribution: Optional[dict] = None
    weight_history: List[dict] = []


class WeightHistoryResponse(BaseModel):
    history: List[dict] = []


class LowScoreAlertsResponse(BaseModel):
    alerts: List[dict] = []


class RecommenderSettingsUpdate(BaseModel):
    max_results: Optional[int] = None
    schema_weight: Optional[float] = None
    session_weight: Optional[float] = None
    user_weight: Optional[float] = None
    project_weight: Optional[float] = None
    global_weight: Optional[float] = None
    llm_weight: Optional[float] = None
    novelty_weight: Optional[float] = None
    score_weight: Optional[float] = None
    score_learning_rate: Optional[float] = None
    score_half_life: Optional[int] = None
    low_score_threshold: Optional[int] = None
    consecutive_low_alert: Optional[int] = None
    auto_recover: Optional[bool] = None


# ─── Chart ───────────────────────────────────────────────────────────


class ChartRequest(BaseModel):
    question: Optional[str] = None
    sql: Optional[str] = None
    sample_size: Optional[int] = Field(default=None, ge=1, le=10000)


class ChartResponse(BaseModel):
    vega_spec: Optional[dict] = None
    chart_type: Optional[str] = None


# ─── Memory (LanceDB) ────────────────────────────────────────────────


class MemorySearchResponse(BaseModel):
    results: List[dict] = []


class MemoryStoreRequest(BaseModel):
    type: str
    content: dict
    project_id: Optional[int] = None


class MemoryStoreResponse(BaseModel):
    id: str


class MemoryListResponse(BaseModel):
    items: List[dict] = []


class MemoryForgetRequest(BaseModel):
    id: str


# ─── DataSources ─────────────────────────────────────────────────────


class DatasourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str
    properties: dict
    description: Optional[str] = None


class DatasourceUpdate(BaseModel):
    name: Optional[str] = None
    properties: Optional[dict] = None
    description: Optional[str] = None


class DatasourceResponse(BaseModel):
    id: int
    name: str
    type: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DatasourceBindingCreate(BaseModel):
    datasource_id: int
    alias: Optional[str] = None
    config_overrides: Optional[dict] = None


class ProjectDatasourceUpdate(BaseModel):
    properties: Optional[dict] = None
    alias: Optional[str] = None
    config_overrides: Optional[dict] = None


class ProjectDatasourceResponse(BaseModel):
    id: int
    project_id: int
    datasource_id: int
    alias: Optional[str] = None
    datasource_name: Optional[str] = None
    datasource_type: Optional[str] = None
    config_overrides: Optional[dict] = None
    created_at: Optional[datetime] = None


class DatasourceTestResponse(BaseModel):
    success: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class TablesResponse(BaseModel):
    tables: List[str] = []


class SyncResponse(BaseModel):
    tables_discovered: int = 0
    tables_removed: int = 0


# ─── Settings ────────────────────────────────────────────────────────


class SettingsResponse(BaseModel):
    settings: dict


class BrandingUpdate(BaseModel):
    app_name: Optional[str] = None
    app_description: Optional[str] = None
    logo: Optional[str] = None
    icon: Optional[str] = None


class ThemeUpdate(BaseModel):
    mode: Optional[str] = Field(default=None, pattern=r"^(light|dark|system)$")
    primary_color: Optional[str] = None
    border_radius: Optional[str] = None
    font: Optional[str] = None


class LLMUpdate(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    endpoint: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    extra_params: Optional[dict] = None
    system_prompt: Optional[str] = None


class LLMEndpointWhitelistUpdate(BaseModel):
    enabled: Optional[bool] = None
    prefixes: Optional[List[str]] = None


class LLMTestRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    model: str
    endpoint: Optional[str] = None


class LLMModelsRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    endpoint: Optional[str] = None


class LLMTestResponse(BaseModel):
    success: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class GeneralUpdate(BaseModel):
    language: Optional[str] = None
    default_page: Optional[str] = None
    telemetry: Optional[bool] = None
    timezone: Optional[str] = None
    date_format: Optional[str] = None
    session_timeout: Optional[int] = None
    route_observability_window_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    request_timeout_ms: Optional[int] = Field(default=None, ge=1000, le=1800000)
    llm_connect_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    llm_read_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    llm_write_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    llm_pool_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    db_connect_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    model_list_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    route_observability_persist_enabled: Optional[bool] = None
    route_observability_persist_interval_seconds: Optional[float] = Field(default=None, ge=1.0, le=3600.0)
    route_observability_persist_event_delta: Optional[int] = Field(default=None, ge=1, le=10000)
    model_ref_case_sensitive: Optional[bool] = None


class TimeoutUpdate(BaseModel):
    request_timeout_ms: Optional[int] = Field(default=None, ge=1000, le=1800000)
    llm_connect_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    llm_read_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    llm_write_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    llm_pool_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    db_connect_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    model_list_timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)


class LLMAdvancedUpdate(BaseModel):
    max_retries: Optional[int] = Field(default=None, ge=1, le=10)
    retry_base_delay_s: Optional[float] = Field(default=None, ge=0.0, le=60.0)
    retry_max_delay_s: Optional[float] = Field(default=None, ge=0.1, le=300.0)
    http_circuit_enabled: Optional[bool] = None
    http_circuit_failure_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    http_circuit_open_seconds: Optional[float] = Field(default=None, ge=1.0, le=3600.0)
    chat_history_limit: Optional[int] = Field(default=None, ge=1, le=50)
    general_chat_history_limit: Optional[int] = Field(default=None, ge=1, le=50)


class AskSettingsUpdate(BaseModel):
    max_sql_rows: Optional[int] = Field(default=None, ge=1, le=100000)
    default_preview_row_limit: Optional[int] = Field(default=None, ge=1, le=100000)
    min_preview_row_limit: Optional[int] = Field(default=None, ge=1, le=100000)
    max_preview_row_limit: Optional[int] = Field(default=None, ge=1, le=100000)
    max_source_materialization_rows: Optional[int] = Field(default=None, ge=100, le=200000)
    analysis_cache_max: Optional[int] = Field(default=None, ge=16, le=10000)
    analysis_cache_ttl_s: Optional[float] = Field(default=None, ge=10.0, le=86400.0)


class RouterSettingsUpdate(BaseModel):
    tier1_max_retries: Optional[int] = Field(default=None, ge=1, le=10)
    tier2_max_retries: Optional[int] = Field(default=None, ge=1, le=10)
    tier3_max_retries: Optional[int] = Field(default=None, ge=1, le=10)
    tier1_max_columns_per_model: Optional[int] = Field(default=None, ge=1, le=500)
    tier2_max_columns_per_model: Optional[int] = Field(default=None, ge=1, le=500)
    tier3_max_columns_per_model: Optional[int] = Field(default=None, ge=1, le=500)
    max_sub_questions: Optional[int] = Field(default=None, ge=1, le=20)
    max_suggested_questions: Optional[int] = Field(default=None, ge=1, le=20)
    metadata_summary_max_models: Optional[int] = Field(default=None, ge=1, le=200)
    guidance_llm_available: Optional[bool] = None
    schema_pruning_enabled: Optional[bool] = None
    cross_source_max_workers: Optional[int] = Field(default=None, ge=1, le=32)
    decompose_merge_circuit_enabled: Optional[bool] = None
    decompose_merge_failure_threshold: Optional[int] = Field(default=None, ge=1, le=20)
    decompose_merge_disable_seconds: Optional[float] = Field(default=None, ge=30.0, le=86400.0)
    external_connection_pool_enabled: Optional[bool] = None
    external_connection_pool_max_per_key: Optional[int] = Field(default=None, ge=1, le=64)
    external_connection_pool_idle_seconds: Optional[float] = Field(default=None, ge=30.0, le=86400.0)
    execution_metrics_log_every: Optional[int] = Field(default=None, ge=1, le=2000)
    execution_metrics_log_interval_seconds: Optional[float] = Field(default=None, ge=10.0, le=86400.0)
    execution_metrics_max_samples: Optional[int] = Field(default=None, ge=50, le=10000)
    route_observability_window_seconds: Optional[int] = Field(default=None, ge=300, le=86400)
    route_observability_max_events_per_project: Optional[int] = Field(default=None, ge=1000, le=200000)
    route_observability_persist_enabled: Optional[bool] = None
    route_observability_persist_interval_seconds: Optional[float] = Field(default=None, ge=1.0, le=3600.0)
    route_observability_persist_event_delta: Optional[int] = Field(default=None, ge=1, le=10000)
    sql_route_v2_enabled: Optional[bool] = None
    sql_route_allowlist_projects: Optional[List[int]] = None
    sql_route_shadow_mode: Optional[bool] = None
    sql_route_event_persist_enabled: Optional[bool] = None
    model_ref_case_sensitive: Optional[bool] = None
    sql_route_profile_id: Optional[str] = None
    sql_route_profile_version: Optional[str] = None
    sql_route_strict_json_probe_enabled: Optional[bool] = None

    @field_validator("sql_route_allowlist_projects")
    @classmethod
    def validate_sql_route_allowlist_projects(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is None:
            return None
        normalized: List[int] = []
        seen = set()
        for item in value:
            item_int = int(item)
            if item_int <= 0:
                raise ValueError("sql_route_allowlist_projects must contain positive project IDs")
            if item_int in seen:
                continue
            seen.add(item_int)
            normalized.append(item_int)
        return normalized


class SecuritySettingsUpdate(BaseModel):
    sql_forbidden_keywords: Optional[List[str]] = None
    forbidden_duckdb_functions: Optional[List[str]] = None
    allowed_operators: Optional[List[str]] = None
    allowed_access_types: Optional[List[str]] = None
    rate_limit_window_s: Optional[int] = None
    rate_limit_max: Optional[int] = None
    rate_limit_max_entries: Optional[int] = None
    ws_ticket_ttl_s: Optional[int] = None
    jwt_expiry_hours: Optional[int] = None
    sso_state_ttl_s: Optional[int] = None
    oidc_cache_ttl_s: Optional[int] = None
    max_session_days: Optional[int] = None


class AppInfoResponse(BaseModel):
    version: str
    platforms: List[str] = []


# ─── Exports ─────────────────────────────────────────────────────────


class ExportResponse(BaseModel):
    download_url: Optional[str] = None


class ImportResponse(BaseModel):
    id: int
    name: Optional[str] = None


# ─── WebSocket ───────────────────────────────────────────────────────


class WSAskMessage(BaseModel):
    type: str = "ask"
    question: str
    thread_id: Optional[int] = None


class WSStateMessage(BaseModel):
    type: str = "state"
    state: str
    message: Optional[str] = None
    sql: Optional[str] = None


class WSDeltaMessage(BaseModel):
    type: str = "delta"
    content_type: str
    content: str


class WSResultMessage(BaseModel):
    type: str = "result"
    sql: Optional[str] = None
    data: Optional[List[dict]] = None
    chart_spec: Optional[dict] = None
    summary: Optional[str] = None


class WSErrorMessage(BaseModel):
    type: str = "error"
    message: str
