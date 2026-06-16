# LLM 能力探测与自适应兼容系统设计方案

> 版本: v2.0  
> 日期: 2026-06-13  
> 目标: 全面探测 LLM 能力和属性特征，持久化到数据库，根据探测结果自适应调整 AI 问答参数和提示词

---

## 1. 背景与问题

### 1.1 现状痛点

当前系统对不同 LLM 的处理缺少模型身份感知，表现在：

| 问题 | 现象 | 影响 |
|------|------|------|
| `response_format` 不兼容 | Gemma4 拒绝 `json_schema`，退到 `json_object` 仍偶发错误 | JSON 解析失败、重试增加耗时 |
| SQL 安全约束违规 | Gemma4 生成的 SQL 被 `sql_guard.py` 因 `Paren` 节点拒绝 | 查询直接失败 |
| Schema 幻觉 | Gemma4 使用不存在的列名（如 `titles.dept_no`） | DuckDB Binder 拒绝 |
| JSON 格式不稳定 | Gemma4 输出截断/格式错误 | 需重试 1-2 次 |
| repair 能力弱 | Gemma4 repair 返回空 payload | 浪费 ~120s 重试循环 |
| 参数不适应 | 不同模型的最佳 `temperature` / `max_tokens` 不同 | 输出质量不稳定 |
| 能力数据无持久化 | 每次重启后重新探测，切换模型后丢失缓存 | 重复消耗探针开销 |

### 1.2 调研参考

| 项目 | 方法 | 与本方案的相关性 |
|------|------|------------------|
| **Z7Lab/LLM-probe** | 向模型发送探针请求，检测 tool_call / json_schema / system_prompt 遵循 | 直接参考：探针设计 |
| **rmk40/opencode-model-scout** | 3 层元数据富化：provider 探针 → models.dev 数据库 → 关键词启发式 | 直接参考：分层缓存架构 |
| **LLMCapabilities (Ruby)** | 4 层能力解析：经验缓存 → OpenRouter 索引 → registry → 启发式 | 参考：能力注册表设计 |
| **LiteLLM ProviderCapabilities** | Frozen dataclass 声明 provider 能力 | 参考：数据结构 |
| **sqlglot AST 分析** | 解析 SQL 结构差异 | 已有基础，扩展 |

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                      CapabilityProbeSystem                       │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────┐      ┌─────────────────────────────┐   │
│  │    Probe Engine     │──────▶    Capability Registry      │   │
│  │                     │      │                             │   │
│  │  ┌───────────────┐  │      │  L1: 内存缓存 (dict)        │   │
│  │  │ Provider Probe │  │      │  L2: 数据库 metadata.settings│   │
│  │  ├───────────────┤  │      │  L3: 关键词启发式 Fallback  │   │
│  │  │ JsonSchema    │  │      └──────────┬──────────────────┘   │
│  │  ├───────────────┤  │                 │                      │
│  │  │ SqlSafety     │  │                 ▼                      │
│  │  ├───────────────┤  │      ┌─────────────────────────────┐   │
│  │  │ ColumnAccuracy│  │      │     Adapter Layer           │   │
│  │  ├───────────────┤  │      │                             │   │
│  │  │ Latency       │  │      │  ┌───────────────────────┐  │   │
│  │  └───────────────┘  │      │  │ response_format 降级  │  │   │
│  └─────────────────────┘      │  ├───────────────────────┤  │   │
│                                │  │ prompt 约束注入      │  │   │
│  ┌─────────────────────┐      │  ├───────────────────────┤  │   │
│  │  Trigger Events     │      │  │ 参数自适应            │  │   │
│  │                     │      │  ├───────────────────────┤  │   │
│  │  · 系统启动         │──────▶  │ repair 策略自适应     │  │   │
│  │  · LLM设置保存      │      │  ├───────────────────────┤  │   │
│  │  · 缓存过期         │      │  │ 预检查(pre-check)    │  │   │
│  │  · 管理 API 触发    │      │  └───────────────────────┘  │   │
│  └─────────────────────┘      └─────────────────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 核心数据流

```
[触发条件] → [Probe Engine] → [Capability Registry] → [Adapter Layer]
   │               │                  │                       │
   │               ▼                  ▼                       ▼
   │         ┌──────────┐     ┌──────────────┐        ┌──────────────┐
   ├─ 启动    │Provider  │     │metadata.     │        │chat() 参数   │
   ├─ 设置保存│元数据探针 │     │settings      │        │prompt 注入   │
   ├─ 缓存过期│(Ollama /  │     │llm_capability│        │repair 策略   │
   └─ API触发│OpenAI /   │     │_<model_hash> │        │校验策略      │
             │Anthropic) │     │(key-value)   │        │sql_guard     │
             └──────────┘     └──────────────┘        └──────────────┘
                                     │
                                     ▼
                             ┌────────────────┐
                             │ 上次探测时间    │
                             │ updated_at 比对 │
                             │ TTL 检查       │
                             └────────────────┘
```

---

## 3. 能力模型定义

### 3.1 完整的能力维度

探测引擎覆盖以下六大类、共 30+ 细粒度能力维度：

```python
@dataclass
class ModelCapabilities:
    """完整的 LLM 能力和属性特征模型"""
    
    # ═══════════════════════════════════════════════
    # 一、模型元信息（Provider 探针 + 关键词推断）
    # ═══════════════════════════════════════════════
    model_family: str                          # "qwen" / "gemma" / "llama" / "gpt" / "claude" / "deepseek" / "mistral" / "phi" / "unknown"
    model_size_b: float                        # 模型参数量，如 9, 70, 8x7
    provider: str                              # "ollama" / "openai" / "anthropic" / "vllm" / "custom"
    context_window: int                        # 上下文窗口大小
    max_output_tokens: int                     # 最大输出 token 数
    quantization: str                          # "q4_K_M" / "q5_K_M" / "fp16" / ""（本地模型特有）
    
    # ═══════════════════════════════════════════════
    # 二、结构化输出能力（功能探针检测）
    # ═══════════════════════════════════════════════
    supports_json_schema: bool                 # response_format={"type": "json_schema", "json_schema":{...}} 
    supports_json_object: bool                 # response_format={"type": "json_object"}
    json_mode_reliable: bool                   # json_object 模式下输出是否稳定合法 JSON
    json_output_leak_markdown: bool            # 是否在 JSON 外套 markdown 代码围栏
    json_output_field_accuracy: float          # JSON 字段完整性（0.0 ~ 1.0）
    
    # ═══════════════════════════════════════════════
    # 三、SQL 生成质量（质量探针检测）
    # ═══════════════════════════════════════════════
    sql_accuracy_tier: str                     # "high" / "medium" / "low"
    sql_safety_compliant: bool                 # 生成的 SQL 能否通过 sql_guard 验证
    sql_column_hallucination_rate: float       # 列名幻觉率（0.0 ~ 1.0，越低越好）
    sql_table_hallucination_rate: float        # 表名幻觉率
    sql_join_accuracy: float                   # JOIN 条件准确率
    sql_group_by_compliance: float             # GROUP BY 合规率
    sql_aggregate_placement: float             # 聚合函数放置正确率
    sql_syntax_validity: float                 # 语法正确率
    sql_readonly_compliance: float             # 只读约束遵循率
    
    # ═══════════════════════════════════════════════
    # 四、指令遵循能力（功能探针检测）
    # ═══════════════════════════════════════════════
    system_prompt_adherence: str               # "strict" / "normal" / "weak"
    instruction_following_score: float         # 指令遵循综合评分（0.0 ~ 1.0）
    format_compliance: float                   # 输出格式遵循率
    reasoning_leak: bool                       # 是否泄露 reasoning/think 块内容
    empty_output_rate: float                   # 空输出概率
    
    # ═══════════════════════════════════════════════
    # 五、修复与纠错能力（质量探针检测）
    # ═══════════════════════════════════════════════
    repair_capability: bool                    # 修复提示是否有效
    repair_success_rate: float                 # 修复成功率
    error_feedback_utilization: float          # 错误反馈利用程度
    max_useful_repair_attempts: int            # 最大有效修复尝试次数
    
    # ═══════════════════════════════════════════════
    # 六、性能特性（延迟探针检测 + Provider 元数据）
    # ═══════════════════════════════════════════════
    recommended_temperature: float             # 建议温度值
    recommended_max_tokens: int                # 建议最大 token 数
    supports_streaming: bool                   # 是否支持流式输出
    supports_vision: bool                      # 是否支持图片输入
    supports_tool_calling: bool                # 是否支持工具调用
    avg_response_latency_ms: float             # 平均响应延迟
    latency_p50_ms: float                      # 延迟中位数
    latency_p95_ms: float                      # 延迟 P95
    token_generation_speed: float              # token 生成速度（tokens/s）
    
    # ═══════════════════════════════════════════════
    # 七、探测元信息
    # ═══════════════════════════════════════════════
    model_key: str                             # 模型唯一标识 provider:endpoint:model
    probe_version: int                         # 探测方案版本号
    probe_count: int                           # 已执行探测次数
    last_error: str                            # 最近一次错误信息
    probed_at: datetime                        # 探测完成时间
    probe_duration_ms: float                   # 探测总耗时
    probe_level: str                           # "full" / "keyword_only" / "partial"
```

### 3.2 聚合能力档位

为简化适配层决策，将细粒度能力聚合为三个档位：

```python
@dataclass
class ModelTierProfile:
    tier: str                                  # "strong" / "medium" / "weak"
    
    # response_format 策略
    response_format_strategy: str              # "json_schema" / "json_object" / "text_with_instruction"
    
    # 参数
    temperature: float
    max_tokens: int
    extra_params: dict
    
    # prompt 约束
    extra_system_suffix: str                   # 追加的系统提示后缀
    sql_constraint_level: str                  # "none" / "normal" / "strict"
    
    # 修复策略
    max_repair_attempts: int
    skip_repair_if_json_empty: bool
    json_parse_retries: int
    
    # 预检查
    enable_fast_precheck: bool                 # 是否启用 SQL 快速拒绝预检查
    precheck_check_paren: bool                 # 检查多余括号
    precheck_check_keywords: bool              # 检查禁止关键字
    precheck_check_readonly: bool              # 检查只读约束
```

---

## 4. 数据库存储设计

### 4.1 复用 `metadata.settings` 表

使用已有的 `metadata.settings`（key-value）表存储能力数据，无需新建表。键名命名规范：

```python
# 键名模式
llm_capability_{provider}:{endpoint_hash}:{model_hash}

# 示例
llm_capability_ollama:a1b2c3d4:e5f6g7h8
```

其中 `endpoint_hash` 和 `model_hash` 使用 hashlib.sha256 摘要的前 8 位，确保唯一性同时避免键名过长。

### 4.2 值结构

`value` 列为完整的 JSON 对象，结构如下：

```json
{
    "model_meta": {
        "model_family": "gemma",
        "model_size_b": 9.0,
        "provider": "ollama",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "quantization": "q4_K_M"
    },
    "structured_output": {
        "supports_json_schema": false,
        "supports_json_object": true,
        "json_mode_reliable": false,
        "json_output_leak_markdown": true,
        "json_output_field_accuracy": 0.75
    },
    "sql_quality": {
        "sql_accuracy_tier": "low",
        "sql_safety_compliant": false,
        "sql_column_hallucination_rate": 0.35,
        "sql_table_hallucination_rate": 0.05,
        "sql_join_accuracy": 0.6,
        "sql_group_by_compliance": 0.3,
        "sql_aggregate_placement": 0.4,
        "sql_syntax_validity": 0.7,
        "sql_readonly_compliance": 0.5
    },
    "instruction": {
        "system_prompt_adherence": "weak",
        "instruction_following_score": 0.5,
        "format_compliance": 0.6,
        "reasoning_leak": false,
        "empty_output_rate": 0.1
    },
    "repair": {
        "repair_capability": false,
        "repair_success_rate": 0.0,
        "error_feedback_utilization": 0.2,
        "max_useful_repair_attempts": 0
    },
    "performance": {
        "recommended_temperature": 0.1,
        "recommended_max_tokens": 4096,
        "supports_streaming": true,
        "supports_vision": false,
        "supports_tool_calling": false,
        "avg_response_latency_ms": 2850.0,
        "latency_p50_ms": 2700.0,
        "latency_p95_ms": 4200.0,
        "token_generation_speed": 35.5
    },
    "probe_meta": {
        "model_key": "ollama:localhost:11434:gemma4:latest",
        "probe_version": 2,
        "probe_count": 1,
        "last_error": "",
        "probed_at": "2026-06-13T10:30:00Z",
        "probe_duration_ms": 5230,
        "probe_level": "full"
    }
}
```

### 4.3 数据库操作接口

```python
_CAPABILITY_KEY_PREFIX = "llm_capability_"

def _model_capability_key(provider: str, endpoint: str, model: str) -> str:
    """生成数据库键名"""
    endpoint_hash = hashlib.sha256((endpoint or "").encode()).hexdigest()[:8]
    model_hash = hashlib.sha256((model or "").encode()).hexdigest()[:8]
    return f"{_CAPABILITY_KEY_PREFIX}{provider}:{endpoint_hash}:{model_hash}"

def _load_capability_from_db(provider: str, endpoint: str, model: str) -> Optional[dict]:
    """从数据库加载能力数据"""
    key = _model_capability_key(provider, endpoint, model)
    con = get_connection()
    row = con.execute(
        "SELECT value, updated_at FROM metadata.settings WHERE key = ?", [key]
    ).fetchone()
    if row is None:
        return None
    value, updated_at = row
    # 检查 TTL（默认 24 小时）
    if _is_capability_expired(updated_at):
        return None
    return json.loads(value) if isinstance(value, str) else value

def _save_capability_to_db(provider: str, endpoint: str, model: str, data: dict) -> None:
    """保存能力数据到数据库"""
    key = _model_capability_key(provider, endpoint, model)
    con = get_connection()
    con.execute(
        "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
        [key, json.dumps(data)]
    )

def _delete_stale_capability(provider: str, endpoint: str, model: str) -> None:
    """删除过期的能力数据（模型切换时）"""
    key = _model_capability_key(provider, endpoint, model)
    con = get_connection()
    con.execute("DELETE FROM metadata.settings WHERE key = ?", [key])

def _list_all_capabilities() -> list[dict]:
    """列出数据库中所有已探测的模型能力"""
    con = get_connection()
    rows = con.execute(
        "SELECT key, value, updated_at FROM metadata.settings WHERE key LIKE ?",
        [_CAPABILITY_KEY_PREFIX + "%"]
    ).fetchall()
    results = []
    for key, value, updated_at in rows:
        data = json.loads(value) if isinstance(value, str) else value
        results.append({
            "key": key,
            "model_key": data.get("probe_meta", {}).get("model_key", key),
            "probed_at": data.get("probe_meta", {}).get("probed_at"),
            "probe_level": data.get("probe_meta", {}).get("probe_level"),
            "updated_at": updated_at,
        })
    return results
```

### 4.4 TTL 与过期策略

```python
# 默认 TTL 配置
_CAPABILITY_TTL_SECONDS = 86400    # 24 小时（常规）
_CAPABILITY_TTL_PROBE_FAILED = 300 # 5 分钟（探测失败时短 TTL，允许快速重试）
_CAPABILITY_KEYWORD_TTL = 3600     # 1 小时（关键词启发式，无探针时短 TTL）

def _is_capability_expired(updated_at, ttl_seconds=None) -> bool:
    if updated_at is None:
        return True
    if ttl_seconds is None:
        ttl_seconds = _CAPABILITY_TTL_SECONDS
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)
    elapsed = (datetime.now() - updated_at).total_seconds()
    return elapsed > ttl_seconds
```

---

## 5. 探测引擎设计 (Probe Engine)

### 5.1 触发时机与加载优先级

```
get_model_capabilities(model, provider, endpoint)
  │
  ├── (1) L1: 内存缓存命中且未过期？→ 直接返回
  │
  ├── (2) L2: 数据库中存在且未过期？→ 加载到内存，返回
  │
  ├── (3) L3: 关键词启发式可用？→ 加载到内存+DB，标记 probe_level="keyword_only"，返回
  │
  └── (4) 以上均无 → 执行同步探测 → 保存到内存+DB → 返回
```

### 5.2 同步探测流程（完整探针）

```
probe_sync(provider, endpoint, model)
  │
  ├── Stage 1: Provider 元数据探针 (~1s)
  │   ├── Ollama:  GET /api/show → context_window, model_family, quantization
  │   ├── OpenAI:  GET /v1/models → model metadata
  │   └── Generic: 关键词匹配 → model_family, model_size_b
  │
  ├── Stage 2: 功能探针 (~3s, 3条请求)
  │   ├── JsonSchemaProbe:    发送 json_schema 请求 → 检测 structured_output
  │   ├── JsonObjectProbe:    发送 json_object 请求 → 检测 json_mode_reliable
  │   └── SystemPromptProbe:  发送含系统提示覆盖的请求 → 检测 adherence
  │
  ├── Stage 3: SQL 质量探针 (~3s, 3条请求)
  │   ├── SqlSyntaxProbe:     发送"生成 SQL 查询某个表的销售量" → 检查列幻觉/语法
  │   ├── SqlSafetyProbe:     发送"修改表结构"→ 检查只读约束遵循
  │   └── SqlGroupByProbe:    发送"按城市分组统计" → 检查 GROUP BY 合规
  │
  ├── Stage 4: 延迟探针 (~2s, 重复2次)
  │   └── LatencyProbe: 简单问候 → 统计延迟特征
  │
  ├── 聚合计算推荐参数
  │   ├── recommended_temperature = f(json_reliable, adherence, hallucination)
  │   └── 各档位阈值判定
  │
  └── 保存到数据库 + 更新内存缓存
```

### 5.3 异步探测（后台执行）

启动时或模型切换时，立即用关键词启发式填充能力数据（不阻塞），同时异步执行完整探针，完成后更新数据库和内存缓存：

```python
def trigger_async_probe(provider, endpoint, model):
    # 1. 立即用关键词启发式填充（非阻塞）
    keyword_caps = _keyword_fallback(provider, endpoint, model)
    _save_capability_to_db(provider, endpoint, model, keyword_caps)
    _update_memory_cache(provider, endpoint, model, keyword_caps)
    
    # 2. 后台线程执行完整探测
    def _probe_worker():
        full_caps = probe_sync(provider, endpoint, model)
        _save_capability_to_db(provider, endpoint, model, full_caps)
        _update_memory_cache(provider, endpoint, model, full_caps)
    
    thread = threading.Thread(target=_probe_worker, daemon=True)
    thread.start()
```

### 5.4 探针详细设计

每条探针请求统一格式：

```python
@dataclass
class ProbeRequest:
    name: str                              # 探针名称
    stage: int                             # 阶段编号
    messages: list[dict]                   # 发送的消息
    response_format: Optional[Any]          # 期望的 response_format
    timeout_s: float = 30.0
    retry_count: int = 1
    
@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str
    response: Optional[str]
    latency_ms: float
    attempts: int
    score: float                           # 0.0 ~ 1.0 评分
```

#### JsonSchemaProbe

```python
def _probe_json_schema(llm) -> ProbeResult:
    """检测 json_schema response_format 支持"""
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "test",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["name", "value"],
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    }
    messages = [
        {"role": "user", "content": "Output name='test' and value=42 as JSON."}
    ]
    try:
        result = llm.chat(messages, response_format=schema)
        content = _llm_content_text(result)
        parsed = json.loads(content)
        passed = isinstance(parsed, dict) and parsed.get("name") == "test" and parsed.get("value") == 42
        return ProbeResult("json_schema", passed, str(parsed) if passed else f"unexpected: {content[:200]}", content, ...)
    except Exception as e:
        return ProbeResult("json_schema", False, str(e), None, ...)
```

#### JsonObjectProbe

```python
def _probe_json_object(llm) -> ProbeResult:
    """检测 json_object response_format - 不指定 schema，看是否输出合法 JSON"""
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Output valid JSON."},
        {"role": "user", "content": "Respond with name=test value=42."}
    ]
    try:
        result = llm.chat(messages, response_format="json")
        content = _llm_content_text(result)
        parsed = json.loads(content)
        passed = isinstance(parsed, dict)
        return ProbeResult("json_object", passed, f"parsed={passed}", content, ...)
    except Exception as e:
        return ProbeResult("json_object", False, str(e), None, ...)
```

#### SqlSafetyProbe

```python
def _probe_sql_safety(llm) -> ProbeResult:
    """检测 SQL 只读约束遵循 - 看模型是否会在提示下生成非只读 SQL"""
    messages = [
        {"role": "system", "content": "You generate SQL queries."},
        {"role": "user", "content": "Change the salaries table to add a bonus column."}
    ]
    try:
        result = llm.chat(messages)
        content = _llm_content_text(result)
        # 用 sql_guard 验证
        try:
            validate_read_only_sql(content)
            passed = True  # 模型拒绝了修改请求，生成了只读 SQL（好的！）
            detail = "Generated read-only SQL despite modification prompt"
        except ValueError:
            # 检查内容是否包含 ALTER TABLE
            if any(kw in content.upper() for kw in ["ALTER TABLE", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE"]):
                passed = False  # 模型真的生成了修改 SQL（坏的！）
                detail = "Generated non-read-only SQL"
            else:
                passed = True  # 可能是语法错误，但不表示不遵循只读约束
                detail = "Syntax error, not safety violation"
        return ProbeResult("sql_safety", passed, detail, content, ...)
    except Exception as e:
        return ProbeResult("sql_safety", False, str(e), None, ...)
```

#### SqlColumnAccuracyProbe

```python
def _probe_sql_column_accuracy(llm, schema_hint: str) -> ProbeResult:
    """检测列名幻觉 - 提供真实 schema，看模型是否引用不存在的列"""
    messages = [
        {"role": "system", "content": "You are a SQL generator. Use ONLY columns from the schema."},
        {"role": "user", "content": f"Schema:\n{schema_hint}\n\nQuery: total sales by city"}
    ]
    try:
        result = llm.chat(messages)
        content = _llm_content_text(result)
        # 用项目已有的列引用校验
        unknown_columns = _validate_sql_columns(content, [])
        hallucination_count = len(unknown_columns) if unknown_columns else 0
        passed = hallucination_count == 0
        return ProbeResult("column_accuracy", passed, f"unknown_columns={hallucination_count}", content, ...)
    except Exception as e:
        return ProbeResult("column_accuracy", False, str(e), None, ...)
```

### 5.5 关键词启发式 Fallback

无需请求 LLM，直接从模型名推断能力档位：

```python
_MODEL_FAMILY_KEYWORDS: dict[str, dict[str, Any]] = {
    "gpt-4":     { "family": "openai",      "tier": "strong", ... },
    "gpt-5":     { "family": "openai",      "tier": "strong", ... },
    "claude":    { "family": "anthropic",   "tier": "strong", ... },
    "qwen2.5":   { "family": "qwen",        "tier": "medium", ... },
    "qwen3":     { "family": "qwen",        "tier": "medium", ... },
    "deepseek":  { "family": "deepseek",    "tier": "strong", ... },
    "llama-3":   { "family": "llama",       "tier": "medium", ... },
    "llama-4":   { "family": "llama",       "tier": "medium", ... },
    "mistral":   { "family": "mistral",     "tier": "medium", ... },
    "mixtral":   { "family": "mistral",     "tier": "medium", ... },
    "gemma-2":   { "family": "gemma",       "tier": "weak",   ... },
    "gemma-3":   { "family": "gemma",       "tier": "weak",   ... },
    "gemma-4":   { "family": "gemma",       "tier": "weak",   ... },
    "gemma":     { "family": "gemma",       "tier": "weak",   ... },  # 通用兜底
    "phi":       { "family": "phi",         "tier": "weak",   ... },
    "tinyllama": { "family": "llama",       "tier": "weak",   ... },
}

def _keyword_fallback(provider: str, endpoint: str, model: str) -> dict:
    """根据模型名关键词推断能力"""
    model_lower = model.lower()
    matched = None
    matched_len = 0
    for keyword, config in _MODEL_FAMILY_KEYWORDS.items():
        if keyword in model_lower and len(keyword) > matched_len:
            matched = config
            matched_len = len(keyword)
    
    if matched is None:
        matched = { "family": "unknown", "tier": "weak" }
    
    caps = _default_capabilities(provider, endpoint, model)
    caps["model_meta"]["model_family"] = matched["family"]
    _apply_tier_defaults(caps, matched["tier"])
    caps["probe_meta"]["probe_level"] = "keyword_only"
    return caps

def _apply_tier_defaults(caps: dict, tier: str) -> None:
    """根据档位填充默认能力值"""
    TIER_DEFAULTS = {
        "strong": {
            "structured_output": {
                "supports_json_schema": True,
                "supports_json_object": True,
                "json_mode_reliable": True,
            },
            "sql_quality": {
                "sql_accuracy_tier": "high",
                "sql_safety_compliant": True,
                "sql_column_hallucination_rate": 0.05,
            },
            "instruction": {
                "system_prompt_adherence": "strict",
                "instruction_following_score": 0.95,
            },
            "repair": {"repair_capability": True, "max_useful_repair_attempts": 2},
            "performance": {"recommended_temperature": 0.3},
        },
        "medium": {
            "structured_output": {
                "supports_json_schema": True,
                "supports_json_object": True,
                "json_mode_reliable": True,
            },
            "sql_quality": {
                "sql_accuracy_tier": "medium",
                "sql_safety_compliant": True,
                "sql_column_hallucination_rate": 0.15,
            },
            "instruction": {
                "system_prompt_adherence": "normal",
                "instruction_following_score": 0.8,
            },
            "repair": {"repair_capability": True, "max_useful_repair_attempts": 1},
            "performance": {"recommended_temperature": 0.2},
        },
        "weak": {
            "structured_output": {
                "supports_json_schema": False,
                "supports_json_object": True,
                "json_mode_reliable": False,
            },
            "sql_quality": {
                "sql_accuracy_tier": "low",
                "sql_safety_compliant": False,
                "sql_column_hallucination_rate": 0.35,
            },
            "instruction": {
                "system_prompt_adherence": "weak",
                "instruction_following_score": 0.5,
            },
            "repair": {"repair_capability": False, "max_useful_repair_attempts": 0},
            "performance": {"recommended_temperature": 0.1},
        },
    }
    defaults = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["weak"])
    for category, values in defaults.items():
        for k, v in values.items():
            caps.setdefault(category, {})[k] = v
```

---

## 6. 适配层设计 (Adapter Layer)

### 6.1 response_format 降级决策

```
get_response_format_strategy(capabilities)
  │
  ├── supports_json_schema == True?
  │     ├── Yes → "json_schema" (使用严格 schema 约束)
  │     └── No  → supports_json_object == True?
  │                 ├── Yes → json_mode_reliable?
  │                 │         ├── Yes → "json_object"
  │                 │         └── No  → "text_with_instruction"
  │                 └── No  → "text_with_instruction"
  │
  └── 返回值: {"strategy": str, "response_format": Optional[dict], "retry_on_failure": str}
```

### 6.2 Prompt 自适应注入

根据模型档位在 system_suffix 中增加不同强度的约束：

**Strong 模型 suffix**:
```
无额外约束（标准 prompt）
```

**Medium 模型 suffix**:
```
- Output ONLY valid JSON. No markdown fences, no extra text.
- Every non-aggregated column in SELECT must appear in GROUP BY.
- Use column names EXACTLY as they appear in the schema.
- Never wrap the entire query in parentheses.
```

**Weak 模型 suffix**:
```
CRITICAL - Output ONLY valid JSON. No markdown, no code fences, no extra text.
CRITICAL - Only use SELECT or WITH...SELECT. No INSERT/UPDATE/DELETE.
CRITICAL - Confirm every column name exists in the schema before using it.
CRITICAL - Never put aggregate functions (COUNT, SUM, AVG, MIN, MAX) in GROUP BY.
CRITICAL - Never wrap expressions or the full query in extra parentheses.
CRITICAL - All non-aggregated SELECT columns must be in GROUP BY.
```

### 6.3 参数自适应

```python
_TIER_PARAMS: dict[str, dict[str, Any]] = {
    "strong": {
        "temperature": 0.3,
        "max_tokens": 4096,
        "extra_params": {},
    },
    "medium": {
        "temperature": 0.2,
        "max_tokens": 4096,
        "extra_params": {},
    },
    "weak": {
        "temperature": 0.1,              # 低温减少随机性
        "max_tokens": 4096,
        "extra_params": {
            "top_p": 0.9,                # top_p 辅助稳定
            "repeat_penalty": 1.1,       # 减少重复/截断
            "frequency_penalty": 0.1,    # 轻微频率惩罚
        },
    },
}
```

### 6.4 修复策略自适应

```python
_REPAIR_TIER_CONFIG: dict[str, dict[str, Any]] = {
    "strong": {
        "max_repair_attempts": 2,
        "json_parse_retries": 1,
        "repair_timeout_s": 60,
        "skip_repair_if_json_empty": False,
        "retry_on_binder_error": True,
    },
    "medium": {
        "max_repair_attempts": 1,
        "json_parse_retries": 2,
        "repair_timeout_s": 90,
        "skip_repair_if_json_empty": False,
        "retry_on_binder_error": True,
    },
    "weak": {
        "max_repair_attempts": 0,         # 不 repair，直接出 editable SQL
        "json_parse_retries": 2,
        "repair_timeout_s": 30,
        "skip_repair_if_json_empty": True,
        "retry_on_binder_error": False,   # binder 错误直接返回，不重试
    },
}
```

### 6.5 SQL 预检查快速拒绝（Weak 模型专属）

```python
def _fast_precheck_bad_sql(sql: str, tier: str, capabilities: dict) -> Optional[str]:
    """对 weak 模型生成的 SQL 做快速拒绝检查"""
    if tier != "weak":
        return None
    
    errors = []
    sql_upper = sql.upper()
    
    # 1. 检查是否包含非 SELECT 关键字
    forbidden = ["INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE "]
    for kw in forbidden:
        if kw in sql_upper:
            errors.append(f"Contains forbidden keyword: {kw.strip()}")
    
    # 2. 检查括号匹配
    if sql.count("(") != sql.count(")"):
        errors.append("Unmatched parentheses")
    
    # 3. 检查是否有 shell 控制字符
    if re.search(r'[;&|`$]', sql):
        errors.append("Contains shell control characters")
    
    # 4. 如果开启了检查，用 sqlglot 快速解析检查
    if capabilities.get("sql_quality", {}).get("sql_hallucination_risk") != "low":
        try:
            parsed = sqlglot.parse_one(sql, read="duckdb")
            # 检查是否有禁止的表达式
            if any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter))
                   for node in parsed.walk()):
                errors.append("AST contains non-READ-ONLY expressions")
        except Exception:
            errors.append("SQL parse failed")
    
    return "; ".join(errors) if errors else None
```

---

## 7. 能力注册表缓存设计

### 7.1 三层缓存架构

```
┌──────────────────────────────────────────────────────────────┐
│                     CapabilityRegistry                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  L1: 内存缓存 (thread-safe dict)                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  key: provider:endpoint_hash:model_hash              │   │
│  │  value: { data: dict, loaded_at: float, ttl: float } │   │
│  │  TTL: 300s (可配置)                                   │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                  │
│                            ▼                                  │
│  L2: 数据库持久化                                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  table: metadata.settings                            │   │
│  │  key: llm_capability_{provider}:{hash8}:{hash8}       │   │
│  │  value: JSON (完整能力数据)                            │   │
│  │  TTL: 86400s (24小时)                                 │   │
│  │  updated_at: TIMESTAMP (用于过期判断)                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                  │
│                            ▼                                  │
│  L3: 关键词启发式 Fallback                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  按模型名关键词逐级匹配家族 → 档位 → 填充默认值          │   │
│  │  probe_level = "keyword_only"                        │   │
│  │  TTL: 3600s (1小时)                                   │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 加载策略

```python
def get_model_capabilities(
    provider: str,
    endpoint: str,
    model: str,
    force_refresh: bool = False,
) -> dict:
    """获取模型能力 - 按 L1→L2→L3 优先级加载"""
    
    cache_key = _cache_key(provider, endpoint, model)
    
    # L1: 内存缓存
    if not force_refresh:
        cached = _memory_cache_get(cache_key)
        if cached is not None and not _is_cache_expired(cached["loaded_at"], cached.get("ttl", 300)):
            return cached["data"]
    
    # L2: 数据库
    if not force_refresh:
        db_data = _load_capability_from_db(provider, endpoint, model)
        if db_data is not None:
            _memory_cache_set(cache_key, db_data)
            return db_data
    
    # L3: 关键词启发式
    keyword_data = _keyword_fallback(provider, endpoint, model)
    # 保存到数据库 + 内存缓存
    _save_capability_to_db(provider, endpoint, model, keyword_data)
    _memory_cache_set(cache_key, keyword_data, ttl=_CAPABILITY_KEYWORD_TTL)
    
    # 后台异步执行完整探测
    _trigger_async_probe(provider, endpoint, model)
    
    return keyword_data
```

### 7.3 强制刷新

```python
def probe_and_save(
    provider: str,
    endpoint: str,
    model: str,
) -> dict:
    """强制同步完整探测并保存到数据库"""
    cache_key = _cache_key(provider, endpoint, model)
    
    # 执行完整探测
    caps = _probe_sync(provider, endpoint, model)
    
    # 保存到数据库
    _save_capability_to_db(provider, endpoint, model, caps)
    
    # 更新内存缓存
    _memory_cache_set(cache_key, caps)
    
    return caps
```

---

## 8. 与现有代码的集成点

### 8.1 `services/llm_service.py` — chat() 方法

```python
class LLMService:
    def chat(self, messages, response_format=None, timeout=None):
        # ── 新增：获取模型能力，自适应调整 ──
        capabilities = get_model_capabilities(
            self.config.get("provider", ""),
            self.config.get("endpoint", ""),
            self.config.get("model", ""),
        )
        tier = _capabilities_to_tier(capabilities)
        
        # 自适应 response_format
        response_format = _adapt_response_format(
            response_format, capabilities
        )
        
        # 自适应温度参数
        self.config["temperature"] = capabilities.get(
            "performance", {}
        ).get("recommended_temperature", self.config.get("temperature", 0.7))
        
        # ── 原有逻辑 ──
        ...
```

### 8.2 `services/ask_observability.py` — _llm_semantic_link

```python
def _llm_semantic_link(question, project_id, *, models=None, relations=None, llm=None, language=None):
    # ── 新增：注入模型特定约束 ──
    capabilities = get_model_capabilities(
        llm.config.get("provider", ""),
        llm.config.get("endpoint", ""),
        llm.config.get("model", ""),
    ) if llm else {}
    tier = _capabilities_to_tier(capabilities)
    
    # 原有 prompt 选择
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "semantic_link",
        strict_json_mode=strict_json.get("mode", "none"),
        model_tier=tier,           # 新增参数
    )
    
    # 追加模型特定约束
    system_suffix = prompt_selection.system_suffix or ""
    system_suffix += tier.get("extra_system_suffix", "")
    
    # 自适应 response_format
    response_format = _resolve_response_format(capabilities, default_format)
    
    # ── 原有逻辑 ──
    ...
```

### 8.3 `services/ask_service.py` — SQL 执行与修复

```python
def _execute_duckdb_semantic_query(project_id, planned_sql, ...):
    # 获取当前模型能力
    llm_config = get_llm_config()
    capabilities = get_model_capabilities(
        llm_config.get("provider", ""),
        llm_config.get("endpoint", ""),
        llm_config.get("model", ""),
    )
    tier = _capabilities_to_tier(capabilities)
    
    # 快速拒绝预检查
    precheck_error = _fast_precheck_bad_sql(
        planned_sql, tier, capabilities
    )
    if precheck_error:
        return _warning_query_result(
            f"SQL pre-check failed: {precheck_error}", plan, start
        )
    
    # ── 原有逻辑 ──
    ...
```

### 8.4 `services/ask_service.py` — Repair 策略

```python
def _repair_sql(sql, errors, ...):
    llm_config = get_llm_config()
    capabilities = get_model_capabilities(...)
    tier = _capabilities_to_tier(capabilities)
    repair_config = _REPAIR_TIER_CONFIG.get(tier, _REPAIR_TIER_CONFIG["weak"])
    
    if repair_config["max_repair_attempts"] <= 0:
        # 跳过 repair，直接返回 editable SQL
        return sql, [], ["Repair not supported for this model"]
    
    # 使用 repair_config 中的参数替代固定值
    ...
```

### 8.5 `services/sql_guard.py` — 预检查

```python
def normalize_read_only_sql(sql, dialect="duckdb"):
    # 在现有安全检查前增加快速拒绝
    capabilities = ...  # 从当前上下文获取
    tier = _capabilities_to_tier(capabilities)
    precheck_error = _fast_precheck_bad_sql(sql, tier, capabilities)
    if precheck_error:
        raise ValueError(precheck_error)
    
    # ── 原有逻辑 ──
    ...
```

### 8.6 `services/sql_routing/prompt_profiles.py` — PromptProfileRouter

```python
class PromptProfileRouter:
    def select(self, stage, *, strict_json_mode="none", profile_id=None, profile_version=None, model_tier=None):
        # 根据 model_tier 选择 profile
        if model_tier == "weak":
            profile_id = profile_id or "prismbi.weak_model"
        return PromptProfileSelection(...)
```

### 8.7 `routers/settings.py` — 管理 API

```python
# LLM 设置保存时触发探测
@router.put("/api/settings/llm")
async def update_llm_settings(data: LLMUpdate):
    # ── 原有保存逻辑 ──
    ...
    # ── 新增：触发同步探测 ──
    capabilities = probe_and_save(
        data.provider, data.endpoint, data.model
    )
    return {
        "status": "ok",
        "capabilities": capabilities,
    }

# 新增：探测状态 API
@router.post("/api/settings/llm/probe")
async def trigger_probe():
    llm_config = get_llm_config()
    probe_and_save(
        llm_config.get("provider", ""),
        llm_config.get("endpoint", ""),
        llm_config.get("model", ""),
    )
    return {"status": "completed"}

@router.get("/api/settings/llm/probe")
async def get_probe_status():
    capabilities = get_model_capabilities(...)
    return {
        "model": capabilities.get("probe_meta", {}).get("model_key"),
        "probe_level": capabilities.get("probe_meta", {}).get("probe_level"),
        "probed_at": capabilities.get("probe_meta", {}).get("probed_at"),
        "tier": _capabilities_to_tier(capabilities),
        "capabilities": capabilities,
    }

# 新增：列出所有已探测模型
@router.get("/api/settings/llm/probe/history")
async def list_probed_models():
    return {"models": _list_all_capabilities()}
```

---

## 9. 文件改动清单

### 9.1 新增文件

| 文件 | 职责 | 预估行数 |
|------|------|----------|
| `services/sql_routing/llm_capability.py` | **扩展重写**：数据类 `ModelCapabilities`, `ModelTierProfile`；注册表类 `CapabilityRegistry`；数据库读写接口；关键词启发式 | 500 |
| `services/sql_routing/llm_probe_suite.py` | **新建**：各探针实现（JsonSchemaProbe / JsonObjectProbe / SqlSafetyProbe / SqlColumnAccuracyProbe / LatencyProbe / SystemPromptProbe）；探针编排逻辑 `probe_sync()` / `trigger_async_probe()` | 600 |

### 9.2 修改文件

| 文件 | 改动 | 预估行数 |
|------|------|----------|
| `services/llm_service.py` | `chat()` 中接入能力注册表：response_format 自适应、温度自适应 | +40 |
| `services/ask_observability.py` | `_llm_semantic_link()` 中注入模型约束 suffix + response_format 自适应 | +30 |
| `services/ask_service.py` | SQL 执行前快速预检查；repair 策略自适应 | +60 |
| `services/sql_guard.py` | 增加 `_fast_precheck_bad_sql()` 入口 | +30 |
| `services/sql_routing/prompt_profiles.py` | `PromptProfileRouter.select()` 支持 model_tier 参数 | +20 |
| `routers/settings.py` | LLM 设置保存触发探测；新增 probe 管理 API | +100 |
| `main.py` 或启动脚本 | 启动后异步触发首次探测 | +15 |

**总计**：新增 ~1100 行，修改 ~295 行

---

## 10. 能力预设速查表

| 模型名关键词 | 家族 | 档位 | json_schema | json_object | sql_safety | hallucination | repair | 推荐温度 |
|-------------|------|------|-------------|-------------|------------|---------------|--------|----------|
| gpt-4*, gpt-5* | openai | strong | ✅ | ✅ | ✅ | low | ✅ | 0.3 |
| claude-3*, claude-4* | anthropic | strong | ✅ | ✅ | ✅ | low | ✅ | 0.3 |
| qwen2.5*, qwen3* | qwen | medium | ✅ | ✅ | ✅ | low | ✅ | 0.2 |
| deepseek* | deepseek | strong | ✅ | ✅ | ✅ | medium | ✅ | 0.3 |
| llama-3*, llama-4* | llama | medium | ✅ | ✅ | ✅ | medium | ✅ | 0.2 |
| mistral*, mixtral* | mistral | medium | ✅ | ✅ | ✅ | medium | ✅ | 0.2 |
| gemma-2/3/4* | gemma | weak | ❌ | ✅ | ❌ | high | ❌ | 0.1 |
| phi-3*, phi-4* | phi | weak | ❌ | ✅ | ❌ | high | ❌ | 0.1 |
| tinyllama* | llama | weak | ❌ | ❌ | ❌ | high | ❌ | 0.1 |
| 其他/未知 | unknown | weak | ❌ | ❌ | ❌ | high | ❌ | 0.1 |

---

## 11. 验收标准

| 编号 | 标准 | 验证方式 |
|------|------|----------|
| AC1 | Qwen3.5:9b 切换后自动识别为 medium 档位 | 查看 /api/settings/llm/probe 返回的 tier |
| AC2 | Gemma4:latest 切换后自动识别为 weak 档位 | 同上 |
| AC3 | Weak 档位自动跳过 repair 循环 | Gemma SQL 失败后直接出 editable SQL |
| AC4 | Weak 档位注入额外 SQL 约束指令 | 比较 Qwen/Gemma 的 system_suffix 差异 |
| AC5 | Strong 档位使用 json_schema response_format | 查看 LLM 调用日志 payload |
| AC6 | Weak 档位使用 text_with_instruction 兜底 | 同上 |
| AC7 | 探测数据持久化到数据库，重启后从数据库加载 | 重启服务后 probe/status 返回相同数据 |
| AC8 | 数据库中记录更新时间，过期后自动重新探测 | `updated_at` 超过 TTL 后自动触发探针 |
| AC9 | 关键词启发式在无网络时也能工作 | 断开网络后，关键词仍能正确分档 |
| AC10 | LLM 设置保存 API 触发同步探测 | PUT /api/settings/llm 返回包含 capabilities |
| AC11 | 454 个已有测试全部通过 | `python3 -m pytest tests/ -q` |
| AC12 | 管理 API 可查询探测历史和状态 | GET /api/settings/llm/probe 系列端点工作正常 |

---

## 12. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 探针消耗 Token/延迟 | 高 | 低 | 仅启动/切换时探测；探针使用极短 prompt（<50 tokens） |
| 探针误判 | 中 | 中 | 多层验证（关键词+探针+运行时反馈）+ 定期重新探测修正 |
| 探针超时 | 高 | 低 | 每探针有独立 timeout + 全部失败用关键词启发式兜底 |
| 数据库写入失败 | 低 | 低 | L1 内存缓存保障运行，数据库失败不阻塞主流程 |
| 并发探测冲突 | 低 | 中 | 探测线程锁 + 按 cache_key 互斥 |
| 未知模型名 | 中 | 低 | 默认 weak 档位（保守安全） |
