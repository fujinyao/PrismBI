# PrismBI 详细设计文档

> 基于 WrenAI Open Context Engine 重构的新一代 AI BI 工具  
> 设计版本: v4.0 | 状态: **实现同步中** | 最近同步: 2026-06-08

> 当前实现基线: PrismBI 已具备本地 JWT 登录、session-bound JWT 会话撤销、API token bearer 认证、多项目、项目级 DuckDB 数据源隔离、Sample/Manual 项目创建向导、模型/视图/关系/计算字段 metadata description 编辑、Admin 用户/角色/权限/审计/备份恢复基础闭环、系统设置敏感值脱敏与存储层 Fernet 加密, 并具备 DuckDB/sample 真实执行、有限非 DuckDB 单源/跨源执行、共享 SQL 只读 guard、复杂 SQL RLS 下推 (支持 CTE/subquery/alias/join)、CLS 引用拦截与表达式级 MASK 重写、Knowledge 文本检索注入 Ask、WebSocket Ask (含逐 chunk 流 + 步骤进度)、Recommendation Catalog/Hints/评分/统计 API、Dashboard 空 widget 与 Save as View 与 viewer-aware 缓存重算、**备份与恢复** (创建/列表/下载/恢复/删除 — 含路径穿越防护、zip穿越验证、原子替换、连接管理修复、备份/恢复互斥锁、download权限分离); **推荐引擎四层** (层0 MDL自动候选 + 层1 会话级Expansion/Follow-up + 层2 Catalog频率加权/权重自动调整 + 层3 协同过滤/偏好学习/意图趋势); **SSE流式** + **WebSocket逐chunk流** + **步骤进度推送**、**键盘快捷键与命令面板** (Ctrl+K)、**模型-数据源映射** (PropertyPanel 数据源选择器)、**虚拟滚动结果表格** (>100 行自动切换)。**NL2SQL 路由引擎**已实现完整 3 层路由 (direct_llm / fewshot_cot / decompose_merge)、问题分析 (tier/entities/metrics/dimensions/filters)、Schema 按需剪枝、Decompose & Merge 策略、GROUP BY 完整性校验、聚合一致性校验 (warn-only)、ROUTER_CONFIG 集中常量管理、LLM 重试循环 (带错误反馈) 和问题分析 LRU 缓存。**SSO/OIDC 完整集成** (OIDC discovery/authorize/callback/ID token 校验/nonce 防重放/redirect URI 白名单/claim→role 映射/email 碰撞检测/自动用户创建 — 30 个测试用例)。**跨源查询增强**: 谓词/投影下推、聚合下推、列血缘追踪、表达式 MASK 检测。**Desktop (Tauri 2.x 系统托盘+后端进程管理) + Mobile (Capacitor 4-tab 布局+只读视图) 双平台就绪。** 24 种语言 i18n、RTL 支持、Vega 与 ChartEditor 懒加载、loading.tsx 骨架屏全覆盖、TanStack Query 指数退避重试、无障碍 (a11y) 基础。本文档中标记为"规划"的能力尚未完全落地。

---

## 目录

1. [项目概述](#1-项目概述)
2. [架构设计](#2-架构设计)
3. [技术栈](#3-技术栈)
4. [项目目录结构](#4-项目目录结构)
5. [页面路由设计](#5-页面路由设计)
6. [组件树设计](#6-组件树设计)
7. [API 设计](#7-api-设计)
8. [数据流设计](#8-数据流设计)
9. [关键模块详细设计](#9-关键模块详细设计)
10. [多数据源架构](#10-多数据源架构)
11. [用户角色与权限管理子系统](#11-用户角色与权限管理子系统)
12. [多项目与 DuckDB 元数据存储](#12-多项目与-duckdb-元数据存储)
13. [系统设置](#13-系统设置)
14. [主动推荐引擎](#14-主动推荐引擎)
15. [多平台打包](#15-多平台打包)
16. [移动端界面](#16-移动端界面)
17. [与旧 wren-ui 架构对比](#17-与旧-wren-ui-架构对比)
18. [实现阶段规划](#18-实现阶段规划)
19. [评审确认记录](#19-评审确认记录)

---

## 1. 项目概述

### 1.1 定位

**PrismBI** 是基于 WrenAI 的最新 Open Context Engine 技术重塑的下一代 AI BI 工具。它将第二阶段 GenBI 应用（wren-ui）的功能完整保留，但架构上从**微服务编排模式**彻底转型为**引擎内核直连模式**，并扩展为多平台应用。

### 1.2 核心原则

| 原则 | 说明 |
|------|------|
| **前端功能全保留** | 建模画布、问答线程、Dashboard、知识库、设置向导等 UI 功能 100% 保留 |
| **后端极致精简** | 取消 Apollo GraphQL + SQLite + 5 微服务的复杂架构，直连 wren-engine |
| **引擎能力最大化** | 充分利用 wren-core-py 的 plan+exec 一体化、LanceDB 嵌入式内存层、Skills 框架 |
| **多项目原生支持** | DuckDB 持久化项目元数据，支持创建/切换/导入/导出 |
| **多数据源** | 系统级注册 + 项目级绑定 (多对多), 跨源透明查询 |
| **多平台覆盖** | Desktop (Win/Mac/Linux) + Mobile (Android/iOS/Harmony) |
| **部署零摩擦** | `pip install wren-engine && npx prism-bi` 即可运行 |

### 1.3 目标形态

```
旧: 5 Docker 容器 + 1 Node.js BFF + 1 SQLite = 7 进程
新: 1 Python 后端进程 (wren-engine) + 1 Node.js 前端进程 = 2 进程
   + DuckDB 项目元数据 (嵌入式)
   + Tauri 桌面壳 / Capacitor 移动壳
```

---

## 2. 架构设计

### 2.1 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     PrismBI Desktop App (Tauri)                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                   PrismBI Frontend                          │  │
│  │              (Next.js 16 + React 19)                        │  │
│  │                                                             │  │
│  │  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────┐ ┌─────┐│  │
│  │  │ 建模画布│ │ 问答线程  │ │Dashboard │ │ 知识库 │ │ 设置  │ │管理 ││  │
│  │  │(React  │ │(WebSocket│ │(Grid布局) │ │(管理页) │ │(品牌/ │ │后台 ││  │
│  │  │ Flow)  │ │ + SSE)   │ │          │ │        │ │ 主题) │ │Admin││  │
│  │  └───┬────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ └──┬───┘ └──┬──┘│  │
│  │      └───────────┴────────────┴────────────┴─────────┴────────┘   │  │
│  │                          │ REST + WebSocket                 │  │
│  └──────────────────────────┼─────────────────────────────────┘  │
└─────────────────────────────┼───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                    PrismBI Backend (FastAPI)                     │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                 WrenEngine SDK (Python)                   │    │
│  │  ┌────────────┐  ┌────────────┐  ┌──────────────────┐  │    │
│  │  │ SQL 规划器  │  │ CTE 重写器  │  │ Connector(22+源) │  │    │
│  │  │(wren-core) │  │(Python层)  │  │ (Ibis)           │  │    │
│  │  └────────────┘  └────────────┘  └──────────────────┘  │    │
│  │  ┌────────────┐  ┌────────────┐                        │    │
│  │  │ Memory层   │  │ Skills框架  │                        │    │
│  │  │(LanceDB)   │  │ (LLM指令集) │                        │    │
│  │  └────────────┘  └────────────┘                        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │            DuckDB: 统一元数据 + 临时数据引擎              │    │
│  │                                                          │    │
│  │  metadata/  ← 永久 Schema                                │    │
│  │  ├── users, projects, datasources, project_datasources  │    │
│  │  ├── model_datasource_mappings                          │    │
│  │  ├── threads, thread_responses, dashboards, dashboard_items │    │
│  │  ├── instructions, sql_pairs, api_history, settings     │    │
│  │  ├── api_tokens                                         │    │
│  │  ├── roles, user_roles, permissions                     │    │
│  │  ├── role_permissions, user_permission_overrides        │    │
│  │  ├── row_level_security_policies                        │    │
│  │  ├── column_level_security_policies                     │    │
│  │  ├── audit_logs                                         │    │
│  │  ├── recommended_questions_cache                        │    │
│  │  ├── question_sql_catalog                                │    │
│  │  ├── user_preference_hints                               │    │
│  │  ├── interest_clusters                                   │    │
│  │  ├── recommendation_scores                               │    │
│  │  ├── recommendation_feedback                             │    │
│  │  └── layer_weight_history                                │    │
│  │                                                          │    │
│  │  cache/     ← TTL Schema (Dashboard 数据缓存)            │    │
│  │                                                          │    │
│  │  temp_<session>/  ← 临时 Schema (跨源查询中间数据)        │    │
│  │                                                          │    │
│  │  system/       ← 系统内部 Schema (迁移记录等)             │    │
│  │                                                          │    │
│  │  导出/导入: YAML / JSON / CSV / SQLite                   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │             推荐引擎 (Recommendation Engine)              │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐ │    │
│  │  │ 层0: Schema  │ │ 层1: 会话级  │ │ 层2: 项目级      │ │    │
│  │  │ 驱动推荐     │ │ Expansion/  │ │ 热门查询/兴趣聚类 │ │    │
│  │  │ (MDL语义模型)│ │ Follow-up   │ │ /自学习Catalog   │ │    │
│  │  └──────────────┘ └──────────────┘ └──────────────────┘ │    │
│  │  ┌──────────────────────────────────────────────────┐   │    │
│  │  │ 层3: 全局级 (协同过滤/偏好学习/意图趋势/模型优化)    │   │    │
│  │  └──────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              跨源查询引擎 (Cross-Source Engine)           │    │
│  │                                                          │    │
│  │  1. SQL 分析: 识别 SQL 中每个表所属的数据源               │    │
│  │  2. 分源提取: 为每个数据源生成独立子查询                   │    │
│  │  3. 并行执行: 同时查询多个数据源                          │    │
│  │  4. 结果入库: 各源结果存入 DuckDB temp_<session>/        │    │
│  │  5. SQL 重写: 原 SQL → DuckDB 方言, 引用临时表            │    │
│  │  6. 合并执行: DuckDB 本地 JOIN 返回结果                   │    │
│  │  7. 清理: 会话/TTL 策略删除临时数据                       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└────────────┬──────────────┬──────────────┬──────────────────────┘
             │              │              │
             ▼              ▼              ▼
   Data Source A     Data Source B     Data Source C
   (PostgreSQL)      (ClickHouse)      (BigQuery)
        │                 │                │
        └─────────────────┴────────────────┘
                          │
                   22+ 数据库/数据仓库/文件源
```

### 2.2 与旧架构的核心差异

| 维度 | 旧 wren-ui 架构 | PrismBI 架构 |
|------|----------------|--------------|
| **BFF 层** | Apollo GraphQL Server (resolver→service→repository→adaptor) | FastAPI REST + WebSocket (直接调用 wren-engine SDK) |
| **数据存储** | SQLite (项目元数据) + Qdrant (向量) | DuckDB (项目元数据) + LanceDB (向量, 嵌入式) |
| **SQL 引擎** | wren-engine (Java/Rust) + ibis-server (Python) 两个服务 | wren-core-py 一个引擎 (plan + execute) |
| **AI 管道** | wren-ai-service (FastAPI + Haystack + Qdrant + 自建 RAG) | Skills 框架 (标准化指令模式, 完全替代旧管道) |
| **认证** | JWT + bcrypt + SQLite 用户表 | JWT + bcrypt + DuckDB 用户表 (保留并增强) |
| **通信** | GraphQL over HTTP | REST + WebSocket (优先) + SSE (流式备选) |
| **编辑器** | react-ace | CodeMirror 6 (更轻量, ~1MB vs ~5MB) |
| **UI 框架** | Ant Design 4 + styled-components | 优选轻量方案 + Tailwind CSS 4 |
| **项目** | 单项目, 无原生多项目支持 | 多项目原生支持, DuckDB 持久化, 可导出 |
| **平台** | 仅 Web | Desktop (Tauri) + Mobile (Capacitor) + Web |

---

## 3. 技术栈

### 3.1 前端 (Node.js 24+)

| 技术 | 版本 | 用途 |
|------|------|------|
| Node.js | 24 LTS | 运行时 |
| Next.js | 16.x | React 框架 (App Router, 输出静态/Standalone) |
| React | 19.x | UI 框架 |
| TypeScript | 6.x | 类型安全 |
| Tailwind CSS | 4.x | 原子化样式 (替代 styled-components) |
| UI 组件库 | **待定评审** | 优选更轻量的方案 (如 shadcn/ui 或 Park UI) 替代 Ant Design |
| React Flow | 12.x | 建模关系图画布 |
| Vega-Lite | 6.x | 图表渲染 |
| react-vega | 8.x | React 图表渲染封装 |
| CodeMirror 6 | latest | SQL/JSON 编辑器 (替代 Monaco, 体积减 80%) |
| Zustand | 5.x | 全局状态管理 |
| TanStack Query | 5.x | 服务端状态/缓存 (Server State) |
| ws (原生 WebSocket) | latest | WebSocket 客户端 |
| react-markdown | latest | Markdown 渲染 |
| react-grid-layout | latest | Dashboard 网格布局 |
| driver.js | latest | 用户引导 (具体引导流程待设计) |

### 3.2 桌面端 (Tauri)

| 技术 | 用途 |
|------|------|
| Tauri 2.x | Rust 驱动的桌面壳 (比 Electron 小 10x) |
| Rust (系统内置) | Tauri 后端, 与 wren-core 共享 Rust 生态 |
| tauri-plugin-shell | 本地 Python 进程管理 |
| tauri-plugin-fs | 文件系统访问 (导出/导入) |
| tauri-plugin-dialog | 原生对话框 |

### 3.3 移动端 (Capacitor)

| 技术 | 用途 |
|------|------|
| Capacitor 7.x | Web → 移动 App 壳 |
| @capacitor/android | Android 打包 |
| @capacitor/ios | iOS 打包 |
| @capacitor/core | 原生 API 桥接 |

### 3.4 后端 (Python 3.12+)

| 技术 | 用途 |
|------|------|
| FastAPI | REST API + WebSocket 框架 |
| wren-engine | 核心引擎 SDK (`pip install wren-engine`) |
| wren-core | Rust 语义引擎 (PyO3 绑定) |
| LanceDB | 嵌入式向量存储 (语义搜索 + 历史召回) |
| DuckDB | 项目元数据存储 (嵌入式 OLAP) |
| sqlglot | SQL 解析/转译 |
| PyArrow | 结果集格式 |
| python-jose | JWT 令牌 |
| bcrypt / passlib | 密码哈希 |
| websockets | WebSocket 支持 |
| cryptography (fernet) | 连接凭据加密存储 |
| sentence-transformers | 推荐引擎语义相似度 (可替换为 LanceDB 内置) |

### 3.5 去除的技术（不再使用）

| 技术 | 原因 |
|------|------|
| Apollo Server/Client | GraphQL → REST + WebSocket |
| Knex/SQLite | → DuckDB (更强的分析能力 + 导出能力) |
| styled-components | → Tailwind CSS (更轻量, SSR 友好) |
| react-ace / Monaco | → CodeMirror 6 (1MB vs 5MB) |
| Haystack / Qdrant | → Skills 框架 + LanceDB (完全替代) |
| PostHog | 可选 telemetry, 不在核心中内置 |

---

## 4. 项目目录结构

```
PrismBI/
├── DESIGN.md                       # 本文档
├── README.md                       # 项目说明
│
├── frontend/                       # 前端工程 (Web + Desktop + Mobile)
│   ├── package.json                # Node 依赖
│   ├── tsconfig.json               # TypeScript 配置
│   ├── next.config.ts              # Next.js 配置
│   ├── tailwind.config.ts          # Tailwind CSS 配置
│   ├── eslint.config.mjs           # ESLint 配置
│   │
│   ├── src/
│   │   ├── app/                    # Next.js App Router
│   │   │   ├── layout.tsx          # 根布局 (Providers)
│   │   │   ├── page.tsx            # / → 重定向
│   │   │   ├── login/              # /login
│   │   │   ├── home/               # /home (问答)
│   │   │   │   ├── page.tsx        # 问答首页
│   │   │   │   ├── [threadId]/
│   │   │   │   └── dashboard/      # Dashboard
│   │   │   ├── modeling/           # /modeling
│   │   │   ├── knowledge/          # /knowledge
│   │   │   │   ├── instructions/
│   │   │   │   └── question-sql-pairs/
│   │   │   ├── api-management/
│   │   │   │   └── history/
│   │   │   ├── setup/              # /setup
│   │   │   │   ├── connection/
│   │   │   │   ├── models/
│   │   │   │   └── relationships/
│   │   │   ├── settings/           # /settings (系统设置)
│   │   │   │   ├── page.tsx        # 设置首页
│   │   │   │   ├── datasources/    # 数据源管理
│   │   │   │   ├── recommendations/ # 推荐引擎管理页
│   │   │   │   │   └── scores/     # 评分历史分析
│   │   │   ├── admin/              # /admin (权限管理)
│   │   │   │   ├── users/          # 用户管理
│   │   │   │   ├── roles/          # 角色管理
│   │   │   │   └── audit/          # 审计日志
│   │   │   └── projects/
│   │   │       └── [id]/
│   │   │           └── settings/   # /projects/:id/settings
│   │   │
│   │   ├── components/
│   │   │   ├── ui/                 # 基础 UI 组件
│   │   │   ├── layouts/            # AppShell, Sidebar, Header
│   │   │   ├── diagram/            # 建模画布 (ReactFlow)
│   │   │   ├── home/               # 问答线程组件
│   │   │   ├── dashboard/          # Dashboard 组件
│   │   │   ├── modeling/           # 建模编辑组件
│   │   │   ├── knowledge/          # 知识库组件
│   │   │   ├── modals/             # 业务弹窗
│   │   │   ├── chart/              # 图表组件
│   │   │   ├── editor/             # CodeMirror 6 编辑器
│   │   │   ├── settings/           # 系统设置组件
│   │   │   ├── recommendation/     # 推荐引擎组件 (问题列表/评分器/Catalog/Hints)
│   │   │   ├── admin/              # 权限管理组件 (用户/角色/审计)
│   │   │   └── mobile/             # 移动端专用组件
│   │   │
│   │   ├── hooks/                  # 自定义 Hooks
│   │   │   ├── useWebSocket.ts     # WebSocket 连接管理 (含心跳/重连/状态)
│   │   │   ├── useSSE.ts           # SSE 降级连接 (含自动降级/恢复探测)
│   │   │   ├── useRecommendations.ts # 推荐引擎 Hook (触发/刷新/评分)
│   │   │   ├── useAuth.ts          # 认证状态 + Token 管理
│   │   │   ├── useProject.ts       # 当前项目上下文
│   │   │   ├── useDebouncedValue.ts # 防抖 (输入框联动搜索)
│   │   │   └── useVirtualScroll.ts  # 虚拟滚动 (大列表/表格)
│   │   ├── stores/                 # Zustand 状态管理
│   │   │   ├── authStore.ts        # 用户认证 + 权限缓存
│   │   │   ├── projectStore.ts     # 当前项目 + 项目列表
│   │   │   ├── threadStore.ts      # 当前线程 + 流式内容累积
│   │   │   ├── modelingStore.ts    # 画布数据 + 选中节点 + undo/redo
│   │   │   ├── themeStore.ts       # 主题/布局偏好 (持久化 localStorage)
│   │   │   └── recommendationStore.ts # 推荐列表 + 评分状态
│   │   ├── lib/                    # 工具函数
│   │   │   ├── api.ts              # Axios/fetch 封装 + 拦截器
│   │   │   ├── ws.ts               # WebSocket 客户端工厂
│   │   │   ├── sse.ts              # SSE 客户端
│   │   │   └── utils.ts            # 通用工具函数
│   │   └── styles/                 # 全局样式
│   │
│   └── public/                     # 静态资源 (含默认 Logo)
│
├── src-tauri/                      # Tauri 桌面壳
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── src/
│   │   ├── main.rs
│   │   ├── commands.rs             # Tauri 命令 (启动 Python 后端等)
│   │   └── tray.rs                 # 系统托盘
│   ├── icons/                      # 应用图标
│   └── capabilities/
│
├── android/                        # Capacitor Android (移动端)
│   └── app/
│       └── src/main/java/
│
├── ios/                            # Capacitor iOS (移动端)
│
├── backend/                        # Python 后端
│   ├── pyproject.toml
│   ├── main.py                     # FastAPI 入口 (含 WebSocket)
│   ├── routers/
│   │   ├── query.py                # SQL 执行
│   │   ├── ask.py                  # NL→SQL + WebSocket/SSE
│   │   ├── models.py               # 模型 CRUD
│   │   ├── projects.py             # 项目管理
│   │   ├── settings.py             # 系统设置
│   │   ├── exports.py              # 导出/导入
│   │   ├── ws.py                   # WebSocket 端点
│   │   ├── datasources.py          # 数据源两级管理
│   │   ├── cross_source_query.py   # 跨源查询引擎
│   │   ├── knowledge.py            # 知识库
│   │   ├── recommendations.py      # 推荐引擎 API
│   │   ├── ratings.py              # 评分反馈 API
│   │   └── cleanup.py              # 临时数据清理 API
│   ├── services/
│   │   ├── engine_service.py       # wren-engine 封装
│   │   ├── project_service.py      # 项目管理 (DuckDB)
│   │   ├── memory_service.py       # LanceDB 内存层
│   │   ├── export_service.py       # 导出/导入 (YAML/JSON/CSV)
│   │   ├── auth_service.py         # JWT 认证
│   │   ├── datasource_service.py   # 数据源两级管理
│   │   ├── cross_source_service.py # 跨源查询 + DuckDB 临时表管理
│   │   ├── settings_service.py     # 系统设置
│   │   ├── recommendation_service.py # 推荐引擎管线 (4 层)
│   │   ├── rating_service.py       # 评分反馈闭环逻辑
│   │   └── cleanup_service.py      # 临时数据清理 + TTL 管理
│   ├── models/
│   │   └── schemas.py              # Pydantic Schema
│   └── db/
│       ├── init.py                 # DuckDB 初始化 + 迁移
│       ├── migrations/             # 数据库迁移
│       └── repositories/           # 数据访问层
│
└── scripts/
    ├── dev.sh                      # 开发环境启动
    ├── build-desktop.sh            # 构建 Desktop
    ├── build-mobile.sh             # 构建 Mobile
    └── export-project.sh           # 导出项目脚本
```

---

## 5. 页面路由设计

### 5.1 路由表

| 路径 | 页面 | 对应旧 wren-ui | 功能说明 |
|------|------|----------------|----------|
| `/` | 重定向 | `index.tsx` | 根据项目状态跳转 |
| `/login` | 登录页 | `login.tsx` | 登录 |
| `/home` | 问答首页 | `home/index.tsx` | 推荐问题 + 输入框 |
| `/home/[threadId]` | 对话线程 | `home/[id].tsx` | 问答线程详情 |
| `/home/dashboard` | Dashboard | `home/dashboard.tsx` | 图表面板集合 |
| `/modeling` | 建模画布 | `modeling.tsx` | 模型/视图/关系编辑 |
| `/knowledge/instructions` | 指令管理 | `knowledge/instructions.tsx` | 业务指令 CRUD |
| `/knowledge/question-sql-pairs` | SQL 问答对 | `knowledge/question-sql-pairs.tsx` | SQL Pair CRUD |
| `/api-management/history` | API 历史 | `api-management/history.tsx` | API 调用记录 |
| `/setup/connection` | 项目创建向导 | `setup/connection.tsx` | 当前主实现: Sample/Manual 选择、数据源草稿、选表、关系、完成创建 |
| `/setup/models` | 模型选择 | `setup/models.tsx` | 兼容/保留路由, 主流程已收敛到 `/setup/connection` |
| `/setup/relationships` | 关系定义 | `setup/relationships.tsx` | 兼容/保留路由, 主流程已收敛到 `/setup/connection` |
| `/settings` | **新增** | — | 系统设置 (品牌/主题/数据源/LLM/导出/推荐等) |
| `/settings/datasources` | **新增** | — | 系统数据源管理 (列表/添加/编辑) |
| `/settings/recommendations` | **新增** | — | 推荐引擎管理 (Catalog 查看/来源权重/Hints 管理/评分统计) |
| `/settings/recommendations/scores` | **新增** | — | 评分历史分析和权重调整记录 |
| `/admin/users` | **新增** | — | 用户管理 (列表/创建/编辑/禁用) |
| `/admin/roles` | **新增** | — | 角色管理 (角色 CRUD / 权限矩阵配置) |
| `/admin/audit` | **新增** | — | 审计日志 (操作记录/筛选/导出) |
| `/admin/backup` | **新增** | — | 备份与恢复 (创建/下载/恢复/删除) |
| `/admin/sso` | **新增** | — | SSO/OIDC 配置页面 (provider/issuer/client_id/client_secret/mapping_rules/enabled) |
| `/admin/security-policies` | **新增** | — | RLS/CLS 安全策略管理 (按项目/角色筛选 + CRUD) |
| `/admin/users/:id` | **规划** | — | 用户详情 (角色分配, 登录历史) 尚未单独落页 |
| `/admin/roles/:id` | **规划** | — | 角色详情 (权限矩阵, 成员列表) 尚未单独落页 |
| `/projects/:id/settings` | **新增** | — | 项目设置 (绑定数据源/成员/RBAC 角色分配) |
| `/projects/:id/members` | **规划** | — | 当前未单独落页, 已并入 `/projects/:id/settings` 的 Members Tab |
| `/settings/profile` | **新增** | — | 个人资料, 修改密码, API Token |
| `/settings/profile/sessions` | **新增** | — | 活跃会话管理 |

#### 5.1.1 当前实现说明

| 模块 | 当前状态 | 代码位置 |
|------|----------|----------|
| 创建项目向导 | 已实现统一页面。Sample 与 Manual 相互隔离; Sample 支持 `hr/music/ecommerce/nba` 多选; Manual 数据源以弹窗草稿方式添加/编辑/删除, 不跳页 | `frontend/src/app/setup/connection/page.tsx` |
| Sample 关系 | 已从 wren-ui sample 真值数据同步, Step3 按 Step2 选表过滤并默认选中 | `frontend/src/lib/sampleRelations.ts` |
| 当前/默认项目 | 完成创建后调用 switch API, 后端同步 `users.default_project_id`, 前端 store 同步当前项目 | `backend/routers/projects.py`, `frontend/src/stores/projectStore.ts` |
| Admin RBAC | 用户/角色/权限/审计基础闭环已实现, 后端接口已做权限校验, 前端按权限显示入口和按钮 | `backend/routers/admin.py`, `backend/routers/auth.py`, `frontend/src/app/admin/*` |

### 5.2 移动端路由 (手机/Pad)

> 当前实现采用 **同一路由 + MobileLayout 自适应导航**，未单独提供 `/mobile/*` 前缀路由。

| 路径 | 移动端表现 | 限制 |
|------|------------|------|
| `/home` | 问答首页 (Bottom Tab: Home) | 可提问 |
| `/home/[threadId]` | 对话线程 + CompactPromptBar | 可提问 |
| `/home/dashboard` | Dashboard 列表 (Bottom Tab: Dashboard) | 可查看, 图表可交互 |
| `/settings/profile` | 个人资料 / API Token (Bottom Tab: Settings) | 账户管理 |
| `/knowledge/*`、`/modeling`、`/projects*` | 通过 Bottom Tab 的 More 面板进入 | 建模编辑能力受权限控制 |

### 5.3 布局层次

```
Desktop:
┌─────────────────────────────────────────────────────┐
│ AppShell                                             │
│ ┌──────────┐  ┌──────────────────────────────────┐  │
│ │          │  │  HeaderBar                        │  │
│ │  Sidebar │ │  [Logo] [项目名]  [设置] [头像↘]  │  │
│ │          │  ├──────────────────────────────────┤  │
│ │  - Home  │  │                                  │  │
│ │  - Dash  │  │         Content                   │  │
│ │  - Model │  │         (Page Content)             │  │
│ │  - Know  │  │                                  │  │
│ │  - API   │  │                                  │  │
│ │  - Admin │  │                                  │  │
│ │  ═══════ │  │                                  │  │
│ │  ⚙ Sett. │  │                                  │  │
│ └──────────┘  └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘

Mobile:
┌─────────────────┐
│ TopNav           │
│ [←] PrismBI [≡] │
├─────────────────┤
│                 │
│   Content       │
│   (全屏内容)     │
│                 │
├─────────────────┤
│ BottomTab        │
│ Home│Dash│Profile│
└─────────────────┘
```

---

## 6. 组件树设计

### 6.1 组件分层

```
src/components/
├── ui/                  # 通用 UI 原子组件
│   ├── Button.tsx
│   ├── Card.tsx
│   ├── Modal.tsx
│   ├── Drawer.tsx
│   ├── Table.tsx
│   ├── Tabs.tsx
│   ├── Tag.tsx
│   ├── Input.tsx
│   ├── Select.tsx
│   ├── Form.tsx
│   ├── Skeleton.tsx          # 骨架屏 (加载态)
│   ├── EmptyState.tsx        # 空状态 (含引导 action)
│   ├── ErrorBoundary.tsx     # 错误边界 (兜底 fallback)
│   ├── ErrorToast.tsx        # 错误提示 Toast
│   ├── Toast.tsx             # 全局轻提示 (success/warning/error/info)
│   ├── ConfirmDialog.tsx     # 确认弹窗
│   └── ...
├── layouts/
│   ├── AppShell.tsx         # 全局壳
│   ├── Sidebar.tsx          # 导航侧栏
│   ├── Header.tsx           # 顶栏
│   └── (移动端由 AppShell + MobileLayout 自适应承载)
├── diagram/
│   ├── Canvas.tsx           # ReactFlow 画布
│   ├── ModelNode.tsx        # 模型节点
│   ├── ViewNode.tsx         # 视图节点
│   ├── RelationEdge.tsx     # 关系边
│   └── DiagramContext.tsx   # 画布上下文
├── recommendation/          # 推荐引擎 UI
│   ├── RecommendedQuestions.tsx   # 推荐问题列表 (问答页下方)
│   ├── RecommendationCard.tsx     # 单条推荐 (含分类标签 + 来源说明 + 评分器)
│   ├── StarRating.tsx             # 1-5 星评分器组件 (可点击/只读两种模式)
│   ├── ScoreHistory.tsx           # 评分历史查看 (用户可见自己的评分记录)
│   ├── OnboardingQuestions.tsx    # 新用户引导推荐
│   ├── CatalogManager.tsx         # 自学习 Catalog 管理界面
│   ├── HintEditor.tsx             # 用户偏好 Hints 编辑
│   └── RecommenderSettings.tsx    # 推荐引擎设置 (层权重/来源开关)
├── home/
│   ├── PromptInput.tsx      # 问题输入
│   ├── ThreadList.tsx       # 线程列表
│   ├── ResponseCard.tsx     # 回答卡片
│   ├── StreamContent.tsx    # 流式内容
│   └── BreakdownStep.tsx    # SQL 分解
├── dashboard/
│   ├── DashboardGrid.tsx    # Grid 布局
│   ├── DashboardItem.tsx    # 面板
│   ├── CacheSettings.tsx    # 缓存设置
│   └── EmptyDashboard.tsx
├── modeling/
│   ├── MetadataDrawer.tsx
│   ├── ModelDrawer.tsx
│   ├── RelationModal.tsx
│   └── FieldTable.tsx
├── knowledge/                # 知识库组件
│   ├── InstructionList.tsx   # 指令列表
│   ├── InstructionForm.tsx   # 指令创建/编辑表单
│   ├── SqlPairList.tsx       # SQL 问答对列表
│   ├── SqlPairForm.tsx       # SQL 问答对创建/编辑表单
│   ├── KnowledgeCard.tsx     # 知识库条目卡片
│   └── KnowledgeSearch.tsx   # 知识库搜索过滤
├── editor/                  # CodeMirror 6 封装
│   ├── SQLEditor.tsx
│   └── JSONEditor.tsx
├── chart/
│   ├── VegaChart.tsx
│   └── ChartPicker.tsx
├── admin/                    # 权限管理 UI
│   ├── UserTable.tsx         # 用户列表 (含搜索/分页/批量操作)
│   ├── UserFormDrawer.tsx    # 用户创建/编辑抽屉
│   ├── RoleTable.tsx         # 角色列表
│   ├── RoleFormDrawer.tsx    # 角色创建/编辑 (含权限树)
│   ├── PermissionMatrix.tsx  # 权限矩阵 (角色×功能 可视化/编辑)
│   ├── AuditLogTable.tsx     # 审计日志 (筛选/详情/导出)
│   ├── TokenManager.tsx      # API Token 管理 (创建/撤销/查看)
│   └── DataSourceTestButton.tsx  # 数据源测试连接按钮 (含状态/日志)
├── settings/
│   ├── BrandingSettings.tsx    # 品牌设置
│   ├── ThemeSettings.tsx       # 主题设置
│   ├── DataSourceSettings.tsx  # 数据源管理 (系统级)
│   ├── LLMSettings.tsx         # LLM 配置
│   ├── RecommenderSettings.tsx # 推荐引擎设置 (与 recommendation/ 共享)
│   ├── ExportSettings.tsx      # 导出设置
│   ├── GeneralSettings.tsx     # 通用设置
│   └── AboutPage.tsx           # 关于/版本信息
├── mobile/                   # 移动端专用
│   ├── MobileLayout.tsx
│   ├── BottomSheet.tsx
│   ├── PullToRefresh.tsx
│   ├── CompactPromptBar.tsx
│   ├── MobileChartViewer.tsx
│   ├── MobileLogin.tsx
│   ├── MobileProfile.tsx
│   ├── ReadOnlyModelViewer.tsx
│   ├── ThreadCard.tsx
│   └── CompactRecommendation.tsx
└── modals/
    ├── CalculatedFieldModal.tsx
    ├── SaveAsViewModal.tsx
    ├── AdjustSQLModal.tsx
    └── ProjectSwitchModal.tsx
```

### 6.2 组件 Props 约定

组件采用 TypeScript 接口定义 Props, 遵循以下约定:

```
// 通用原子组件
interface ButtonProps {
  variant: 'primary' | 'secondary' | 'ghost' | 'danger';
  size: 'sm' | 'md' | 'lg';
  loading?: boolean;       /** @required 异步操作时必传 */
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}

interface SelectProps<T> {
  value: T;
  options: { label: string; value: T }[];
  onChange: (value: T) => void;
  placeholder?: string;
  searchable?: boolean;
}

// 业务组件
interface ModelNodeProps {
  model: { id: number; name: string; displayName: string; columns: ColumnDef[] };
  selected: boolean;
  onDragStart: (modelId: number) => void;
  onContextMenu: (ev: React.MouseEvent, modelId: number) => void;
}

interface ResponseCardProps {
  response: {
    id: number;
    question: string;
    sql: string;
    columns: string[];
    rows: Record<string, unknown>[];
    chartSpec?: VegaLiteSpec;
    summary?: string;
  };
  onChartTypeChange: (type: ChartType) => void;
  onRegenerate: () => void;
  onRate: (score: number) => void;
}

// Store 类型 (Zustand)
interface AuthStore {
  user: User | null;
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

interface ModelingStore {
  models: ModelDef[];
  views: ViewDef[];
  relations: RelationDef[];
  selectedNodeId: string | null;
  undoStack: Command[];
  redoStack: Command[];
  addModel: (model: ModelDef) => void;
  removeModel: (id: number) => void;
  connect: (source: string, target: string) => void;
  undo: () => void;
  redo: () => void;
}
```

> 注: 以上接口为示意性定义, Phase 1 实现时需在此基础上细化。

---

## 7. API 设计

### 7.1 设计原则

- 完全消除 GraphQL, 改为 RESTful + WebSocket
- WebSocket 通道用于实时通信 (问答流式, 任务状态推送)
- SSE 保留作为备选降级方案
- 统一响应格式 `{ data, error }` (以下接口清单展示 `data` 部分内容)
  - 成功: `{ data: <接口返回体>, error: null }`
  - 失败: `{ data: null, error: { code: "ERROR_CODE", message: "描述", details?: any } }`
- **安全传输**: REST API 强制 HTTPS, WebSocket 强制 WSS; 开发环境可降级
- **跨域**: 仅允许前端来源 (配置化 CORS whitelist), 生产环境不可通配
- **速率限制**: 各 API 端点按用户/项目粒度限流 (默认 100 req/min), 超限返回 `429 Too Many Requests`
- **请求 ID**: 每个请求带 `X-Request-Id` 追踪链路
- **认证**: 默认所有端点需认证 (通过 `Authorization: Bearer <JWT>` 头传递); `(public)` 标注的端点除外
- **WebSocket 安全**: 优先使用 `/api/auth/ws-ticket` 的短期 ticket (默认 30s); 同时兼容 Authorization/query/subprotocol/cookie 传 JWT 或 API token
- **查询参数风格**: 当前后端以 **snake_case** 为主 (如 `?project_id`, `?page_size`, `?include_route_dimensions`); 部分历史入口兼容 camelCase
- **常见错误码**:
  - `200` 成功 | `400` 请求参数错误 | `401` 未认证/令牌过期
  - `403` 权限不足 | `404` 资源不存在 | `422` 业务校验失败
  - `429` 请求过频 | `500` 服务端内部错误 | `502` 引擎不可达或超时

### 7.2 接口清单

#### 认证

```
(public) POST /api/auth/login       { username, password } → { token, user }
(public) POST /api/auth/register    { username, password, display_name } → { user }
GET  /api/auth/me          → { user }
POST /api/auth/refresh     → { token }
GET  /api/auth/sso/login    → 重定向到 OIDC Provider
GET  /api/auth/sso/callback → OIDC 回调 (校验 state/nonce, 创建本地会话)
POST /api/auth/sso/token    → code/id_token 交换为 PrismBI JWT
GET  /api/auth/sso/cookie-token → 读取 SSO 回跳短期 cookie token
POST /api/auth/ws-ticket    → 获取短期 WS ticket (默认 30s)
```

#### 用户管理 (管理员)

```
GET    /api/admin/users                     → 用户列表 (支持 ?search, ?status, ?role, &page, &page_size)
POST   /api/admin/users                     → 创建用户 { username, password, display_name, email, status } → { id }
GET    /api/admin/users/:id                 → 用户详情 (含角色分配)
PUT    /api/admin/users/:id                 → 更新用户 { display_name, email, status }
POST   /api/admin/users/:id/reset-password  → 管理员重置密码 { new_password }
POST   /api/admin/users/:id/deactivate      → 禁用用户 (status=INACTIVE)
DELETE /api/admin/users/:id                 → 永久删除用户
POST   /api/admin/users/:id/roles           → 分配角色 { role_id, project_id?, expires_at? }
DELETE /api/admin/users/:id/roles/:role_id  → 移除角色

GET    /api/admin/roles                     → 角色列表 (?scope=SYSTEM|PROJECT)
POST   /api/admin/roles                     → 创建角色 { name, scope, description, permissions[] } → { id }
GET    /api/admin/roles/:id                 → 角色详情 (含权限矩阵, 成员列表)
PUT    /api/admin/roles/:id                 → 更新角色 { name?, description?, permissions? }
DELETE /api/admin/roles/:id                 → 删除角色 (检查是否被用户引用)

GET    /api/admin/permissions               → 功能权限定义列表 (供权限矩阵配置)
PUT    /api/admin/roles/:id/permissions     → 批量更新角色权限 { permission_ids[] } → { success }

GET    /api/admin/audit-logs                → 审计日志 (?event_type, ?user_id, ?from, ?to, &page, &page_size)
POST   /api/admin/audit-logs/export         → 导出审计日志 { format: "csv"|"json" } → 文件下载

GET    /api/admin/sso                       → SSO 配置
PUT    /api/admin/sso                       → 更新 SSO 配置 { provider, client_id, client_secret, issuer_url, mapping_rules, enabled }

GET    /api/admin/security-policies/rls              → RLS 策略列表 (?project_id, ?role_id)
POST   /api/admin/security-policies/rls              → 创建 RLS
PUT    /api/admin/security-policies/rls/:id          → 更新 RLS
DELETE /api/admin/security-policies/rls/:id          → 删除 RLS

GET    /api/admin/security-policies/cls              → CLS 策略列表 (?project_id, ?role_id)
POST   /api/admin/security-policies/cls              → 创建 CLS
PUT    /api/admin/security-policies/cls/:id          → 更新 CLS
DELETE /api/admin/security-policies/cls/:id          → 删除 CLS

GET    /api/admin/backups                            → 备份列表
POST   /api/admin/backups                            → 创建备份
GET    /api/admin/backups/:name                      → 备份详情
GET    /api/admin/backups/:name/download             → 下载备份
POST   /api/admin/backups/restore                    → 恢复备份
DELETE /api/admin/backups/:name                      → 删除备份
```

#### 个人设置与 API Token

```
GET    /api/profile                         → 当前用户资料
PUT    /api/profile                         → 更新资料 { display_name, email }
POST   /api/profile/change-password         → 修改密码 { old_password, new_password }
GET    /api/profile/tokens                  → API Token 列表
POST   /api/profile/tokens                  → 创建 Token { name, expires_at?, scope? } → { id, token }
POST   /api/profile/tokens/:id/revoke       → 吊销 Token (软删除, is_revoked=true)
GET    /api/profile/sessions                → 活跃会话
POST   /api/profile/sessions/:id/revoke     → 结束会话 (标记 is_revoked=true)
```

API Token 当前实现为 `Authorization: Bearer prismbi_...` 认证方式: 后端按 `token_prefix` 查候选 token, 用 bcrypt hash 校验完整 token, 校验 `expires_at/is_revoked/user.status`, 命中后更新 `last_used_at`。权限判断先套用用户/角色/RBAC, 再按 token scope 收窄; scope 支持 `resource:action`, `resource:*`, `resource:manage` 和 `*`。

#### 项目管理

```
GET    /api/projects                → 项目列表
POST   /api/projects                → 创建项目 { name, type, connection_info } → { id }
GET    /api/projects/:id            → 项目详情
PUT    /api/projects/:id            → 更新项目 { name?, display_name?, connection_info?, language? }
DELETE /api/projects/:id            → 删除项目
POST   /api/projects/:id/switch     → 切换当前项目
GET    /api/projects/:id/export     → 导出项目 (?format=yaml|json)
POST   /api/projects/import/file    → 导入项目 (文件上传) → { project_id }
POST   /api/projects/migrate/sqlite → 从旧 SQLite 迁移项目
```

#### 项目成员管理

```
GET    /api/projects/:id/members          → 项目成员列表
POST   /api/projects/:id/members          → 添加成员 { user_id, role_id, expires_at? }
PUT    /api/projects/:id/members/:member_id  → 修改成员角色 { role_id }
DELETE /api/projects/:id/members/:member_id  → 移除成员
```

#### SQL 执行

```
POST /api/query           { sql, project_id, limit?, dry_run? } → { columns, rows, total_rows }
POST /api/query/dry-plan  { sql, project_id } → { planned_sql, model_refs, security_plan }
GET  /api/query/metrics   ?project_id=...&include_route_dimensions=bool → 执行与路由观测指标
```

#### 自然语言→SQL (WebSocket 为主 + SSE 降级)

**WebSocket 主通道**:
```
WebSocket: /ws/ask (header/query/subprotocol/cookie 传 token 或 x-ws-ticket)
  → { type: "ping" }
  ← { type: "pong" }
  → { type: "ask", request_id, question, thread_id?, previous_questions?, language?, preview_row_limit?, temporary? }
  ← { type: "delta", content_type: "state", content: "running", request_id, seq, ts, elapsed_ms }
  ← { type: "delta", content_type: "step"|"text"|"sql", content, request_id, seq, ts, elapsed_ms }
  ← { type: "result", data: { thread_id, response, summary, sql }, request_id, seq, ts, elapsed_ms }
  ← { type: "error", message, request_id? }
```

当前 WebSocket 实现说明:
- `/ws/ask` 支持 `x-ws-ticket`、Authorization Bearer、query 参数、subprotocol 与 cookie 等多通道鉴权; 无效凭证直接关闭连接。
- 每条 `ask` 消息复用 REST Ask 的 project/thread 解析和 `models:read` 权限校验; temporary Ask 不写入数据库, 继续保持空项目临时语义。
- 前端 `/home/[threadId]` 已接入 `useWebSocket`: 连接可用时走 WebSocket Ask, 否则回落到 REST `/api/ask`; `frontend/src/lib/ws.ts` 支持 ping/pong、指数退避重连和多个 message handler。
- WebSocket 当前已支持步骤进度 (`delta.step`) + 文本分块 (`delta.text`) + SQL 分块 (`delta.sql`) 与 `seq/ts/elapsed_ms` 流式元数据。

**SSE 降级机制**:
```
触发条件:
  1. WebSocket 连接失败 (ws:// 不可达, 如企业防火墙限制)
  2. WebSocket 连接超时 (10s 内未建立连接)
  3. WebSocket 异常断开且重试 3 次失败
  4. 客户端网络环境不支持 WebSocket (如某些代理环境)

降级流程:
  客户端:
    WebSocket 连接 → 失败 → 标记 fallback=true
      → POST /api/ask/stream (SSE) 发起相同请求
      → 自动重试: 指数退避 (1s, 2s, 4s, max 3 次)
      → 全部失败 → 退化为 POST /api/ask (REST 非流式)

  服务端:
    检测到 SSE 请求 → 使用相同的 NL→SQL Pipeline
      → 将 WebSocket 消息事件转换为 SSE events
      → event: delta  data: {"content_type":"text","content":"..."}
      → event: state  data: {"state":"sql_ready","sql":"..."}
      → event: result data: {"sql":"...","summary":"..."}

  恢复策略:
    每 5 分钟探测一次 WebSocket 可用性
    一旦恢复 → 切换回 WebSocket 通道
```

REST 备选 (非流式, 兜底):
```
POST /api/ask             { question, thread_id, previous_questions? } → { sql, summary, thread_id, response }
```

当前实现状态:
- `/api/ask` 与 `/api/ask/stream` 已共用统一 Ask Service, `/api/ask/stream` 为 `text/event-stream` 真流式输出 (delta state/step/text + result, 含 request_id/seq/elapsed_ms)。
- Home 聊天 prompt 分为三层: 系统提示词(system prompt)、项目提示词(project prompt)、用户提示词(user prompt)。
- 无当前项目、打开空项目、或当前项目尚无数据源/模型上下文时, Home 使用 `系统提示词 + 用户提示词` 与 LLM 普通对话, response 写入 `answerDetail.content`。
- 打开非空项目时, Ask Service 先对项目元数据做轻量语义命中: 对模型/字段/关系的 `name/display_name/description/table_reference` 做 token 匹配和加权排序, 仅将命中的模型、字段描述和关系描述注入 `{{semantic_model}}`; 无命中时不再退回完整语义模型生成 SQL, 而是直接走项目上下文普通 LLM 回答。
- 语义命中已增加中英业务词同义词扩展, 用于把“订单/产品/销售/销量/销售额/城市/客户/卖家/表现/排行”等中文提问映射到英文模型名、字段名和描述, 避免 Sample/英文字段项目被误判为无 metadata 命中。
- 命中元数据后, Ask Service 会先做问题路由: 将可由项目元数据回答的 `metadata_question_part` 交给当前 SQL adapter 生成 SELECT/WITH SQL 并执行; 将 `non_metadata_question_part` 交给 LLM 补全; 最后把 SQL 结果、补全内容和原问题一起交给 LLM 生成最终回答。当前 SQL adapter 仍是 LLM 生成 SQL 的 fallback seam, 尚未直连 wren-engine。
- Ask Service 会同时从 DuckDB `metadata.instructions` 和 `metadata.sql_pairs` 做轻量文本检索, 对 instruction/question/description/category/scope 做 token overlap 和 priority 加权; 命中的 Instructions 与 Verified Question-SQL examples 会追加到路由与 SQL 生成 prompt。命中摘要写入 `answerDetail.knowledgeHits` 与 `askingTask.knowledgeHits`, 便于审计和前端展示。当前仍不接入 vector DB。
- SQL 执行成功后, 参考 wren-ui TextBasedAnswer 链路, Ask Service 先以 `question + sql + sqlData(columns/rows/total_rows)` 生成数据部分回答, 再与未命中元数据补全部分合成最终自然语言回答; 其中 `sqlData` 是唯一可用于数据事实、排行、对比和示例的来源, SQL 生成阶段 summary 不能作为事实来源。最终回答 prompt 明确禁止输出 SQL/JSON/code fence, 并按前端传入的界面语言/用户语言输出。SQL 生成 prompt 已要求只选择与问题直接相关或 join/filter/group/order 必需的字段, 避免 `SELECT *` 和无关列。
- 对 Sample 电商销售结果中常见的 `product_category + customer_city + item_count + order_count + total_sales + avg_item_price` 结果形态, Ask Service 会优先走确定性 Markdown 文本解释: 用“结论 / 产品汇总 / 城市表现”短章节和紧凑表格说明预览结果覆盖多少产品、多少城市、总出货量、订单数、销售额, 再按产品销售额排序并列出表现最好的城市及出货量、销售额、平均价格, 避免 LLM 只泛泛描述数据结构或输出业务无关铺垫。为保持可读性, 正文只展示 Top 产品和每个产品的 Top 城市, 完整明细留在 Result 数据视图和图表。
- 对任意项目和任意 SQL 结果, 最终回答增加通用防跑偏校验: 如果 LLM 输出包含反问/澄清/泛泛描述数据结构等模式, 或没有引用足够的返回字段和值, 则丢弃该 LLM 回答并降级为确定性结果解释。确定性解释会基于会话设置的预览数据量生成 Markdown 短章节、关键指标小表和少量代表性预览行, 保证 SQL 已返回数据时回答至少紧扣查询结果, 但避免列举过多数据导致可读性下降; 完整结果由 Result 视图承载。
- 每个聊天线程保存 `preview_row_limit`，默认 20，可在聊天输入栏设置为 10/20/50/100。若查询返回行数不超过该阈值, 回答可列举全部预览行; 超过阈值时只基于阈值内预览数据做摘要和代表性展示。Result Data view 与回答使用同一阈值展示预览。
- 空项目不创建任何持久化项目、线程、response 或项目级数据: 后端不再 seed `metadata.projects.id = 0`, 启动迁移会清理历史 `id=0` General Chat 项目及其 threads/responses/dashboard/modeling/knowledge/recommendation/binding 残留数据, 同时删除 `data/projects/0`。前端使用 `/home/temp-*` 与 `sessionStorage` 维护浏览器会话级临时线程, 调用 `/api/ask` 时携带 `temporary=true`; 后端仅用普通 LLM 回答并返回 wren-ui response shape, 但不写入数据库或项目文件。非 temporary ask、thread 创建、Dashboard/Knowledge/Modeling/Query/Recommendation/Datasource 项目级 API 都要求真实 `project_id > 0`。刷新同一浏览器标签页可恢复该临时会话, 关闭浏览器会话后丢弃。
- 线程标题规则: 新建线程默认标题为“新会话”; 首次或后续提问后, 若 `summary_manual=false`, 后端按最新用户问题自动生成简短标题并更新 `summary`; 用户在左侧线程列表双击或点击编辑按钮可手动改名, `PUT /api/threads/:id` 会写入 `summary_manual=true`, 之后系统不再自动覆盖该标题。Header 在 `/home/:threadId` 显示 `首页 >> <会话标题>`。
- 线程列表、线程详情、删除与 Ask 写入均按当前用户隔离; 删除线程时级联删除 responses, 前端同时移除对应 React Query 缓存并在当前线程被删除后跳回 `/home`, 避免已删除会话在下一次提问时被旧 `threadId` 或缓存重新带回。
- 项目下拉菜单按权限而非当前项目存在性控制: 空项目状态仍允许进入 Home 普通聊天; `New Project` 只依赖 `projects:create` 权限显示, `Project Settings`/`Delete Project` 仅在有当前项目且有对应权限时显示。
- Home 回答区参考 wren-ui 的 Answer / View SQL / Chart tab 组织方式, 已合并为同一个 Result panel 的 `Answer / Data / Chart / SQL` 四个选项卡, 避免回答、数据和 SQL 上下堆叠。Answer 渲染 Markdown 解释; Data 展示同一预览结果; SQL 支持 Adjust SQL 和重新执行, SQL 编辑框会随代码量自适应高度并在超过最大高度后滚动; Chart 基于当前 SQL 预览结果自动推断 Vega-Lite 图表, 优先覆盖时间序列、维度+指标柱状图、城市/产品分组以及双指标散点等常见行业 BI 形态。Chart tab 采用画布式布局, 去掉解释性文案, 图表自适应面板空间; 编辑、重置、固定到看板以半透明悬浮工具栏排列在右上角, 编辑面板以半透明浮层覆盖在图表上方, 不挤压图表本体。Chart tab 支持固定到看板、编辑图表类型、坐标字段、颜色/品类呈现、size、聚合、排序、标签角度、tooltip 和标题。底部操作按 tab 区分: Answer tab 显示 Re-run/Save as View/Save as Pair, Data/Chart/SQL tab 显示 Adjust SQL/Save as View/Save as Pair; 空项目临时线程不显示 Save as View/Save as Pair 等任何知识库/建模持久化入口。
- Home 页面内容区与线程左栏统一为圆角卡片; AppShell 内容外边距从原 `p-6` 收敛到约 20% 的 `p-1.5`, Home 线程左栏和右侧聊天卡片间距收敛到 `5px`, 提问/回答整体被封装在同一回答卡片中, 与左侧卡片边界对齐。
- 页面标题统一放在 Header, 使用 `父级 >> 子级/名称` 拼接: Home 线程、Dashboard 详情、Knowledge 子页、系统设置子页、系统管理子页、项目设置均不再在右侧内容区重复显示模块标题。Home、Dashboard、Knowledge、API History、系统设置、系统管理、项目设置的右侧内容区统一为圆角卡片容器。
- Home 线程页已参考 wren-ui `Answer preparation steps`: 提交后立即显示动态思考/准备步骤, 处理过程中默认展开并逐步推进; 回答完成后默认折叠, 用户可展开查看检索语义模型、组织查询、执行查询、生成回答等过程。
- 系统设置 LLM 面板支持维护系统提示词, 项目设置 General 面板支持维护项目描述和项目提示词。提示词采用轻量 `{{variable}}` 模板变量渲染; 系统提示词支持 `{{app_name}}/{{language}}/{{timezone}}/{{llm_provider}}/{{llm_model}}/{{current_date}}`, 项目提示词支持 `{{name}}/{{display_name}}/{{description}}/{{semantic_model}}/{{datasource_count}}/{{model_count}}/{{current_date}}`。
- 返回/持久化的 thread response 对齐 wren-ui 形态: `askingTask`, `answerDetail`, `breakdownDetail`, `chartDetail`, `adjustment`, 同时保留 `sql`。
- `/api/query` 和 Ask 执行层已支持 DuckDB/sample 数据源真实执行并应用 CLS 结果过滤/掩码; 多个 DuckDB/sample binding 会通过 `ATTACH` 和临时 model view 查询。
- `/api/query` 与 `/api/query/dry-plan` 共用 `services/sql_guard.py` 的 sqlglot guard: 仅允许单条 SELECT/WITH/集合查询; 拒绝 INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/COMMAND、`ATTACH/DETACH/COPY/PRAGMA/LOAD/INSTALL/EXPORT/IMPORT` 等命令, 并阻断 DuckDB 文件/外部扫描函数如 `read_csv/read_parquet/read_json/glob/postgres_scan/mysql_scan/sqlite_scan`。
- RLS 已从外层 wrapper 改为基于 sqlglot AST 的表引用下推: 对 SELECT、聚合、JOIN alias、子查询和 CTE 内的真实模型表引用, 重写为 `FROM (SELECT * FROM model WHERE <policy>) AS alias`, 避免 `SELECT count(*) FROM orders` 因外层看不到 `tenant_id` 而失效。CTE 名称本身不会被误当成受保护模型表。
- CLS 当前分两层: 执行结果仍会隐藏/掩码输出列; dry-plan/execute 前会拒绝引用 HIDE 列的直接列引用, 包括聚合表达式如 `COUNT(DISTINCT secret)`。MASK 列仍只在结果层替换, 更严格的表达式级 mask 重写和完整列血缘追踪仍是后续增强。
- 非 DuckDB 最小执行已接入: PostgreSQL/Redshift、MySQL、ClickHouse、SQL Server、Trino/Athena SQL 必须引用语义模型名, 执行层再按 `table_reference` 改写到物理表; 为保证项目隔离和 RLS/CLS 策略可审计, `/api/query` 与 Ask 不再允许直接查询物理表名, 即使项目只有单一 datasource binding。多 binding 且 SQL 引用模型名时, 当前将各模型源表最多拉取 5000 行 materialize 到本地 DuckDB 临时表, 再执行最终合并 SQL。该方案适用于小规模跨源 join/merge, 不是完整下推优化器; 大表聚合/过滤下推、复杂 CTE 分解和分页总数仍待后续跨源引擎完善。

SSE 降级通道 (流式, WebSocket 不可达时自动降级):
```
POST /api/ask/stream      { question, thread_id, previous_questions? } → SSE stream (event: data frame: delta / result / error)
```

线程:
POST   /api/threads                   → { id, preview_row_limit } { project_id?, summary?, preview_row_limit? }
GET    /api/threads                   → 线程列表 (?project_id, &page, &page_size)
GET    /api/threads/:id               → 线程详情 (含 responses)
PUT    /api/threads/:id               → 手动更新线程标题 { summary } 并锁定自动标题
DELETE /api/threads/:id               → 删除线程 (级联删除关联 responses)
POST   /api/threads/:id/responses     → 新建回应 { question, sql? } → { response }
GET    /api/threads/:id/responses     → 获取线程所有回应
DELETE /api/responses                 → 批量清理旧回应 (管理员)
        ?before=ISO8601&project_id=可选
DELETE /api/history                   → 清理 API 历史
        ?before=ISO8601&status_code=可选
```

#### 建模

```
GET    /api/modeling/:project_id/diagram                    → { models, views, relations, calculated_fields }
PUT    /api/modeling/:project_id/diagram                    → 更新画布布局 JSON

GET    /api/modeling/:project_id/models                     → 模型列表
GET    /api/modeling/:project_id/models/:model_id           → 模型详情
POST   /api/modeling/:project_id/models                     → 创建模型 { name, display_name?, description?, table_reference?, model_type?, source_binding_id?, columns } → { id }
PUT    /api/modeling/:project_id/models/:model_id           → 更新模型 { name?, display_name?, description?, table_reference?, model_type?, source_binding_id?, columns? }
DELETE /api/modeling/:project_id/models/:model_id           → 删除模型
GET    /api/modeling/:project_id/models/:model_id/compiled-sql → 获取模型编译 SQL

GET    /api/modeling/:project_id/views                      → 视图列表
GET    /api/modeling/:project_id/views/:view_id             → 视图详情
POST   /api/modeling/:project_id/views                      → 创建视图 { name, display_name?, description?, model_id?, columns?, sql?, source_response_id? } → { id }
PUT    /api/modeling/:project_id/views/:view_id             → 更新视图 { name?, display_name?, description?, columns?, sql? }
DELETE /api/modeling/:project_id/views/:view_id             → 删除视图

GET    /api/modeling/:project_id/relations                  → 关系列表
POST   /api/modeling/:project_id/relations                  → 创建关系 { name, description?, source_model_id, source_column, target_model_id, target_column, relation_type } → { id }
PUT    /api/modeling/:project_id/relations/:relation_id     → 更新关系 { name?, description?, source_column?, target_column?, relation_type? }
DELETE /api/modeling/:project_id/relations/:relation_id     → 删除关系

GET    /api/modeling/:project_id/calculated-fields          → 计算字段列表
POST   /api/modeling/:project_id/calculated-fields          → 创建计算字段 { name, model_id, expression, result_type?, display_name?, description? } → { id }
PUT    /api/modeling/:project_id/calculated-fields/:field_id → 更新 { name?, expression?, result_type?, display_name?, description? }
DELETE /api/modeling/:project_id/calculated-fields/:field_id → 删除
```

#### Dashboard

```
GET    /api/dashboards            → Dashboard 列表
GET    /api/dashboards/:id        → 详情 (含 items)
POST   /api/dashboards            → 创建 { name, project_id, cache_enabled?, schedule_frequency?, schedule_timezone?, schedule_cron? } → { id }
PUT    /api/dashboards/:id        → 更新 { name?, cache_enabled?, schedule_frequency?, schedule_timezone?, schedule_cron? }
DELETE /api/dashboards/:id        → 删除

GET    /api/dashboards/:id/items           → 面板列表
POST   /api/dashboards/:id/items           → 新增面板 { title?, display_name?, chart_config?, data_source?, response_id?, type? } → { id }
PUT    /api/dashboards/:id/items/:item_id  → 更新面板 { title?, display_name?, chart_config?, data_source?, type? }
DELETE /api/dashboards/:id/items/:item_id  → 删除面板
PUT    /api/dashboards/items/layouts       → 批量更新布局 { layouts: [{ item_id, x, y, w, h }] }
POST   /api/dashboards/items/:item_id/preview?force_refresh=bool → 预览数据 { columns, rows }
POST   /api/dashboards/:id/schedule        → 设置缓存计划 { frequency, timezone?, cron? } → { success }
```

当前 Dashboard 实现说明:
- Dashboard 列表/创建显式要求真实 `project_id > 0`; 空项目可查看入口但不能写入 Dashboard。
- Home Chart tab 的 Pin 写入 `response_id` 与 `chart_config.spec/sql/columns/rows/preview_row_limit/source_response_id`。
- 手动 Add Widget 支持创建无 `response_id`、无 `chart_config` 的空 widget; preview 返回空 `columns/rows`, 不再 500。
- Dashboard item 若引用 response, 后端校验 response 必须属于同一 dashboard project。当前保存的 chart rows 是生成时快照; 如果用户权限变化, 最小实现尚不会按当前 viewer 重新执行 SQL, 这是后续 RLS/CLS cache key 与 viewer-aware dashboard rendering 的安全增强项。

#### 知识库

```
GET    /api/knowledge/instructions         → 指令列表 (?project_id, &search, &sort, &page, &page_size)
POST   /api/knowledge/instructions         → 创建 { project_id, text, category?, scope?, priority? } → { id }
PUT    /api/knowledge/instructions/:id     → 更新 { text?, category?, scope?, priority? }
DELETE /api/knowledge/instructions/:id     → 删除

GET    /api/knowledge/sql-pairs            → SQL Pair 列表 (?project_id, &search, &sort, &page, &page_size)
POST   /api/knowledge/sql-pairs            → 创建 { project_id, question, sql, description?, category?, scope? } → { id }
PUT    /api/knowledge/sql-pairs/:id        → 更新 { question?, sql?, description?, category?, scope? }
DELETE /api/knowledge/sql-pairs/:id        → 删除
```

#### 推荐引擎

```
## 问题推荐 (核心 API)
GET  /api/recommendations/:project_id               → { data: [{ title, category, scope, source_type, confidence, metadata }] }
      ?max_results=5&types=expand,drilldown,compare

## Schema 推荐 (层 0: 冷启动)
GET  /api/recommendations/:project_id/onboarding    → 冷启动推荐问题集合

## 自学习 Catalog (层 2)
GET    /api/recommendations/:project_id/catalog       → { data: [{ id, question, sql, metadata, verified, frequency }] }
POST   /api/recommendations/:project_id/catalog       → { id }
PUT    /api/recommendations/:project_id/catalog/:id   → { success }
DELETE /api/recommendations/:project_id/catalog/:id   → { success }

## 用户偏好 Hints (Odin 式)
GET    /api/recommendations/:project_id/hints         → { data: [{ id, hint_text, source_query, confidence }] }
POST   /api/recommendations/:project_id/hints         → { id }
PUT    /api/recommendations/:project_id/hints/:id     → { success }
DELETE /api/recommendations/:project_id/hints/:id     → { success }

## 推荐反馈 (隐式 + 显式)
POST /api/recommendations/:project_id/dismiss/:recommendation_id → { success }

## 推荐评分 (核心评分 API)
POST /api/recommendations/:project_id/rate/:recommendation_id → { id, score_id, recommendation_id, rating, comment }
     { rating: 1-5, comment?, source_layer?, recommend_type?, context? }

## 查看评分历史
GET  /api/recommendations/:project_id/scores        → { data: [...] }
     ?source_layer=可选

## 推荐引擎统计/管理 (管理员)
GET  /api/recommendations/statistics                → { total_catalogs, total_hints, top_queries, layer_performance, score_distribution, weight_history }
GET  /api/recommendations/statistics/weight-history → { history }
GET  /api/recommendations/statistics/low-score-alerts → { alerts }
GET  /api/recommendations/:recommendation_id/rating  → { avg_score, total_ratings, distribution }
PUT  /api/settings/recommendations                 → { success }
     { max_results?, schema_weight?, session_weight?, user_weight?, project_weight?, global_weight?, llm_weight?, novelty_weight?, score_weight?, score_learning_rate?, score_half_life?, low_score_threshold?, consecutive_low_alert?, auto_recover? }
```

#### 图表

```
GET  /api/exports/chart           ?question=...&sql=...&sample_size=... → { vega_spec, chart_type } (当前返回 501, 预留)
```

#### 内存层 (LanceDB)

```
GET  /api/exports/memories/search   ?query=...&type=...&project_id=可选 → { data: [...] }
POST /api/exports/memories/store    { type, content, project_id? } → { id }
GET  /api/exports/memories/list     ?type=...&project_id=可选 → { data: [...] }
POST /api/exports/memories/forget   { id } → { success }
```

#### 系统设置

```
GET  /api/settings                → 所有设置
GET  /api/settings/public         → 公共设置 (含 sso_enabled/sso_provider)
GET  /api/settings/audit-summary  → 设置变更审计摘要
PUT  /api/settings/branding       { app_name?, app_description?, logo?, icon? } → { success }
PUT  /api/settings/theme          { mode: "light"|"dark"|"system", primary_color?, border_radius?, font? } → { success }
PUT  /api/settings/llm            { provider?, api_key?, model?, endpoint?, max_tokens?, temperature?, extra_params?, system_prompt? } → { success }
POST /api/settings/llm/test       { provider, api_key?, model, endpoint?, probe_level? } → { success, latency_ms?, error?, async? }
POST /api/settings/llm/models     { provider, endpoint?, api_key? } → { models, error? }
GET  /api/settings/llm/advanced   → LLM 高级参数
PUT  /api/settings/llm/advanced   → 更新 LLM 高级参数
GET  /api/settings/ask            → Ask 运行参数
PUT  /api/settings/ask            → 更新 Ask 运行参数
GET  /api/settings/router         → Router 运行参数
PUT  /api/settings/router         → 更新 Router 运行参数
POST /api/settings/router/reload  → 强制刷新 Router 运行快照
GET  /api/settings/security       → 安全策略参数
PUT  /api/settings/security       → 更新安全策略参数
PUT  /api/settings/general        { language?, default_page?, telemetry?, timezone?, date_format?, session_timeout? } → { success }
GET  /api/settings/app-info       → { version, platforms }
GET/PUT /api/settings/recommendations → 推荐参数
```

#### 数据源 (两级管理)

```
## 系统级数据源 (全局注册)
GET    /api/system/datasources                        → 系统所有数据源列表
POST   /api/system/datasources                        → 新增系统数据源 { type, properties, name } → { id }
PUT    /api/system/datasources/:id                    → 更新系统数据源
DELETE /api/system/datasources/:id                    → 删除系统数据源 (检查是否被项目引用)
POST   /api/system/datasources/:id/test               → 测试连接 → { success, latency_ms?, error? }

## 项目级数据源绑定 (多对多)
GET    /api/projects/:id/datasources                 → 项目绑定的数据源列表
POST   /api/projects/:id/datasources                 → 绑定已有系统数据源 { datasource_id, alias?, config_overrides? } → { id, binding_id, bindingId }
DELETE /api/projects/:id/datasources/:binding_id     → 解绑数据源 (不影响系统注册)

## 从项目内直接添加 (同时注册到系统)
POST   /api/projects/:id/datasources/register        → 新建并绑定 { type, properties, name } → { id, bindingId }
                                                          自动完成: 1.创建系统数据源 2.绑定到项目

## 数据源元数据发现
GET    /api/projects/:id/datasources/:binding_id/tables  → 列出该数据源的可用表
POST   /api/projects/:id/datasources/:binding_id/sync    → 同步表结构变化 → { tables_discovered, tables_removed }
```

当前实现细节:
- Sample Project: 样例数据源的表、列、关系来自预置案例数据; Step3 关系使用 `frontend/src/lib/sampleRelations.ts` 真值关系。
- Manual DuckDB: 注册 DuckDB 数据源时按项目创建/打开 `backend/data/projects/{project_id}/{dbname}.duckdb`; 首次发现时执行 `initSql` 并记录 hash, 后续仅在 SQL 变化时重跑; 表/列从 DuckDB `information_schema` 和 `PRAGMA table_info` 读取。
- 非 DuckDB: 后端优先尝试真实连接读取元数据。当前支持 PostgreSQL/Redshift、MySQL、ClickHouse、SQL Server、Trino/Athena 的 live discovery, 依赖对应可选 Python 驱动是否安装。
- 配置兜底: 所有非 DuckDB 数据源都可通过连接属性里的 `table_details` 或 `tables` 提供元数据兜底; live discovery 失败时会返回 warning 并使用配置元数据。
- 非 DuckDB 查询执行: 已复用 discovery 使用的可选驱动, 支持 PostgreSQL/Redshift、MySQL、ClickHouse、SQL Server、Trino/Athena 的单源 SELECT/WITH 执行; 缺驱动或连接失败会返回 warning, Ask 会基于项目上下文回退普通 LLM 回答。
- 跨源最小执行: 当 SQL 引用多个 datasource binding 的语义模型名时, PrismBI 先分别对各源表执行 `SELECT * ... LIMIT 5000`, 将结果写入本地 DuckDB 临时表, 再执行原始语义 SQL 完成本地 join/merge; 超过 5000 行会在结果 warning 中提示截断风险。
- 其他数据源: BigQuery、Snowflake、Oracle、Databricks 等尚未实现 live discovery, 需要接入相应 SDK/驱动或先使用配置兜底。

---

## 8. 数据流设计

### 8.1 SQL 查询流程

```
旧架构:
  UI → Apollo Client → Apollo Server → Resolver → queryService
  → wrenEngineAdaptor (HTTP) → wren-engine (Java Docker)
  → ibisAdaptor (HTTP) → ibis-server (Python Docker) → Database
  【4 次 HTTP 调用, 3 个独立服务】

新架构 (单源):
  UI → fetch(/api/query) → FastAPI → WrenEngine.query(sql)
  → wren-core-py.transform_sql() + ibis.execute() → Database
  【1 次 HTTP 调用, 1 个后端进程】

新架构 (跨源):
  UI → fetch(/api/query) → FastAPI
  → cross_source_service.analyze(sql)
  │   ├── 识别涉及的数据源集合 {ds_a, ds_b, ...}
  │   ├── 为每个数据源生成子查询
  │   ├── 并行执行: asyncio.gather(ds_a.exec(), ds_b.exec(), ...)
  │   ├── 结果写入 DuckDB temp_<session>/ 表
  │   ├── 重写 SQL 引用临时表
  │   └── DuckDB 本地执行合并 → 返回结果
  【1 次 HTTP, 1 个后端进程, N 个并行数据源查询】
```

### 8.2 自然语言→SQL (WebSocket 流式)

```
UI (WebSocket Client)
  │
  │ connect: /ws/ask (ws-ticket 或 token)
  │
  │ send:  { type: "ask", question: "上月销售额多少?", thread_id: 1 }
  │
  ├── recv: { type: "state", state: "generating", message: "正在理解问题..." }
  ├── recv: { type: "delta", content_type: "text", content: "我要查询..." }
  ├── recv: { type: "state", state: "sql_ready", sql: "SELECT ..." }
  ├── recv: { type: "state", state: "executing" }
  ├── recv: { type: "delta", content_type: "sql", content: "SELECT sum(amount)..." }
  ├── recv: { type: "delta", content_type: "chart", content: {...vegaSpec...} }
  ├── recv: { type: "state", state: "summarizing" }
  ├── recv: { type: "delta", content_type: "text", content: "上月销售额共..." }
  └── recv: { type: "result", sql, data, chartSpec, summary }
```

### 8.3 多项目切换流程

```
用户点击"切换项目"
  │
  ▼
FastAPI: POST /api/projects/:id/switch
  │
  ├── DuckDB: 读取 project 元数据 (dsn, catalog, schema)
  ├── wren-engine: 加载对应 Project Context
  │   ├── 读取 YAML 项目文件或 DuckDB 中的 MDL 定义
  │   └── 初始化 SessionContext
  └── 返回项目详情
  │
  ▼
UI: 重载所有数据 (Diagram, Threads, Dashboard)
```

### 8.4 推荐引擎数据流

```
问答完成后 → 触发推荐流程 (异步)
  │
  ▼
推荐引擎 (Recommendation Engine)
  │
  ├── 层 0: Schema 驱动 (同步, < 10ms)
  │   ├── 输入: 当前项目 MDL 语义模型
  │   ├── 处理: 度量/维度组合 → 模板填充
  │   └── 输出: 候选问题集 S0 (3-10 条)
  │
  ├── 层 1: 会话级 (异步, < 50ms)
  │   ├── 输入: 当前会话历史 {q1, q2, ..., qn} + 最近回复
  │   ├── 处理: LLM 分类 (Expansion/Follow-up) + 共现查询匹配
  │   ├── 存储: DuckDB recommended_questions_cache (TTL 30min)
  │   └── 输出: 候选问题集 S1 (2-5 条)
  │
  ├── 层 2: 项目级 (异步, < 100ms)
  │   ├── 输入: 项目内历史查询 + 自学习 Catalog + 兴趣聚类
  │   ├── 处理:
  │   │   ├── 热门查询 (按频率+新颖性排序)
  │   │   ├── 兴趣簇匹配 (当前上下文 → 最相似簇 → 簇内 Top)
  │   │   └── 工作负载 Hint 检索 (TailorSQL 式)
  │   └── 输出: 候选问题集 S2 (2-5 条)
  │
  ├── 层 3: 全局级 (异步, 后台 Batch)
  │   ├── 输入: 全量用户 + 全量项目数据
  │   ├── 处理:
  │   │   ├── 协同过滤 (CFQP 式图传播)
  │   │   ├── 意图趋势 (周期检测兴趣偏移)
  │   │   └── 语义模型优化建议 (Snowflake 式)
  │   └── 输出: 候选问题集 S3 (1-3 条) + 模型优化建议
  │
      └── 排序器 (Ranker)
      ├── 加权融合: 各层统一打分
      │   rank = 0.22 × S_schema + 0.18 × S_session + 0.13 × S_user
      │        + 0.13 × S_project + 0.08 × S_global + 0.08 × S_llm
      │        + 0.05 × S_novelty + 0.13 × S_score
      ├── 去重: 过滤语义相似问题
      ├── 终止条件: 保留 top-5
      └── 返回 UI

用户反馈 → 反馈循环
  ├── 隐式: 用户点击/忽略某个推荐 → 更新频率权重
  ├── 显式评分 (1-5★):
  │   ├── 写入 recommendation_scores 表
  │   ├── 更新 S_score 因子 (同类推荐历史评分加权平均)
  │   ├── 评分 ≥ 4 → 提升 Catalog 优先级 +0.1
  │   ├── 评分 ≤ 2 → 降低该来源层权重 ΔW = α × (avg_score - 3) × lr
  │   ├── 连续 5 次低评分同来源 → 触发管理员通知
  │   └── 评分附带上下文 (session/question), 用于后续分析
  ├── 自学习: 用户接受的推荐写入 Catalog (ProxySQL 式)
  └── 权重自动恢复: 低评分来源层 7 天无低评 → 恢复 50% 衰减
```

### 8.5 状态管理分层

| 层 | 技术 | 职责 |
|----|------|------|
| 服务端状态 | TanStack Query | API 缓存、自动重验证、乐观更新 |
| UI 全局状态 | Zustand | 用户信息、当前项目、主题、侧栏展开 |
| 建模状态 | Zustand | 画布数据、选中节点、拖拽状态 |
| 线程状态 | Zustand + WS | 当前线程、流式内容累积 |
| 本地持久化 | localStorage | 主题偏好、上次登录信息 |

---

## 9. 关键模块详细设计

### 9.1 建模画布 (Modeling Canvas)

**当前实现**:
- `/modeling` 使用 ReactFlow v12, 左侧只保留 Models/Views 面板, 已去掉独立 Fields 面板。
- 模型布局采用关系感知的轻量分层方案: 根据 `relation_type` 推断一侧/多侧方向, 多组件分组 packing, 并按画布宽高比选择换行以提高空间利用率。
- 无关系模型回退为居中网格布局; 有关系模型按左到右分层、同层按连接重心和连接度排序, 尽量减少交叉线。
- 模型节点支持左键单击选中并查看元数据, 双击进入编辑; 关系边支持单击查看、双击编辑。
- 模型/关系支持右键菜单: View details / Edit metadata / Delete, 菜单按画布边界定位避免溢出。
- 左侧栏参照 wren-ui Modeling sidebar: 同屏展示 Models/Views 两个分组, Models 组提供 Refresh/New, Views 组提供 New; 点击模型/视图条目会联动画布定位并高亮对应节点。
- 画布现在展示模型节点和视图节点; 视图节点通过其 `model_id` 参与自动布局, 便于从左侧 Views 列表定位到画布。
- 新建模型流程参照 wren-ui `ModelDrawer/ModelForm`: 不是只输入模型名, 而是选择项目数据源绑定、选择源表、选择字段、选择主键, 然后创建模型并写入 `table_reference/source_binding_id/column_defs`。
- 新建视图流程参照 wren-ui: 建模页 Views 组的 New 不直接创建空视图, 而提示用户先在 Home 问答结果中使用 `Save as View`; 保存后再回到 Modeling 复核和部署。
- 画布空白区域右键菜单作为 PrismBI 增强入口: Create data model / How to create a View / Fit to screen。
- 模型、视图、关系、计算字段元数据编辑均已接后端 API: `PUT /api/modeling/{project_id}/models/{model_id}`、`views/{view_id}`、`relations/{relation_id}`、`calculated-fields/{field_id}`。
- Home 结果卡的 `Save as View` 已接后端 `POST /api/modeling/{project_id}/views`: 从 thread response 保存 `sql/source_response_id/columns` 到 `metadata.views`, `model_id` 对该场景允许为空; 保存后刷新 Modeling diagram。建模页直接 New View 仍提示从 Ask 结果保存, 避免创建缺少 SQL 来源的空视图。
- 右侧属性栏已从窄表格改为卡片式字段详情, 并按最新设计从 520px 收窄 20% 至约 416px (`max-width` 35vw): 长字段名 `break-all`, 描述 `whitespace-pre-wrap`, 编辑模式字段 description 使用多行可调整 `textarea`, 避免长字段名和描述被截断。
- 左侧栏现在展示 Models/Views/Calculated Fields 三个分组; 视图和计算字段支持 View details / Edit metadata / Delete。模型、视图、关系、计算字段删除已接真实 API; 编辑成功后刷新 diagram 并保持属性面板查看态。

**保留功能** (100% 移植):
- 模型节点展示 (字段列表、主键标识、计算字段)
- 视图节点展示 (SQL 声明)
- 关系连线 (ONE_TO_ONE / ONE_TO_MANY / MANY_TO_ONE)
- 拖拽创建关系
- 右键菜单 (编辑元数据、删除)
- 元数据抽屉 (字段描述、别名、嵌套字段)
- 计算字段弹窗 (表达式选择 + lineage)
- 模型编辑弹窗 (源表映射、字段选择、主键)
- 数据预览

**改进点**:
- ReactFlow 升级到 v12 (更好性能 + TypeScript 支持)
- 画布状态从 GraphQL 缓存移到 REST + TanStack Query, 后续可抽到 Zustand store 支持 undo/redo
- 当前元数据编辑采用即时 REST mutation; 批量变更一次性提交仍为规划项
- 添加 undo/redo 支持 (命令模式)
- 移动端只读查看

### 9.2 问答线程 (Ask Thread)

**当前实现**:
- Ask Service 已实现完整 NL2SQL 路由引擎 (详见 §9.7): 3 层路由策略 (direct_llm / fewshot_cot / decompose_merge)、Schema 按需剪枝、Decompose & Merge 复合问题处理、GROUP BY 完整性校验、聚合一致性校验。
- 自然语言输入 + 推荐问题展示
- SQL 生成 → 执行 → 结果 → 总结 四步流
- 流式内容 (WebSocket + SSE 备选)
- SQL 分解步骤展示
- 图表自动生成 + 手动调整 (图表类型/轴配置)
- 另存为视图、保存到知识库
- 线程管理 (创建、切换、删除)
- 调整 SQL / 推理步骤
- 对话历史上下文

**改进点**:
- WebSocket 优先, SSE 降级
- AbortController 支持取消生成
- 使用 wren-engine 的 `query` 方法一步到位
- LanceDB 语义缓存历史查询
- 移动端兼容 (触屏友好的输入界面)

### 9.3 Dashboard

**当前实现补充**:
- `/home/dashboard` 和 `/home/dashboard/[dashboardId]` 仍在全局 AppShell 左侧栏内, 页面内容区二级左栏参考 wren-ui DashboardTree: 顶部分组标题 `Dashboards` + `New`, 下方列出仪表板条目、数量、选中态、重命名和删除操作; 去掉无关说明文案。
- Dashboard 右侧内容区统一为圆角卡片, 与二级左栏保持 `5px` 间距和统一上下左右留白; 列表页右侧不再重复显示“仪表盘”大标题, 详情页不再在内容区重复显示仪表盘名称, Header 在 `/home/dashboard/:id` 显示 `仪表盘 >> <仪表盘名称>`。
- Dashboard 列表按当前项目 `project_id` 查询; 空项目下可进入页面但禁用创建, 避免把仪表盘写入错误项目。
- Home Chart tab 的 Pin 使用弹窗选择 Dashboard 后确认, 不再使用工具栏下拉; 固定时保存 `response_id` 和 `chart_config.spec/sql/columns/rows/preview_row_limit/source_response_id`, Dashboard API 返回时将 DuckDB JSON 解析为对象, Dashboard 详情页直接用 `ChartContainer` 渲染已保存的图表数据, 不再显示占位图标。
- 兼容旧面板: 如果旧 `dashboard_items.chart_config` 缺少 `rows/columns/sql` 但存在 `response_id` 或 `source_response_id`, 后端从 `metadata.thread_responses.answer_detail` 补齐图表所需数据; 前端仍保留字符串 JSON 解析兜底, 只有确实缺少 `spec` 或 `rows` 时才显示缺数据空态。
- Dashboard 详情页使用内容自适应自动布局: 1 张图居中并放大, 2 张图双列铺开, 3 张图采用 6 列主次布局, 4 张图 2x2, 5 张及以上使用响应式 2/3 列; 根据 chart mark、rows/columns 数量识别 metric/table/line/area/heatmap 等内容密度并调整宽高, 避免少量图表龟缩在画布左上角。若面板存在非默认 `layout_x/layout_y/layout_w/layout_h`, 优先按保存布局的宽高映射到响应式 CSS, 不覆盖用户/历史布局。
- 图表渲染链路使用 `react-vega@8 + vega@6 + vega-lite@6 + vega-embed@7`。前端在 `ChartContainer` 对 LLM/持久化 spec 做运行时净化, 移除外部 `data.url`、`expr/expression/signal/signals/usermeta` 等可执行/外联入口, 并强制使用本地 SQL preview rows 渲染。

**保留功能**:
- react-grid-layout 网格布局
- 8 种图表类型 (柱/折线/饼/面积/堆积/分组/表/数字)
- 缓存调度 (cron 表达式, 频率/时区/日)
- 面板 CRUD (创建、编辑标题、删除)
- 仪表盘切换
- 数据刷新

**改进点**:
- 缓存直接从 DuckDB + LanceDB 读取
- 图表 spec 由 wren-engine Skills 生成
- TanStack Query `refetchInterval` 自动刷新
- 移动端只读查看 + 触屏交互

### 9.4 设置向导 (Setup Wizard)

**当前实现**:
- 主流程收敛在 `/setup/connection`, 页面内部完成 `mode -> models -> relations -> create`。
- Sample Project 与 Manual Setup 互斥隔离, 切换时清理另一模式选择, 避免样例与手动数据源混用。
- Sample Project 显示 `hr`, `music`, `ecommerce`, `nba` 四个样例按钮, 支持多选。
- Manual Setup 的 `Add DataSource` 使用弹窗添加/编辑数据源草稿, 不跳转新页面; 数据源卡片支持单击选中/反选、双击编辑、选中删除。
- Step2 默认规则: Sample 全选表; Manual 全不选。
- Step3 默认规则: Sample 使用 `sampleRelations.ts` 真值关系并默认选中; Manual 使用启发式推荐关系且默认不选。
- 完成创建后先创建项目/注册选中的数据源/写入模型与关系, 再调用 `/api/projects/:id/switch`, 保证后端当前项目、用户默认项目、前端 store 一致, 然后跳转 `/modeling`。
- 创建项目时后端会把创建人以项目级 `project_admin` 角色写入 `metadata.user_roles(project_id=新项目)`, 因此项目设置 Members 初始即可看到项目创建者。
- 创建流程通过 `/api/projects/:id/datasources/register` 写入 `metadata.datasources` 与 `metadata.project_datasources`; 项目设置 Data Sources 直接读取 `/api/projects/:id/datasources`, 复用创建时绑定的数据源, 不再从全局系统数据源列表推断。

**当前限制**:
- 非 DuckDB 数据源已支持 PostgreSQL/Redshift、MySQL、ClickHouse、SQL Server、Trino/Athena 的 live discovery, 但依赖可选 Python 驱动; 其他数据源暂以 `table_details`/`tables` 配置兜底并返回 warning。
- 非 DuckDB 查询执行已覆盖同一批驱动的最小 SELECT/WITH 路径; 跨源合并目前采用本地 DuckDB materialize 前 5000 行再 join/merge, 尚未做谓词/聚合下推或代价优化。
- Home Ask 当前 SQL 生成仍通过 LLM fallback adapter 完成, 尚未真实调用 wren-engine; 多步骤跨源计划形态已由本地 materialize 合并路径支撑基础场景, 但还没有独立的 `source_steps[] + final_local_sql` planner。
- Manual 关系推荐仍为前端启发式推断, 尚未接入后端智能关系推荐。
- 旧 `/setup/models` 与 `/setup/relationships` 路由保留用于兼容, 但主业务流不再依赖跨页面状态传递。

### 9.5 知识库 (Knowledge)

**当前实现补充**:
- `/knowledge/instructions` 与 `/knowledge/question-sql-pairs` 仍在全局 AppShell 左侧栏内, 页面内容区新增模块内二级左栏: Instructions、Question-SQL Pairs。
- Knowledge 二级左栏和右侧内容区统一为 Home/Dashboard 同款圆角卡片, 左右间距为 `5px`; 右侧内容区不再重复显示页面标题, Header 在子页面显示 `知识库 >> 指令管理` 或 `知识库 >> 问答对`。中文界面下二级左栏标签和说明全部使用中文文案。
- Knowledge CRUD 使用当前项目 `currentProject.id`; 空项目下可查看空态说明, 但禁用创建, 不再硬编码 `project_id=1`。
- 当前版本不接入向量数据库。相似检索采用 DuckDB metadata 表内的文本检索和加权排序: Instructions 对 `instruction/category/scope` 加权; Question-SQL Pairs 对 `question/description/sql/category/scope` 加权。后续可在不改变 CRUD API 的前提下替换为 DuckDB FTS、embedding 表或外部 vector DB。
- Ask 已接入同一检索逻辑: 命中的 Instructions 会作为业务规则/口径提示, 命中的 SQL Pairs 会作为 verified examples 注入路由和 SQL 生成上下文; 响应中的 `knowledgeHits` 记录命中的 instruction/sql_pair id、category、scope。
- Home Result 的“Save as Pair / 另存为答对”已从占位操作改为写入 `metadata.sql_pairs`, 保存 `project_id/question/sql/answerDetail.content/category=saved_answer/scope=project`, 供 Knowledge 列表和后续检索复用。

**保留功能**:
- Instructions 管理 (CRUD + 全局/按问题匹配)
- Question-SQL Pairs 管理 (CRUD + 格式化展示)
- 详细信息抽屉

**改进点**:
- 指令存储在 DuckDB + 可同步到 `instructions.md`
- SQL Pair 存储在 DuckDB; 当前无向量库版本使用 DuckDB 文本检索/加权排序, LanceDB/embedding 索引作为可选后续增强
- 利用 Skills 框架处理指令注入

---

### 9.6 前端 UI 设计体系

#### 9.6.1 设计原则

| 原则 | 说明 |
|------|------|
| **数据优先** | 界面围绕数据展示而非表单控件, 可视化是主角 |
| **渐进式复杂** | 用户从简单入手 (问答框), 逐步深入 (建模/调优) |
| **一致性** | 所有 CRUD 操作复用统一模式, 减少认知负担 |
| **反馈即时** | 每次操作都有即时反馈 (乐观更新/骨架屏/Toast) |
| **响应式** | Web/Desktop/Mobile 共享逻辑, 适配不同视口 |

#### 9.6.2 设计系统 (Design Tokens)

| Token | 值 | 用途 |
|-------|-----|------|
| `--color-primary` | `#1677ff` | 品牌主色 (按钮/链接/激活态) |
| `--color-success` | `#52c41a` | 成功/通过 |
| `--color-warning` | `#faad14` | 警告/注意 |
| `--color-error` | `#ff4d4f` | 错误/失败 |
| `--color-bg` | `#f5f5f5` (light) / `#141414` (dark) | 页面背景 |
| `--color-surface` | `#ffffff` (light) / `#1f1f1f` (dark) | 卡片/面板背景 |
| `--radius-sm` | `4px` | CodeMirror / 小标签 |
| `--radius-md` | `8px` | 卡片 / 对话框 |
| `--radius-lg` | `12px` | 大容器 / 模态框 |
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.06)` | 卡片浮起 |
| `--shadow-lg` | `0 8px 24px rgba(0,0,0,0.12)` | 模态框 / 下拉 |
| `--font-mono` | `'JetBrains Mono', 'Fira Code', monospace` | SQL/Code 显示 |
| `--font-sans` | `'Inter', -apple-system, sans-serif` | 界面正文 |
| `--spacing-unit` | `4px` | 间距基准单位 |

#### 9.6.3 页面状态机

每个主要页面遵循统一的状态模型:

```
┌──────────┐  首次加载   ┌─────────┐  空数据   ┌──────────┐
│  LOADING  │ ───────→ │  EMPTY   │ ─────→ │  READY   │
│ (骨架屏)  │           │ (空状态)  │         │ (正常)   │
└──────────┘           └─────────┘         └──────────┘
                          ↑                     │
                          │   错误                │ 操作
                          │                     ↓
                       ┌──────────┐        ┌──────────┐
                       │  ERROR   │        │  LOADING  │
                       │ (重试/回退)│ ←──── │ (乐观更新) │
                       └──────────┘        └──────────┘
```

各页面状态组件:

| 页面 | Loading | Empty | Error | Ready |
|------|---------|-------|-------|-------|
| 问答首页 | 骨架屏 (3 行) | 推荐问题列表 + 输入框 | 错误 Toast + 重试按钮 | 对话列表 |
| 建模画布 | 全屏骨架 | "添加第一个模型" 引导 | 错误 Drawer + 回退按钮 | 画布正常 |
| Dashboard | 卡片骨架 (4×grid) | "创建第一个面板" CTA | 局部错误标签 + 重试单卡片 | 仪表盘正常 |
| 知识库 (Instructions) | 骨架行列表 | "创建第一条指令" | 错误 Toast + 重试按钮 | 指令列表 |
| 知识库 (SQL Pairs) | 骨架行列表 | "添加第一个 SQL 问答对" | 错误 Toast + 重试按钮 | SQL 问答对列表 |
| 管理后台 (用户) | 骨架表格 | "邀请第一个用户" | 错误 Drawer + 重试按钮 | 用户表格 |
| 管理后台 (角色) | 骨架表格 | "创建第一个角色" | 错误 Drawer + 重试按钮 | 角色表格 |
| 管理后台 (审计) | 骨架表格 | (不适用) | 错误 Drawer + 重试按钮 | 审计日志表格 |
| API 历史 | 骨架行列表 | "暂无 API 请求记录" | 错误 Toast + 重试按钮 | 历史列表 |

API 历史当前实现说明:
- `/api-management/history` 改名为 API History / API 历史, 数据源为 `metadata.api_history`, 不再复用审计日志表。
- FastAPI HTTP middleware 会记录 `/api/*` 请求的 method、path、query、thread_id、request_payload、status_code、duration_ms 和时间; Settings 查询暂不记录以减少噪声。
- 页面支持 endpoint 搜索、method 筛选、status 筛选、分页, 空值以 `-` 展示; 审计日志仍留在 Admin Audit Log。
- API History 页面右侧内容区与 Home/Dashboard/Knowledge 一样使用圆角卡片容器, 内页不再重复显示“API 历史”标题, 标题只保留在 Header。

全局左侧栏当前实现说明:
- 全局 `Sidebar` 统一为知识库页面同类的简洁圆角列表风格, 不再在左侧栏显示当前项目信息; 当前项目只通过 Header 右上角项目下拉展示和切换。
- Sidebar 折叠态使用居中固定点击区并保持图标 `h-5 w-5 shrink-0`, 避免窄栏下被文字间距和 padding 挤压导致图标过小。
- Sidebar 折叠态顶部只显示应用图标; 鼠标悬浮在该图标区域时切换为展开图标, 离开后恢复应用图标, 避免应用图标和展开按钮并排挤压。
- Home 线程列表、Dashboard 二级栏、Modeling 左侧栏统一采用知识库页面的圆角卡片式侧栏风格。
- Modeling 左侧栏的 Models / Views / Calculated Fields 三个分组各自固定显示分组标题、数量和操作按钮; 每个分组的数据区域独立滚动且支持折叠, 分组标题不会被滚动内容隐藏。
| 数据源管理 | 骨架列表 | "添加第一个数据源" CTA | 保存失败 Toast + 重试按钮 | 数据源列表 |
| 推荐引擎设置 | Skeleton 行 | (不适用) | 保存失败 Toast + 重试按钮 | 设置表单 |
| 设置页 | Skeleton 行 | (不适用) | 保存失败 Toast + 重试按钮 | 表单正常 |
| 对话线程 | 骨架屏 + Spinner | (不适用, 通过问答页进入) | 错误内联提示 + 重试按钮 | 历史对话列表 |
| 设置向导 Step 1 (数据源) | 骨架表单 | "请先添加系统数据源" | 错误 Toast + 回退上一步 | 数据源选择表单 |
| 设置向导 Step 2 (模型) | 骨架树 | "从数据源选择表" | 错误 Toast + 回退上一步 | 模型选择列表 |
| 设置向导 Step 3 (关系) | 骨架画布 | "建立模型间关联" | 错误 Drawer + 回退上一步 | 关系编辑画布 |
| 个人资料 | Skeleton 行 | (不适用) | 保存失败 Toast + 重试按钮 | 资料表单 |
| 会话管理 | 骨架表格 | "无活跃会话" | 错误 Toast + 重试按钮 | 会话列表 |
| SSO 配置 | 骨架表单 | (不适用) | 保存失败 Toast + 重试按钮 | SSO 配置表单 |
| 项目设置 | 骨架标签页 | (不适用) | 错误 Drawer + 重试当前标签 | 设置标签页 |
| 项目成员 | 骨架表格 | "添加第一个成员" | 错误 Toast + 重试按钮 | 成员表格 |
| 评分历史 | 骨架图表 + 表格 | "暂无评分数据" | 错误 Toast + 重试按钮 | 评分分布图 + 历史列表 |

##### PromptInput 状态
| 状态 | 行为 | UI 表现 |
|------|------|---------|
| `idle` | 等待用户输入 | 输入框可用, 发送按钮正常 |
| `submitting` | 用户已点击发送, 请求进行中 | 输入框禁用, 发送按钮显示 Spinner |
| `disabled` | WebSocket 连接未就绪 | 输入框禁用, 显示 "正在连接..." 提示 |
| `error` | 上次提交失败 | 输入框可用, 显示错误提示 + 重试按钮 |

#### 9.6.4 关键交互模式

**问答流 (核心用户体验)**:

```
1. [空闲态] 展示推荐问题 + 输入框
   │
2. [输入态] 用户输入 → 输入框联动语法高亮
   │
3. [生成态] WebSocket 流式接收:
   │  ┌─ "正在理解问题..." (Text)
   │  ├─ "已生成 SQL" (SQL 块, CodeMirror 语法高亮)
   │  ├─ "正在执行查询..." (Spinner + 进度)
   │  ├─ 结果表格 (虚拟滚动)
   │  └─ 图表渲染 (Vega-Lite, transition 动画)
   │
4. [回应态] 完整 ResponseCard 展示:
      ├── SQL 预览 (可折叠/复制/编辑)
      ├── 结果表格 (虚拟滚动, 固定表头)
      ├── 图表 (Vega-Lite, 可切换类型)
      ├── 文本摘要 (Markdown 渲染)
      └── 推荐下一轮问题 (RecommendationCard + StarRating)
   │
5. [反馈态] 用户评分推荐 / 继续提问 / 调整 SQL
```

**建模画布拖拽交互**:

```
1. [自动布局] 模型创建完成进入 /modeling
   ├── 有关系: 按一侧/多侧方向左到右分层, 同层按连接重心排序
   ├── 多组件: 关系簇按画布宽高比 packing, 减少空白区域
   └── 无关系: 居中网格布局, 保持模型尽量位于画布中心
2. [查看/选中] 左键单击模型节点或关系边
   ├── 选中对象高亮
   ├── 右侧属性面板以只读模式展示元数据
   └── 点击面板 Edit 进入编辑态
3. [左侧栏联动] 点击 Models/Views 分组下的模型或视图
   ├── 选中左侧条目
   ├── 调用 ReactFlow fitBounds 聚焦对应画布节点
   └── 模型会打开属性面板, 视图定位到画布节点
4. [快速编辑] 双击模型节点或关系边
   ├── 模型: 编辑 name/displayName/字段主键标识
   ├── 关系: 编辑 name/sourceColumn/targetColumn/relationType
   └── 保存后调用 REST API 并刷新 diagram
5. [右键菜单] 模型节点或关系边右键
   ├── View details
   ├── Edit metadata
   └── Delete
6. [画布空白右键菜单]
   ├── Create data model → 打开源表/字段/主键选择流程
   ├── How to create a View → 提示从 Home 问答结果 Save as View
   └── Fit to screen → fitView
7. [新建模型] Models 组 New / 空白画布 Create data model
   ├── 选择 datasource binding
   ├── 选择 source table
   ├── 选择 columns
   └── 选择 primary key
8. [新建视图] Views 组 New / 空白画布 How to create a View
   └── 跳出说明: 到 Home 提问并从 SQL 结果保存为 View
9. [连线建关系] 从源字段拖拽连线到目标字段 → 创建关系 (规划增强)
10. [撤销/重做] Ctrl+Z / Ctrl+Shift+Z (命令模式, modelingStore.stack, 规划增强)
11. [画布导航] 滚轮缩放 + 拖拽平移 + 定位到节点 (ReactFlow 内置)
```

**Dashboard 网格布局**:

```
1. [面板排列] react-grid-layout 拖拽调整面板位置/尺寸
   ├── 支持: 拖拽移动 / 右下角拖拽缩放 / 静态面板锁定
   ├── 布局持久化: PUT /api/dashboards/items/layouts
   └── 空面板: "点击添加图表" CTA → 弹出图表选择器
2. [图表配置] 点击面板标题栏 → 编辑面板配置
   ├── 数据源选择 (线程回应/自定义 SQL)
   ├── 图表类型切换 (柱状/折线/饼图/散点/热力图)
   └── 轴字段映射 (X/Y/颜色/大小)
3. [缓存计划] 设置面板 → cron 表达式 + 时区选择 → POST /api/dashboards/:id/schedule
```

**设置向导 (三步)**:

```
Step 1: 数据源连接 ──→ Step 2: 模型选择 ──→ Step 3: 关系定义 ──→ 完成
   │                      │                      │
   ├─ 选择已有数据源     ├─ 从已连接数据源      ├─ 在模型间拖拽
   ├─ 或新建数据源       │  选择表并建模型       │  创建关系
   ├─ 测试连接           ├─ 预览表结构          └─ 自动推荐关系
   └─ 下一步 →          └─ 下一步 →
```

**图表切换交互**:

```
ResponseCard 内:
  ┌─ 图表类型选择器 (Vega-Lite 模板)
  │   柱状图 | 折线图 | 饼图 | 散点图 | 热力图 | 数据表
  ├─ 拖入/拖出字段到 X/Y/Color/Size 通道
  ├─ transition 动画切换 (d3 过渡)
  └─ 右键导出: PNG / SVG / CSV
```

**表单编辑模式**:

```
所有 CRUD 操作 (模型/视图/关系/设置) 遵循:
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │  查看模式     │ ──→ │  编辑模式     │ ──→ │  保存/取消    │
  │ (只读展示)    │     │ (内联编辑    │     │ (乐观更新    │
  │             │ ←── │  / 弹窗/抽屉)│     │  / 回滚)     │
  └──────────────┘     └──────────────┘     └──────────────┘
```

#### 9.6.5 响应式断点

| 断点 | 宽度 | 目标设备 | 布局变化 |
|------|------|---------|---------|
| `xs` | < 640px | 手机 | 单列, 底部 Tab, 简化推荐 |
| `sm` | 640-1023px | Pad 竖屏 | 双列, 侧栏可折叠 |
| `md` | 1024-1439px | Pad 横屏 / 小桌面 | 三列, 侧栏固定 |
| `lg` | ≥ 1440px | 大桌面 | 四列, 全功能布局 |

#### 9.6.6 组件设计规范

| 组件类别 | 使用场景 | 示例 |
|---------|---------|------|
| **原子组件** | 基础 UI | Button, Input, Select, Modal, Table, Tag |
| **复合组件** | 业务通用 | FormDrawer, SearchTable, ConfirmDialog |
| **页面组件** | 完整页面 | HomePage, ModelingPage, SettingsPage |
| **布局组件** | 页面壳 | AppShell, Sidebar, HeaderBar, MobileShell |
| **功能组件** | 核心能力 | PromptInput, ResponseCard, Canvas, VegaChart |
| **推荐组件** | 推荐引擎 | RecommendedQuestions, RecommendationCard, StarRating, ScoreHistory |

所有复合/功能组件遵守:
- **Props**: 类型定义导出, 必要 props 加 `/** @required */` JSDoc
- **Loading**: 所有数据获取有 `<Skeleton />` 兜底
- **Empty**: 列表为空时显示 `<EmptyState message="..." action={...} />`
- **Error**: 失败时显示 `<ErrorBoundary>` 或 `<ErrorToast />`
- **Optimistic**: TanStack Query `optimisticUpdate` 优先

#### 9.6.7 全局通知与反馈体系

```
┌─────────────────────────────────────────────┐
│  全局 Toast 系统                              │
│                                              │
│  ⚠️ 保存失败: "网络连接异常, 请重试"    [✕]  │
│  ✅ 模型已保存                                │
│  ℹ️ 查询完成, 返回 1,234 行                   │
│                                              │
│  显示规则:                                    │
│  ├── 成功: 2s 自动消失 (绿色)                 │
│  ├── 错误: 直到用户关闭 (红色 + 重试按钮)      │
│  ├── 警告: 5s 自动消失 (黄色)                 │
│  └── 信息: 3s 自动消失 (灰色)                 │
└─────────────────────────────────────────────┘

  ConfirmDialog 确认弹窗:
  ┌─ 确认删除 ─────────────────────────────┐
  │  确定要删除模型 "orders" 吗?            │
  │  该操作不可撤销, 同时移除 3 个相关关系。  │
  │                                        │
  │        [取消]    [确认删除]              │
  └────────────────────────────────────────┘
```

#### 9.6.8 键盘快捷键体系

| 快捷键 | 上下文 | 操作 |
|--------|--------|------|
| `Enter` | 问答输入框 | 发送问题 |
| `Shift+Enter` | 问答输入框 | 换行 |
| `Escape` | 全局 | 关闭 Modal/Drawer/下拉 |
| `Ctrl+Z` / `Cmd+Z` | 建模画布 | 撤销 |
| `Ctrl+Shift+Z` / `Cmd+Shift+Z` | 建模画布 | 重做 |
| `Ctrl+S` / `Cmd+S` | 编辑态 | 保存当前编辑 |
| `Ctrl+K` | 全局 | 命令面板 (快速导航/操作) |
| `←` `→` | 问答线程 | 切换历史线程 |
| `/` | 全局 | 聚焦搜索/输入框 |

#### 9.6.9 表单验证策略

| 验证层级 | 时机 | 实现方式 |
|---------|------|---------|
| **前端字段** | 输入时 (实时) | Zod Schema → onChange 校验, 立即显示行内错误 |
| **前端提交** | 提交时 | Zod `safeParse` 全量校验, 未通过则聚焦首错字段 |
| **后端字段** | 请求时 | Pydantic validator → 422 响应, 前端映射到对应字段 |
| **后端业务** | 请求时 | Service 层业务规则校验 → 语义化错误码 |
| **乐观回滚** | 提交后 | TanStack Query onError → 恢复旧值 + Toast 提示 |

#### 9.6.10 React 渲染性能优化

| 模式 | 适用范围 | 说明 |
|------|---------|------|
| **React.memo** | 列表项 (`ModelNode`, `ResponseCard`, `DashboardItem`)、纯展示组件 | 阻止 props 未变时的无关重渲染 |
| **useMemo** | 派生数据 (过滤/排序后的列表、Vega-Lite spec 构建) | 避免每次渲染重新计算 |
| **useCallback** | 事件回调 (onClick, onDragStart, onResize) 传入子组件 | 配合 React.memo 生效, 减少子组件重渲染 |
| **React.lazy + Suspense** | 建模画布、Dashboard 网格、管理后台、知识库 | 按页面分包, 首屏仅加载问答首页 |
| **IntersectionObserver** | Dashboard 图表懒加载、无限滚动列表 | 仅视口内组件渲染, 视口外占位 |
| **动态 import** | Vega-Lite (大型库 ~500KB)、CodeMirror 6 | 仅问答/图表页加载, 设置页不加载 |
| **CSS contain** | 推荐列表、模型树 | 限制布局/样式/绘制范围, 减少回流 |

#### 9.6.11 虚拟滚动策略

| 组件 | 虚拟化方案 | 预估元素 | 说明 |
|------|-----------|---------|------|
| 结果数据表 | `@tanstack/react-virtual` 行虚拟化 | >1000 行 | 固定表头, 双向滚动 |
| 审计日志 | `@tanstack/react-virtual` 行虚拟化 | >10000 行 | 无限滚动 + 分页混合 |
| Dashboard 列表 | `react-window` Grid | >50 面板 | 懒加载图表 (IntersectionObserver) |
| 推荐列表 | 原生 overflow + CSS contain | <20 条 | 简单溢出滚动 |
| 模型选择列表 | `@tanstack/react-virtual` | >200 模型 | 虚拟化下拉 |

#### 9.6.12 国际化 (i18n) 策略

| 层级 | 实现方式 | 说明 |
|------|---------|------|
| **UI 文案** | `next-intl` 或 `react-i18next` | 所有用户可见字符串通过 `t('key')` 调用 |
| **日期/数字** | `Intl.DateTimeFormat`, `Intl.NumberFormat` | 浏览器原生国际化 API |
| **LLM Prompt** | Skills 框架注入 `language` 变量 | 设置中语言 → 传递给 LLM, 输出对应语言 |
| **数据内容** | 不翻译 | 用户数据 (表名/列名) 保持原始语言 |
| **回退** | 英文 | 未翻译的 key 显示英文原文 |
| **文件结构** | `locales/{lang}/common.json` | 按页面/模块分文件, 避免单文件过大 |

当前 UI 翻译包支持 English (`en`) 与简体中文 (`zh`); 界面语言选择器展示世界主流语言并以原生语言名称呈现。未提供翻译包的 locale 会记录为终端偏好, UI 文案回退英文。
默认语言与界面语言已解耦: `metadata.settings.language` 仅表示系统默认语言, 用于登录页初始展示和 LLM 默认语言; `/settings` 的“界面语言”选项卡只写入当前浏览器/APP/移动端本地 `localStorage` 的 `i18n-store`, 不修改系统设置。General tab 的 Default Language 仍写入 `metadata.settings.language`, 当前限定为联合国六种工作语言: Arabic、Chinese、English、French、Russian、Spanish。

#### 9.6.13 无障碍 (a11y) 设计

| 要求 | 实现方式 | 适用范围 |
|------|---------|---------|
| **语义化 HTML** | 使用 `<nav>`, `<main>`, `<aside>`, `<dialog>`, `<table>` 等语义标签 | 整个应用 |
| **ARIA 属性** | `aria-label`, `aria-expanded`, `aria-current`, `role="dialog"`, `aria-live="polite"` | 导航、弹窗、动态内容 |
| **焦点管理** | 弹窗/抽屉打开时自动聚焦首操作项; Tab 键顺序遵循视觉顺序; Escape 关闭弹窗 | Modal, Drawer, Dialog |
| **键盘导航** | 所有交互支持键盘 (Tab, Enter, Space, Arrow, Escape); 建模画布支持快捷键 | 全应用 + 快捷键文档 |
| **色彩对比度** | WCAG 2.1 AA 标准 (对比度 ≥ 4.5:1); 不依赖颜色传递信息 | 主题、图表、状态指示 |
| **屏幕阅读器** | 动态内容更新使用 `aria-live` 区域; 图表提供文字摘要替代 | 问答流、图表、Toast |
| **触屏目标** | 可交互元素最小 44×44px; 移动端星形评分使用 CompactStarRating (大点击区域) | 移动端、推荐卡片 |
| **减少运动** | 遵循 `prefers-reduced-motion`; 关闭非必要过渡动画 | 图表切换、页面过渡 |

> a11y 基础支持在 Phase 1-2 随组件实现, 完整验收在 Phase 5 打磨阶段。

### 9.7 NL2SQL 路由引擎 (NL2SQL Router)

**当前实现**: 路由引擎是 Ask Service 从自然语言到 SQL 的核心管线, 实现完整的 3 层路由策略选择、Schema 剪枝和复合问题分解合并, 代码集中在 `backend/services/ask_service.py`。

#### 9.7.1 整体管线

```
用户提问
  │
  ▼
1. 语义命中: _semantic_prompt() — 对项目元数据做 token 匹配和加权排序,
   只将命中的模型/字段/关系注入上下文; 无命中时走普通 LLM 回答
  │
  ▼
 2. 问题分析: _analyze_question() — 调用 LLM 按 QUESTION_ANALYZER_CONTRACT
   将问题分类为 simple / multi_dimension / compound, 提取 entities/metrics/
   dimensions/filters/sub_questions
   └── LRU 缓存 (_analysis_cache, max 128 项) — 相同 (project_id, question) 跳过 LLM
   │
   ▼
 3. 问题路由: _classify_question_route() — 使用 QUESTION_ROUTING_CONTRACT
   判断是否需要 SQL; 若需 SQL 则将 metadata_question_part 交给 SQL 生成,
   non_metadata_question_part 补全后合成最终回答
   │
   ▼
 4. Schema 剪枝: _prune_schema() — 按分析结果的 entities/dimensions/metrics/
   filters 保留相关列、PK 列和关系连接列; 数值列对 metrics 宽保, 文本/日期列对
   dimensions 宽保; 无分析项时保持全量 schema
   │
   ▼
 5. 策略选择: _select_sql_strategy() — 按 analysis.tier 映射:
   - simple → direct_llm (直接 LLM 生成单条 SQL)
   - multi_dimension → fewshot_cot (多维度提示 + 链式思考)
   - compound → decompose_merge (分解成子查询后 LLM 合并)
   │
   ▼
 6. 策略执行 (重试循环, 带 error feedback):
   ├─ tier-1 direct_llm: 重试上限 ROUTER_CONFIG.tier1_max_retries 次
   ├─ tier-2 fewshot_cot: 重试上限 ROUTER_CONFIG.tier2_max_retries 次,
   │  带 multi-dimension 提示的 LLM 调用
   └─ tier-3 decompose_merge: 重试上限 ROUTER_CONFIG.tier3_max_retries 次
   │
   ▼
 7. 后处理验证 (通过 → 返回; 失败 → 修复 + 再验证; 仍失败 + 有剩余重试 → 收集错误反馈后重试):
   ├─ 孤儿 CTE 检查 (_validate_no_orphaned_cte) — 若存在则调用 _repair_sql 修复
   ├─ 列验证 (_validate_sql_columns) — 检查所有引用的列是否在命中模型定义中
   ├─ GROUP BY 完整性 (_validate_sql_group_by) — 检查分析维度是否出现在 GROUP BY (warn-only)
   └─ 聚合一致性 (_validate_sql_aggregation) — SELECT 中的裸列必须在 GROUP BY 中 (warn-only)
#### 9.7.2 问题分析合约 (QUESTION_ANALYZER_CONTRACT)

```json
{
  "tier": "simple|multi_dimension|compound",
  "sub_questions": ["列出...", "分析..."],  // 仅 compound 时非空
  "entities": ["products", "customers"],
  "metrics": ["revenue", "count"],
  "dimensions": ["city", "category"],
  "filters": [{"field": "date", "operator": ">=", "value": "2026-01-01"}],
  "reasoning": "分类理由..."
}
```

- **simple**: 1 个指标 + 0-1 个维度 → `direct_llm`
- **multi_dimension**: 1-2 个指标 + 1-2 个维度 → `fewshot_cot`
- **compound**: 多个子问题需要不同 GROUP BY 或 JOIN → `decompose_merge`

#### 9.7.3 Decompose & Merge 策略 (tier-3)

当 `analysis.tier == "compound"` 且 `analysis.sub_questions` 非空时触发:

```
Compound 问题
  │
  ├── 对每个子问题独立调用 LLM 生成 SQL
  │   (_decompose_merge_sql → _sub_sql 闭包)
  │   └── 每段 SQL 通过 _validate_sql_columns 验证列是否存在
  │
  ├── 若全部子查询失败 → 返回 None, 触发 fallback 到 direct_llm
  │
  ├── 若仅 1 个成功 → 直接返回该 SQL (跳过 merge 步骤)
  │   ├── 孤儿 CTE 修复
  │   ├── GROUP BY 警告
  │   └── 聚合一致性警告
  │
  └── 若 2+ 个子查询成功 → LLM 合并 prompt:
      ├── 传入原问题、schema 上下文和所有子查询 SQL
      ├── 要求产出单条 SQL 覆盖所有维度和指标
      ├── 合并 SQL 通过 _validate_sql_columns 验证
      ├── 孤儿 CTE 修复
      ├── GROUP BY 警告 + 聚合一致性警告
      └── 列验证失败 → 返回 None, 触发 fallback
```

**Fallback 行为**: 当 `_decompose_merge_sql()` 返回 `None` 时, `_generate_sql()` 将 `engine_label` 设为 `"direct_llm"` 并设置 `had_compound_fallback = True`, 使 `composite_hint` 和 `strategy_hint` 继续生效, 确保 LLM 知道它生成的是复合问题的单条 SQL。

#### 9.7.4 Schema 剪枝 (_prune_schema)

按问题分析结果动态裁减模型列定义以减少 token 消耗:

| 保留规则 | 优先级 | 说明 |
|---------|--------|------|
| 主键列 | 最高 | `col.is_primary_key == True` |
| 关系连接列 | 最高 | 被任何 relation 的 source_column / target_column 引用的列 |
| Token 匹配列 | 高 | 列名或 display_name 包含分析 terms 的任一 token (≥2 字符) |
| 数值列宽保 | 中 | 当 metrics 非空时, 保留所有数值类型列 (INTEGER/BIGINT/DOUBLE/FLOAT/DECIMAL/NUMERIC) |
| 文本/日期列宽保 | 中 | 当 dimensions/entities 非空时, 保留所有 VARCHAR/TEXT/DATE/TIMESTAMP 列 |
| 无关列 | 丢弃 | 不满足以上规则的列被移除 |

剪枝后, 仅保留含至少一个被保留列的模型, 并同步裁减引用已移除模型的关系。

#### 9.7.5 后处理验证器 + 修复再验证

| 验证器 | 触发条件 | 行为 |
|--------|---------|------|
| `_validate_no_orphaned_cte(sql)` | SQL 含 WITH 但存在未引用的 CTE | 通过 `_repair_sql` 修复 → 再验证; 若仍失败且有剩余重试次数, 收集错误后重试 |
| `_validate_sql_columns(sql, models)` | SELECT/FROM/JOIN 中存在不在命中模型定义的列 | 通过 `_repair_sql` 修复 → 再验证; 若仍失败且有剩余重试次数, 收集错误后重试 |
| `_validate_sql_group_by(sql, dimensions)` | 分析维度的列名未在 GROUP BY 中出现 | warn-only, 不阻塞 |
| `_validate_sql_aggregation(sql)` | SELECT 中有裸列 (非聚合函数) 但不在 GROUP BY 中 | warn-only, 使用 sqlglot AST 解析 |

聚合一致性验证使用 sqlglot 解析 SELECT 表达式, 对每个 `Column` 表达式 (非聚合函数参数、非子查询), 检查其列名是否出现在 GROUP BY 表达式中。不支持按位置 GROUP BY (如 `GROUP BY 1, 2`) 时返回空列表(无警告)。

修复再验证使用 `_repair_sql` 尝试修复后, 对输出再次运行原验证器: 若通过则立即返回修复结果; 若仍失败则根据 `tier` 映射的 `max_retries` (tier1=1, tier2=2, tier3=2) 决定是否重试整个生成。重试时, 前次失败原因 (孤儿 CTE 错误、未知列名等) 作为 error feedback 注入 LLM prompt, 指导下一次修正。

#### 9.7.6 ROUTER_CONFIG 常量

```python
ROUTER_CONFIG = {
    "tier1_max_retries": 1,
    "tier2_max_retries": 2,
    "tier3_max_retries": 2,
    "tier1_max_columns_per_model": 12,
    "tier2_max_columns_per_model": 15,
    "tier3_max_columns_per_model": 20,
    "max_sub_questions": 3,
    "max_suggested_questions": 5,
    "metadata_summary_max_models": 10,
    "guidance_llm_available": True,
    "schema_pruning_enabled": True,
}
```

定义于 `services/ask_config.py` 并可由 `/api/settings/router` 运行时覆盖。`max_retries` 对应各 tier 的最大 LLM 重试次数。`max_sub_questions` 限制 decompose-merge 子问题数 (超过截断)。`max_suggested_questions` 和 `metadata_summary_max_models` 限制无匹配引导时的推荐数和模型摘要数。`guidance_llm_available` 控制引导是否使用 LLM (False 时退化为模板引导)。`schema_pruning_enabled` 控制 Schema 剪枝是否启用。

#### 9.7.7 提示词合约

路由引擎使用 4 个独立提示词合约:

| 合约 | 位置 | 用途 |
|------|------|------|
| `QUESTION_ANALYZER_CONTRACT` | `ask_service.py:112` | 问题分类: tier / sub_questions / entities / metrics / dimensions / filters |
| `QUESTION_ROUTING_CONTRACT` | `ask_service.py:80` | 问题路由: 判断是否需要 SQL, 拆分 metadata 与 non-metadata 部分 |
| `SQL_RESPONSE_CONTRACT` | `ask_service.py:51` | SQL 生成: 列前缀、JOIN 类型、GROUP BY 完整性、CTE 语法、单 SQL 覆盖复合问题 |
| `FINAL_ANSWER_CONTRACT` | `ask_service.py:91` | 最终回答: 以 SQL 结果列和行作为唯一事实来源, 禁止输出 SQL/JSON/实现推理 |

#### 9.7.8 当前限制

- 聚合一致性验证 (`_validate_sql_aggregation`) 仍不支持 position-based GROUP BY (如 `GROUP BY 1, 2`)。
- 路由/修复链路复杂, 在极端跨源 SQL 与低质量元数据场景下仍可能触发 fallback/circuit open。
- 路由观测与告警参数较多, 仍需结合生产流量持续调优阈值与预算。

---

## 10. 多数据源架构

### 10.1 问题背景

旧 wren-ui 每个项目只能关联 **一个** 数据源。一个项目中的所有 MDL 模型都映射到同一数据库中的表。这对以下场景构成限制:

- 业务数据分布在 PostgreSQL + ClickHouse 等多个系统中
- 需要将 OLTP (MySQL) 数据与 OLAP (BigQuery) 数据联合分析
- 项目需要引用不同团队维护的不同数据源

PrismBI 彻底解除这一限制, 实现项目到数据源的 **多对多** 关系。

### 10.2 设计目标

1. **打破单数据源限制**: 一个项目可关联多个数据源, 模型可来自不同数据源
2. **两级管理**: 系统级数据源注册表 + 项目级绑定, 避免连接信息重复存储
3. **跨源查询**: 用户 SQL 可透明地跨多个数据源查询, 后端自动分源执行并合并
4. **数据隔离**: 临时中间数据与系统/项目元数据通过 DuckDB schema 严格隔离

### 10.3 两级管理数据模型

```
┌───────────────────────────────────────────────────────────────┐
│                    系统数据源注册表 (全局)                       │
│  datasources                                                   │
│  ┌─────┬──────────┬──────────────────┬──────────────────┐     │
│  │ ID  │  名称     │  类型             │  连接属性 (加密)   │     │
│  ├─────┼──────────┼──────────────────┼──────────────────┤     │
│  │ 1   │ 生产 PG  │ POSTGRES         │ host=...,port=.. │     │
│  │ 2   │ 分析 CK  │ CLICK_HOUSE      │ host=...,port=.. │     │
│  │ 3   │ 数仓 BW  │ BIGQUERY         │ project=...,key= │     │
│  └─────┴──────────┴──────────────────┴──────────────────┘     │
│                                                               │
│   ↑ 被项目引用 (多对多)                 ↑ 从项目添加时自动注册    │
│   ┌─────────────────────────────────┐                          │
│   │   project_datasources  (绑定表)   │                          │
│   │  ┌──────────┬──────────┬──────┐ │                          │
│   │  │ project  │ datasource│别名  │ │                          │
│   │  ├──────────┼──────────┼──────┤ │                          │
│   │  │ P1       │ 1 (PG)   │ 主库  │ │                          │
│   │  │ P1       │ 2 (CK)   │ 分析  │ │                          │
│   │  │ P2       │ 1 (PG)   │ 默认  │ │                          │
│   │  └──────────┴──────────┴──────┘ │                          │
│   └─────────────────────────────────┘                          │
└───────────────────────────────────────────────────────────────┘
```

### 10.4 DuckDB Schema 设计 (数据源相关)

```sql
-- Schema 规划
-- metadata schema: 系统元数据 (用户、设置、项目元数据等)
-- 业务表 (projects, users, threads 等) 在 §12.2 中定义
-- 权限表 (roles, permissions, audit_logs 等) 在 §11.5 中定义
-- 此处仅补充数据源相关表

-- 系统级: 数据源注册表 (连接信息加密存储)
CREATE TABLE metadata.datasources (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,              -- 用户可读的名称, 如 "生产 PostgreSQL"
    type VARCHAR NOT NULL,              -- POSTGRES, CLICK_HOUSE, BIGQUERY, DUCKDB, etc.
    properties_encrypted VARCHAR NOT NULL, -- 连接属性 (Fernet 加密 JSON)
    description VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 项目-数据源 多对多绑定
CREATE TABLE metadata.project_datasources (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    datasource_id INTEGER NOT NULL REFERENCES metadata.datasources(id),
    alias VARCHAR,                       -- 项目内别名, 如 "主库"
    config_overrides JSON,               -- 项目级覆盖配置 (catalog, schema 等)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, datasource_id)
);

-- Model 到数据源的映射 (扩展 MDL)
-- 每个模型可以指定来自哪个数据源, 不指定则使用项目默认数据源
-- 此信息存储在项目的 MDL 定义中 (YAML/JSON), 缓存在 DuckDB:
CREATE TABLE metadata.model_datasource_mappings (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    model_name VARCHAR NOT NULL,         -- MDL 中的模型名
    project_datasource_id INTEGER NOT NULL REFERENCES metadata.project_datasources(id),
    table_catalog VARCHAR,               -- 数据源中的 catalog
    table_schema VARCHAR,                -- 数据源中的 schema
    UNIQUE(project_id, model_name)
);
```

### 10.5 操作流程

#### 流程 A: 在系统设置中先添加数据源, 再分配到项目

```
系统设置 → 数据源管理 → 添加数据源
  1. 填写: 名称, 类型 (PG/CK/BQ...), 连接信息
  2. POST /api/system/datasources → 存入 datasources 表
  3. 数据源出现在系统列表中

项目设置 → 数据源 → 添加现有数据源
  1. 从系统列表中选择一个数据源
  2. 填写: 项目内别名, catalog/schema 覆盖 (可选)
  3. POST /api/projects/:id/datasources → 创建绑定
  4. 该数据源出现在项目的建模侧栏中, 模型可从该源选择表
```

#### 流程 B: 在项目设置中直接添加, 自动注册到系统

```
项目设置 → 数据源 → 新建数据源
  1. 填写: 名称, 类型, 连接信息 (同流程 A)
  2. POST /api/projects/:id/datasources/register
  3. 后端自动完成:
     3a. 创建系统数据源 (datasources)
     3b. 创建项目绑定 (project_datasources)
  4. 新数据源同时出现在系统列表和项目绑定中
```

当前元数据发现状态:
- DuckDB/sample discovery 返回 `table_details[].description` 与 `columns[].description`; DuckDB 会尽力读取 `duckdb_tables()/duckdb_columns()` comments。
- Sample Project 注册时会把 sample dataset 的表、列、关系描述写入 datasource properties 和建模元数据; 建模创建模型时将表描述写入 `metadata.models.description`, 字段描述写入 `column_defs[].description`。
- PostgreSQL discovery 读取 `obj_description/col_description`; MySQL 读取 `TABLE_COMMENT/COLUMN_COMMENT`; SQL Server 读取 `MS_Description`; ClickHouse 读取 `system.columns.comment`; Trino/Athena 当前主要返回表/列名和类型, comment 支持仍取决于后续 connector 能力。
- 建模属性面板支持查看/编辑模型、视图、关系、计算字段描述和字段描述; description 已进入 Sample Project 写入、非 Sample discovery comment/remarks 采集、Pydantic schema 与 CRUD 链路。

#### 流程 C: 跨源查询执行

```
用户 SQL: "SELECT o.order_id, c.name FROM orders o JOIN customers c ON o.cid = c.id"

前提:
  - orders 模型 → datasource A (PostgreSQL)
  - customers 模型 → datasource B (MySQL)

执行流程:

1. SQL 分析阶段:
   ├── 解析 SQL, 识别引用的模型/表
   ├── 查询 model_datasource_mappings
   ├── 确定 orders → PG, customers → MySQL
   └── 生成跨源执行计划

2. 分源提取阶段:
   ├── Source A (PG):  EXTRACT "SELECT order_id, customer_id FROM orders"
   ├── Source B (MySQL): EXTRACT "SELECT id, name FROM customers"
   └── 两个子查询互不依赖, 可并行执行

3. 数据拉取阶段:
   ├── 并行执行两个子查询
   ├── PG → Arrow Table → DuckDB temp.t_query1_session123.orders_sub
   ├── MySQL → Arrow Table → DuckDB temp.t_query1_session123.customers_sub
   └── 记录到 temp_query_registry 便于后续清理

4. SQL 重写阶段:
   ├── 将原 SQL 中 orders → temp.t_query1_session123.orders_sub
   ├── 将原 SQL 中 customers → temp.t_query1_session123.customers_sub
   └── 转换方言为 DuckDB SQL

5. 合并执行阶段:
   ├── DuckDB 本地执行重写后的 SQL
   ├── 利用 DuckDB 的向量化引擎做 JOIN
   └── 返回结果

6. 缓存管理阶段:
   ├── 标记临时表: 当前会话 + 创建时间
   ├── 策略 A: 会话结束立即清理 (默认)
   ├── 策略 B: 保留 5 分钟, 供 Dashboard 缓存复用
   └── 策略 C: 保留直到系统缓存压力触发清理 (LRU)

错误处理:

   跨源查询的异常场景与应对策略:

   场景 1: 部分数据源超时
     ├── 默认超时: 每个数据源 30s
     ├── 超时数据源: 返回空结果 + 错误标记
     ├── 其他数据源: 正常执行
     ├── 合并阶段: 跳过超时数据源, 返回部分结果
     └── 用户提示: "数据源 '分析 CK' 查询超时, 已返回其他数据源的部分结果"

   场景 2: 部分数据源连接失败
     ├── 重试策略: 自动重试 1 次 (指数退避 1s)
     ├── 重试失败: 标记为不可用, 不阻塞其他数据源
     └── 仪表盘: 对应面板显示 "数据源不可用" 标签

   场景 3: 所有数据源均失败
     ├── 返回 HTTP 502, 错误详情含各数据源失败原因
     └── 前端: 显示完整错误信息, 含各源状态

   场景 4: 跨源 SQL 解析失败
     ├── 退回单源模式: 尝试在默认数据源上直接执行
     ├── 若失败: 返回语义化错误, 提示用户调整 SQL
     └── 回退日志: 记录到 audit_logs 供后续分析

   场景 5: 表名归属模糊 (多数据源含同名表)
     ├── 按 model_datasource_mappings 的模型归属推断
     ├── 若仍模糊: 提示用户指定数据源前缀 "表名@数据源别名"
     └── 用户界面: 下拉选择框让用户指定表所属数据源

   场景 6: 跨源 JOIN 类型不兼容
     ├── DuckDB 自动类型提升 (INT→BIGINT, VARCHAR→TEXT 等)
     ├── 若类型无法兼容: 提示用户显式 CAST, 返回具体不兼容列
     └── 限制: 单次跨源查询最多涉及 5 个数据源, 超限退回单源模式
```

### 10.6 DuckDB Schema 隔离策略

```
系统 DuckDB 数据库: backend/data/prismbi.duckdb (可由 PRISMBI_DB_PATH 覆盖)
项目 DuckDB 数据源: backend/data/projects/{project_id}/{dbname}.duckdb

Schema 树:
├── metadata/              ← 系统 + 项目元数据 (永久)
│   ├── users
│   ├── projects
│   ├── datasources
│   ├── project_datasources
│   ├── model_datasource_mappings
│   ├── threads
│   ├── thread_responses
│   ├── dashboards
│   ├── dashboard_items
│   ├── instructions
│   ├── sql_pairs
│   ├── api_history
│   ├── api_tokens
│   ├── settings
│   ├── user_roles
│   ├── roles
│   ├── permissions
│   ├── role_permissions
│   ├── user_permission_overrides
│   ├── row_level_security_policies
│   ├── column_level_security_policies
│   ├── audit_logs
│   ├── recommended_questions_cache
│   ├── question_sql_catalog
│   ├── user_preference_hints
│   ├── interest_clusters
│   ├── recommendation_feedback
│   ├── recommendation_scores
│   └── layer_weight_history
│
├── cache/                 ← Dashboard 缓存 (TTL 驱动)
│   ├── dashboard_items_cache
│   └── query_results_cache
│
├── temp_<session_id>/     ← 会话级中间数据 (自动清理)
│   ├── q_<query_hash>_<source>  (每个跨源查询的子结果)
│   └── merged_<query_hash>       (合并后的最终临时表)
│
└── system/                ← 系统内部维护
    ├── temp_query_registry       (临时表注册, 用于清理追踪)
    └── schema_migrations        (迁移记录)
```

**隔离规则**:
| Schema | 生命周期 | 访问权限 | 清理策略 |
|--------|---------|---------|---------|
| `metadata` | 永久 | 所有服务 | 用户主动删除 |
| `cache` | TTL (默认 30min) | 所有服务 | 定时任务 + 缓存压力触发 |
| `temp_<session>` | 会话级 | 所属会话 | 会话结束立即清理; 异常断开 30min TTL |
| `system` | 永久 | 仅系统服务 | 仅由迁移操作修改 |

### 10.7 缓存与临时数据清理策略

#### Cache Schema 清理
cache/ 中的 dashboard_items_cache 和 query_results_cache 通过定时任务清理:
- TTL: 默认 30 分钟 (可通过设置调整)
- 触发: 每 5 分钟扫描 + Dashboard 缓存刷新时
- 策略: 删除 `cache_created_at < NOW() - INTERVAL '30 minutes'` 的记录

#### recommended_questions_cache 清理
推荐引擎缓存表独立于 Cache Schema, 通过 `expired_at` 列管理:
- TTL: 30 分钟 (与 §8.4 层 1 缓存策略一致)
- 清理时机: 每次读取时检查 `expired_at`; 定时任务每 30 分钟批量清理过期行
- SQL: `DELETE FROM metadata.recommended_questions_cache WHERE expired_at < NOW()`

#### 临时 Schema 清理

```python
# 清理策略伪代码
class TempDataCleaner:
    """临时数据清理器, 三种触发方式"""

    # 1. 主动清理 (会话结束)
    def on_session_end(session_id):
        duckdb.execute(f"DROP SCHEMA IF EXISTS temp_{session_id} CASCADE")

    # 2. 定时清理 (后台线程, 每 5 分钟)
    def scheduled_cleanup():
        # 查找所有 temp_* schema
        # 检查 session 心跳 (WebSocket ping/pong 时间戳)
        # 清理 30 分钟无心跳的 schema
        for schema in list_temp_schemas():
            if is_expired(schema, ttl=30min):
                duckdb.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    # 3. 按需清理 (API 触发)
    def cleanup_session(session_id):
        duckdb.execute(f"DROP SCHEMA IF EXISTS temp_{session_id} CASCADE")
```

### 10.8 LLM 生成 SQL 时的多源感知

当 LLM (通过 Skills 框架) 生成 SQL 时, 上下文需包含:

```
系统 Prompt 中补充:
  "当前项目关联了以下数据源:
   1. 生产 PG (PostgreSQL) - 包含 orders, customers, products 表
   2. 分析 CK (ClickHouse) - 包含 events, analytics 表
   
   如果你的 SQL 需要跨数据源查询, 请分别编写各源子查询,
   标记查询所属的数据源别名。"

生成的 SQL 结构:
  WITH
    -- 数据源: 生产 PG
    orders_data AS (SELECT id, amount FROM postgres.orders WHERE ...),
    -- 数据源: 分析 CK
    events_data AS (SELECT ... FROM clickhouse.events WHERE ...)
  SELECT * FROM orders_data o JOIN events_data e ON ...
```

---

## 11. 用户角色与权限管理子系统

### 11.1 设计目标

1. **基于 RBAC 模型**: 以角色为中心管理用户权限, 支持角色继承与细粒度控制
2. **三层访问控制**: 功能层 (Capability) + 数据层 (Data) + 行/列级 (RLS/CLS)
3. **与语义层集成**: 权限在 wren-engine MDL 语义层统一执行, 跨数据源一致
4. **审计驱动**: 所有权限变更和敏感操作记录审计日志
5. **可扩展**: 支持 SSO/OIDC 集成、临时权限、定期审查

### 11.0 当前实现状态

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| 本地认证 | 已实现 | `/api/auth/login`, `/api/auth/me`, bcrypt 密码哈希, JWT 24 小时有效; JWT 包含 `sid/jti/iat/exp`, 每次请求校验用户状态与 session 记录 |
| 权限种子 | 已实现 | 初始化时幂等写入默认 permissions、roles、role_permissions |
| 默认角色 | 已实现 | `super_admin`, `admin`, `viewer`, `project_admin`, `analyst` |
| 默认管理员 | 已实现 | 若不存在 `admin`, 默认可 bootstrap 创建并绑定 `super_admin`; 密码优先来自 `PRISMBI_ADMIN_PASSWORD`, 未设置时生成一次性随机密码并写入仅当前用户可读的 `data/bootstrap-admin-password` 或 `PRISMBI_BOOTSTRAP_ADMIN_PASSWORD_FILE`, 启动日志只打印文件路径; 公开固定密码 `admin123` 只有显式 `PRISMBI_ALLOW_DEFAULT_ADMIN_PASSWORD=true` 才允许 |
| 有效权限计算 | 已实现 | `user_roles -> role_permissions`, 支持 `user_permission_overrides` 的 ALLOW/DENY 与过期时间 |
| Admin API 授权 | 已实现 | `/api/admin/users`, `/roles`, `/permissions`, `/audit-logs`, `/sso`, `/system` 使用 `require_permission` |
| 前端权限缓存 | 已实现 | `/auth/login` 与 `/auth/me` 返回 `roles`/`permissions`, Zustand 缓存并驱动菜单/按钮显隐 |
| 审计日志 | 部分实现 | 登录、注册、用户/角色/权限/SSO/审计导出等敏感操作写入 `metadata.audit_logs` |
| 项目设置 | 已实现基础链路 | 数据源面板读取 `/api/projects/:id/datasources` 的项目绑定数据源, 与创建流程中的 datasource binding 信息一致; General 面板支持项目名、显示名、项目描述、项目提示词; 项目提示词默认注入, 创建项目时不要求用户填写; 页面标题在 Header 显示 `项目设置 >> <项目名>`, 内容区为圆角卡片 |
| 项目级成员 | 已实现基础链路 | 创建项目时自动把创建人写入项目级 `project_admin`; `/api/projects/:id/members` 提供成员列表/新增/更新/移除; 前端项目设置 Members tab 展示成员列表并保留基础新增表单, 权限细化仍待补强 |
| RLS/CLS | 部分实现 | 采用表单化规则方案 B; 已有策略表迁移、权限种子、Admin CRUD API、`dry-plan` 安全计划; `/api/query` 与 Ask 执行链路已应用安全计划和 CLS 结果过滤/掩码, 并禁止绕过语义模型名直接查询物理表; 复杂 SQL 中 RLS 谓词注入位置、CLS 别名/血缘追踪仍需继续加强 |
| SSO/OIDC | 已实现 (默认关闭) | `/api/admin/sso` 配置 + `/api/auth/sso/login|callback|token|cookie-token` 完整链路已实现; 默认 `enabled=false`, 开启后登录页按 `/api/settings/public` 显示 SSO 入口 |
| LLM 配置 | 部分实现 | 支持 OpenAI、Anthropic、GitHub Copilot、OpenCode Zen、MaxKB、Ollama、vLLM、自定义 OpenAI 兼容端点; 后端真实测试连接与模型列表拉取; 系统提示词已可配置; 高级 `extra_params` UI 待补充 |
| Home 聊天 | 部分实现 | 支持系统/项目/用户三层提示词; 打开空项目或项目无上下文时走普通 LLM 对话; 非空项目先做 metadata hit/miss 与问题分流, 命中部分走 SQL 执行, 未命中部分走 LLM 补全并合成最终回答; DuckDB/sample 和有限非 DuckDB/跨源本地 materialize 执行已接入; response 按 wren-ui detail shape 保存/展示; WS/SSE 真流式与 wren-engine SQL adapter 待补充 |
| Refresh Token/会话强制下线 | 已实现基础链路 | `/api/auth/refresh` 基于当前有效 JWT 延长同一 `sid` 会话; `/api/profile/sessions/:id/revoke` 会立即使对应 JWT 失效; 修改密码会撤销当前会话之外的其他会话; 管理员重置密码/禁用用户会撤销目标用户全部会话 |

### 11.2 权限模型架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    认证层 (Authentication)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ JWT 本地认证  │  │ OIDC/SSO    │  │ API Token / PAT      │   │
│  │ (bcrypt)     │  │ (Azure AD/Okta) │ (机器间认证)         │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │
│         └─────────────────┴─────────────────────┘               │
│                            ▼                                    │
│                    身份解析 → { user_id, roles, groups }         │
└──────────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│                    授权层 (Authorization)                        │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │  RBAC 引擎    │    │  RLS/CLS 引擎 │   │  审计日志     │       │
│  │               │    │              │    │              │       │
│  │  User → Role  │    │  行级过滤器   │    │  who/what/when│      │
│  │  Role → Perm  │    │  列级掩码    │    │  + 变更追踪   │       │
│  │  Role 层级    │    │  MDL 集成    │    │  + 报告导出   │       │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘       │
│         └──────────────────┴──────────────────┘                │
└──────────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│                   三层权限作用域                                  │
│                                                                  │
│  1. 功能层: 控制用户能做什么操作                                  │
│     ├── 项目管理: 创建/编辑/删除/导入/导出                        │
│     ├── 建模: 创建/编辑/删除模型/视图/关系/计算字段               │
│     ├── 问答: 提问/查看SQL/查看数据                               │
│     ├── Dashboard: 创建/编辑/删除/固定/管理缓存                   │
│     ├── 知识库: 管理 Instructions/SQL Pairs                       │
│     ├── 数据源: 管理系统级数据源/绑定项目数据源                   │
│     ├── 系统设置: 修改品牌/主题/LLM/通用设置                      │
│     └── 用户管理: 创建用户/分配角色/审计日志                      │
│                                                                  │
│  2. 数据层: 控制用户能看到哪些数据                                │
│     ├── 项目可见性: 哪些项目对用户可见/可访问                      │
│     ├── 数据源可见性: 哪些系统数据源可被项目绑定                  │
│     └── 模型可见性: 项目内哪些模型/视图对用户开放                 │
│                                                                  │
│  3. 行/列级: 精细到数据行和列                                    │
│     ├── RLS: 如 "销售经理只能看所属区域的订单"                    │
│     └── CLS: 如 "普通用户看不到 salary 列"                       │
└──────────────────────────────────────────────────────────────────┘
```

### 11.3 安全策略

#### 密码策略
| 规则 | 值 | 说明 |
|------|-----|------|
| 最小长度 | 8 位 | 注册/修改密码时校验 |
| 复杂度 | 大写+小写+数字 | 至少含 2 种字符类型 |
| 登录重试 | 5 次失败 → 锁定 15 分钟 | `users.status` 标记为 LOCKED 并记录 lockout_until |
| 会话过期 | 24 小时 | JWT 令牌过期后需重新登录 |
| 刷新令牌 | 同会话续期 | 当前实现没有独立 refresh token; `/api/auth/refresh` 需携带仍有效的 session-bound JWT, 并延长同一会话 |

### 11.4 角色体系设计

#### 11.4.1 预置角色

| 角色 | 层级 | 说明 | 典型用户 |
|------|------|------|---------|
| **超级管理员** `super_admin` | 系统 | 完全控制, 用户管理, 系统设置, 审计 | 初始 admin / IT 管理员 |
| **系统管理员** `admin` | 系统 | 当前实现中与 `super_admin` 同权限, 可后续收窄 | IT 管理员 |
| **项目管理员** `project_admin` | 项目 | 项目内完全控制, 管理项目成员和角色 | BI 团队负责人 |
| **分析师** `analyst` | 项目 | 建模编辑, 问答, Dashboard 创建/编辑, 知识库管理 | 数据分析师 |
| **浏览者** `viewer` | 系统 | 默认不授予任何项目读取权限; 自注册用户需由管理员加入具体项目并赋予项目级角色后才能访问项目资源 | 业务用户 |
| **受限浏览者** `restricted_viewer` | 规划 | 只读 + RLS/CLS 限制, 不可导出 | 外部/合规用户 |

#### 11.4.2 功能权限矩阵

| 功能模块 | super_admin/admin | system_operator(规划) | project_admin | analyst | viewer | restricted_viewer(规划) |
|---------|-----------|-------------|--------------|---------|--------|------------------|
| **系统: 用户管理** | ✅ 增删改 | ❌ | ❌ | ❌ | ❌ | ❌ |
| **系统: 角色管理** | ✅ 增删改 | ❌ | ❌ | ❌ | ❌ | ❌ |
| **系统: 数据源注册** | ✅ 全部 | ✅ 增删改 | ❌ | ❌ | ❌ | ❌ |
| **系统: 审计日志** | ✅ 全部 | ✅ 查看 | ❌ | ❌ | ❌ | ❌ |
| **系统: 系统设置** | ✅ 全部 | ❌ | ❌ | ❌ | ❌ | ❌ |
| **项目: 创建** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **项目: 删除** | ✅ | ❌ | ✅ (own) | ❌ | ❌ | ❌ |
| **项目: 绑定数据源** | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **建模: 创建/编辑模型** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **建模: 删除模型** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **建模: 创建/编辑关系** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **建模: 创建/编辑计算字段** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **问答: 提问** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **问答: 查看 SQL** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **问答: 查看原始数据** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Dashboard: 创建/编辑** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Dashboard: 查看** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (RLS过滤) |
| **Dashboard: 导出** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **知识库: 管理** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **知识库: 查看** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **数据导出** | ✅ 全部 | ✅ 系统 | ✅ 项目 | ✅ | ✅ | ❌ |

> 当前实现中的 permission resource/action 使用复数资源名: `projects`, `datasources`, `models`, `dashboards`, `knowledge`, `settings`, `admin`, `users`, `roles`, `permissions`, `audit_logs`, `sso`; action 使用 `create/read/update/delete/manage/export`。`admin:manage` 可覆盖后台管理能力。

### 11.5 DuckDB Schema (权限相关)

```sql
-- 用户 (增强自基础 schema)
-- 注意: 系统管理员权限通过 user_roles 表分配, 详见下方 RBAC 设计。
--       不设 is_sys_admin 独立字段, 避免与角色体系冲突。
CREATE TABLE metadata.users (
    id INTEGER PRIMARY KEY,
    username VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR NOT NULL,
    display_name VARCHAR,
    email VARCHAR,
    default_project_id INTEGER REFERENCES metadata.projects(id),
    last_login_at TIMESTAMP,
    status VARCHAR DEFAULT 'ACTIVE',   -- ACTIVE, INACTIVE, LOCKED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 角色 (系统级/项目级)
CREATE TABLE metadata.roles (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,              -- super_admin, admin, project_admin, analyst, viewer
    scope VARCHAR NOT NULL,             -- SYSTEM / PROJECT
    description VARCHAR,
    is_system BOOLEAN DEFAULT false,    -- 系统预置角色不可删除
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户-角色分配
CREATE TABLE metadata.user_roles (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES metadata.users(id),
    role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
    project_id INTEGER REFERENCES metadata.projects(id),  -- NULL = 系统级角色
    granted_by INTEGER REFERENCES metadata.users(id),
    expires_at TIMESTAMP,               -- 临时权限过期时间
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, role_id, project_id)
);

-- 功能权限定义
CREATE TABLE metadata.permissions (
    id INTEGER PRIMARY KEY,
    resource VARCHAR NOT NULL,           -- project, model, dashboard, datasource...
    action VARCHAR NOT NULL,             -- create, read, update, delete, export...
    description VARCHAR,
    UNIQUE(resource, action)
);

-- 角色-权限映射
CREATE TABLE metadata.role_permissions (
    id INTEGER PRIMARY KEY,
    role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
    permission_id INTEGER NOT NULL REFERENCES metadata.permissions(id),
    UNIQUE(role_id, permission_id)
);

-- 自定义权限 (为用户单独添加的权限, 超越角色)
CREATE TABLE metadata.user_permission_overrides (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES metadata.users(id),
    permission_id INTEGER NOT NULL REFERENCES metadata.permissions(id),
    project_id INTEGER REFERENCES metadata.projects(id),
    grant_type VARCHAR NOT NULL,        -- ALLOW / DENY
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 行级安全策略 (RLS)
CREATE TABLE metadata.row_level_security_policies (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
    model_name VARCHAR NOT NULL,        -- MDL 中的模型名
    column_name VARCHAR,                -- 方案 B: 表单选择列
    operator VARCHAR,                   -- =, !=, >, >=, <, <=, IN, NOT IN, LIKE, ILIKE
    value VARCHAR,                      -- literal 值或 JSON 数组字符串
    value_source VARCHAR DEFAULT 'literal', -- literal / user_attribute
    user_attribute VARCHAR,             -- 如 email, username, department
    filter_expression VARCHAR,           -- 兼容/展示用表达式, 不作为主要输入
    description VARCHAR,
    is_enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 列级安全策略 (CLS)
CREATE TABLE metadata.column_level_security_policies (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
    model_name VARCHAR NOT NULL,
    column_name VARCHAR NOT NULL,
    access_type VARCHAR NOT NULL,       -- MASK / HIDE
    mask_with VARCHAR,                  -- 如 "***" 或 "REDACTED"
    is_enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审计日志
CREATE TABLE metadata.audit_logs (
    id BIGINT PRIMARY KEY,              -- 当前实现由应用层生成递增 id
    user_id INTEGER REFERENCES metadata.users(id),
    event_type VARCHAR NOT NULL,        -- LOGIN, QUERY, EXPORT, PERM_CHANGE, etc.
    resource_type VARCHAR,
    resource_id VARCHAR,
    action VARCHAR,
    detail JSON,                        -- 请求详情, 变更前后对比
    ip_address VARCHAR,
    user_agent VARCHAR,
    status VARCHAR,                     -- SUCCESS / FAILURE / DENIED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 项目成员视图 (物化查询)
-- 用户可通过此视图快速了解自己在各项目的角色
```

### 11.6 RLS/CLS 与语义层集成

> 当前决策: RLS/CLS 采用方案 B, 即管理员通过表单配置 `model + column + operator + value/user_attribute`, 不直接让普通管理员手写 SQL 片段。当前代码已提供策略 CRUD API、`/api/query/dry-plan` 安全计划, 以及 DuckDB/sample 数据源的 `/api/query` 真实执行; 非 DuckDB 查询执行仍待接入跨源执行引擎。

表单化策略规则:
- RLS 支持操作符: `=`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `NOT IN`, `LIKE`, `ILIKE`。
- RLS 值来源: `literal` 固定值, 或 `user_attribute` 当前用户属性, 如 `username`, `email`, `default_project_id`。
- CLS 支持 `HIDE` 与 `MASK`; `MASK` 可配置 `mask_with`, 默认 `***`。
- 策略按用户当前系统/项目角色合并生效; 多条 RLS 条件以 AND 合并。
- 具备 `security_policies:*` 权限的管理员可查看/管理策略; 审计日志记录策略增删改。

```
PrismBI 中的权限执行链路:

1. 用户登录 → JWT (含 user_id + roles)
2. 每次 API 请求 → 中间件解析 JWT → 提取身份
3. 功能权限检查:
   └── RBAC 引擎: user_roles → role_permissions → 允许/拒绝
4. 数据权限检查:
   └── 查询 MDL 时自动注入 RLS/CLS
       ├── RLS: 在 MDL 模型的 table_reference 后追加 WHERE 条件
       │   └── 如: SELECT * FROM orders → SELECT * FROM orders WHERE region = 'APAC'
       └── CLS: 在查询结果返回前过滤/掩码列
           └── 如: salary 列 → 返回 "***" 或 NULL

关键设计: 权限在 wren-engine 的 MDL 层执行, 而非在数据库层
├── 优势: 跨数据源统一, 不依赖数据库自身权限系统
├── 优势: 可在 DuckDB 临时表层统一执行 (跨源查询时)
└── 优势: 审计日志可记录实际查询和过滤后的数据
```

### 11.7 审计日志

#### 11.7.1 审计事件分类

#### 11.7.2 审计功能

- **审计日志查看**: 系统管理员/操作员可查看和搜索审计日志
- **日志导出**: CSV/JSON 格式导出, 用于合规审查
- **保留策略**: 默认保留 90 天, 可配置
- **告警规则**: 可配置敏感操作告警 (如批量导出、权限变更)
- **清理机制**: 系统定时任务按保留策略清理过期记录; 管理员也可手动触发清理

### 11.8 SSO/OIDC 集成

> 当前决策: SSO/OIDC 默认不开启。仅超级管理员根据企业实际情况配置并启用后, 登录页才显示 SSO 入口。当前状态: `metadata.settings['sso_config']` 与 `/api/admin/sso` 已实现并受 `sso:read/update` 保护; `/api/auth/sso/login`、`/api/auth/sso/callback`、`/api/auth/sso/token`、`/api/auth/sso/cookie-token` 已落地, 包含 OIDC discovery、state/nonce、防重放、redirect URI 白名单、claim→role 映射与自动用户创建。

```
PrismBI 支持以下认证方式:

┌──────────────────────────────────────────────────────────┐
│  认证方式              │  适用场景                        │
├───────────────────────┼──────────────────────────────────┤
│  本地 JWT + bcrypt    │  单机/小团队, 默认方式             │
│  OIDC / OAuth 2.0     │  企业集成 (Azure AD, Okta, Keycloak)│
│  LDAP                 │  传统企业目录集成                  │
│  API Token (PAT)      │  机器间/自动化集成                 │
│  会话 Token (桌面端)   │  Tauri 桌面应用                   │
└──────────────────────────────────────────────────────────┘

OIDC 集成流程:
  1. 用户在登录页选择 "SSO 登录"
  2. 跳转到 OIDC Provider 登录页
  3. 认证成功后回调到 PrismBI
  4. 后端验证 ID Token, 提取用户信息
  5. 自动创建/匹配本地用户
  6. 返回 JWT (PrismBI 内部使用)
```

### 11.9 权限管理 UI

```
/admin/users               → 用户列表 (系统管理员)
/admin/users/:id           → 用户详情 (角色分配, 权限覆盖, 登录历史) [规划]
/admin/roles               → 角色列表 (可编辑自定义角色)
/admin/roles/:id           → 角色详情 (权限矩阵, 成员列表) [规划]
/admin/audit               → 审计日志 (搜索/过滤/导出)
/admin/sso                 → SSO 配置 (OIDC 端点, 客户端 ID, 映射规则)
/admin/security-policies   → RLS/CLS 安全策略管理

/projects/:id/settings     → 项目设置 (含 Members Tab)
                              添加/移除成员, 分配项目角色, 设置过期时间

/ 个人设置
/settings/profile          → 个人资料, 修改密码, API Token 管理
/settings/profile/sessions → 活跃会话管理
```

当前前端实现:
- `/admin/users`: 用户列表、创建、编辑、删除, 可分配一个主要角色; 按 `users:create/update/delete/manage` 控制按钮。
- 系统管理页面 (`/admin/users`, `/admin/roles`, `/admin/audit`) 内容区统一为圆角卡片, 右侧不再重复显示“用户管理/角色管理/审计日志”大标题; Header 显示 `系统管理 >> 用户管理/角色管理/审计日志`。
- `/admin/roles`: 角色列表、创建、编辑、删除; 创建/编辑 Drawer 可配置权限; 系统角色禁止删除; 按 `roles:create/update/delete/manage` 控制按钮。
- `/admin/audit`: 审计列表、筛选、导出; 导出按钮按 `audit_logs:export` 控制。
- `/admin/security-policies`: RLS/CLS 策略管理页面, 支持项目/角色筛选与 CRUD。
- `/admin/sso`: SSO/OIDC 配置页面, 支持 provider/issuer/client_id/client_secret/mapping_rules/enabled。
- Sidebar 中 Admin 入口按 `admin:read` 或 `admin:manage` 显示。

---

## 12. 多项目与 DuckDB 元数据存储

### 12.1 设计目标

1. **原生多项目支持**: 用户可以创建/切换/管理多个语义模型项目
2. **DuckDB 持久化**: 所有项目元数据存储在 DuckDB 中
3. **导出兼容**: 可将项目导出为 wren YAML 格式、JSON、CSV
4. **迁移路径**: 从旧版 SQLite/文件系统可迁移到 DuckDB

> **级联策略说明**: 线程删除时通过 `ON DELETE CASCADE` 自动清理关联回应; Dashboard 删除时同理清理面板项。项目级联删除暂不启用 (由应用层协调, 避免误删)。

### 12.2 DuckDB Schema 设计

```sql
-- 项目
CREATE TABLE metadata.projects (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    display_name VARCHAR,
    type VARCHAR,  -- 数据源类型 (对应 API 字段 type)
    connection_info JSON,
    catalog VARCHAR DEFAULT '',
    schema VARCHAR DEFAULT '',
    sample_dataset VARCHAR,
    language VARCHAR DEFAULT 'EN',
    version VARCHAR DEFAULT '1.0',
    is_current BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户 (完整定义见 §11.5, 此处列出常用列)
CREATE TABLE metadata.users (
    id INTEGER PRIMARY KEY,
    username VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR NOT NULL,
    display_name VARCHAR,
    email VARCHAR,
    default_project_id INTEGER REFERENCES metadata.projects(id),
    last_login_at TIMESTAMP,
    status VARCHAR DEFAULT 'ACTIVE',   -- ACTIVE / INACTIVE / LOCKED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 线程 (独立于项目, 但存在 project_id)
-- 项目删除时由应用层协调清理线程, 不启用 ON DELETE CASCADE
CREATE TABLE metadata.threads (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    summary VARCHAR DEFAULT '',
    summary_manual BOOLEAN DEFAULT false,
    user_id INTEGER REFERENCES metadata.users(id),
    preview_row_limit INTEGER DEFAULT 20,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 线程回应 (增加 user_id 追踪提问人)
CREATE TABLE metadata.thread_responses (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES metadata.threads(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES metadata.users(id),
    question VARCHAR NOT NULL,
    sql TEXT,
    asking_task_id VARCHAR,         -- wren-engine 异步任务 ID, 用于跟踪 SQL 生成进度
    breakdown_detail JSON,
    answer_detail JSON,
    chart_detail JSON,
    adjustment JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Dashboard
CREATE TABLE metadata.dashboards (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    name VARCHAR NOT NULL,
    cache_enabled BOOLEAN DEFAULT false,
    schedule_frequency VARCHAR,
    schedule_timezone VARCHAR DEFAULT 'UTC',
    schedule_cron VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Dashboard 面板
CREATE TABLE metadata.dashboard_items (
    id INTEGER PRIMARY KEY,
    dashboard_id INTEGER NOT NULL REFERENCES metadata.dashboards(id) ON DELETE CASCADE,
    type VARCHAR NOT NULL,  -- BAR, PIE, LINE, TABLE, NUMBER, etc.
    display_name VARCHAR,
    response_id INTEGER REFERENCES metadata.thread_responses(id) ON DELETE SET NULL,  -- 关联的回答
    chart_config JSON,      -- 图表配置: { spec, sql, columns, rows, preview_row_limit, source_response_id }
    layout_x INTEGER DEFAULT 0,
    layout_y INTEGER DEFAULT 0,
    layout_w INTEGER DEFAULT 3,
    layout_h INTEGER DEFAULT 2,
    cache_data JSON,         -- 缓存数据快照
    cache_created_at TIMESTAMP,
    cache_overridden_at TIMESTAMP,
    override BOOLEAN DEFAULT false
);

-- 模型 (存储在项目 MDL YAML/JSON 中, DuckDB 仅存元数据缓存)
CREATE TABLE metadata.models (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    name VARCHAR NOT NULL,
    display_name VARCHAR,
    description VARCHAR,
    table_reference VARCHAR,   -- 源表名
    source_binding_id INTEGER REFERENCES metadata.project_datasources(id),
    column_defs JSON,          -- 字段定义 [{ name, type, isPrimaryKey, displayName?, description?, expression? }]
    relation_defs JSON,        -- 关系定义 [{ name, sourceColumn, targetModel, targetColumn, type }]
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 视图 (基于模型的查询定义)
CREATE TABLE metadata.views (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    name VARCHAR NOT NULL,
    display_name VARCHAR,
    description VARCHAR,
    model_id INTEGER REFERENCES metadata.models(id), -- Save as View 可为空
    column_defs JSON,          -- 视图列定义 [{ name, expression, displayName?, description? }]
    sql TEXT,                  -- Ask Save as View 保存的 SQL 定义
    source_response_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 关系 (模型间关联定义)
CREATE TABLE metadata.relations (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    name VARCHAR NOT NULL,
    description VARCHAR,
    source_model_id INTEGER NOT NULL REFERENCES metadata.models(id),
    source_column VARCHAR NOT NULL,
    target_model_id INTEGER NOT NULL REFERENCES metadata.models(id),
    target_column VARCHAR NOT NULL,
    relation_type VARCHAR DEFAULT 'MANY_TO_ONE',  -- ONE_TO_ONE, ONE_TO_MANY, MANY_TO_ONE, MANY_TO_MANY
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 计算字段 (模型上的衍生字段)
CREATE TABLE metadata.calculated_fields (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    name VARCHAR NOT NULL,
    display_name VARCHAR,
    description VARCHAR,
    model_id INTEGER NOT NULL REFERENCES metadata.models(id),
    expression TEXT NOT NULL,
    result_type VARCHAR,    -- INTEGER, FLOAT, STRING, DATE, BOOLEAN
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 指令
CREATE TABLE metadata.instructions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    instruction TEXT NOT NULL,
    questions JSON DEFAULT '[]',
    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- SQL 问答对
CREATE TABLE metadata.sql_pairs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    sql TEXT NOT NULL,
    question VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- API Token (PAT: Personal Access Token)
CREATE TABLE metadata.api_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES metadata.users(id),
    name VARCHAR NOT NULL,               -- 用户可读的名称, 如 "CI/CD Token"
    token_hash VARCHAR NOT NULL,         -- 令牌的 SHA-256 哈希 (不存明文)
    token_prefix VARCHAR NOT NULL,       -- 令牌前 8 位, 用于用户识别 (如 "prism_ci_...")
    scope JSON DEFAULT '[]',             -- 权限范围, 如 ["query:read","project:export"]
    expires_at TIMESTAMP,                -- NULL = 永不过期
    last_used_at TIMESTAMP,
    is_revoked BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- API 历史 (UUID 主键, 便于分布式追踪)
CREATE TABLE metadata.api_history (
    id VARCHAR PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    api_type VARCHAR NOT NULL,
    thread_id INTEGER REFERENCES metadata.threads(id),
    headers JSON,
    request_payload JSON,
    response_payload JSON,
    status_code INTEGER,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 系统设置 (全局, 不隶属于特定项目)
CREATE TABLE metadata.settings (
    key VARCHAR PRIMARY KEY,
    value JSON NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 推荐引擎主表: 缓存各层生成的推荐候选
CREATE TABLE metadata.recommended_questions_cache (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    user_id INTEGER REFERENCES metadata.users(id),
    session_id VARCHAR,
    question TEXT NOT NULL,
    model_names VARCHAR[],           -- 关联的语义模型名
    recommend_type VARCHAR,          -- expand/drilldown/rollup/compare/related/followup/trend/anomaly
    score FLOAT,
    source VARCHAR,                  -- SCHEMA/SESSION/PROJECT/GLOBAL
    llm_explanation TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expired_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES metadata.projects(id)
);

-- 自学习 Catalog (ProxySQL 式): 问题-SQL 对自动积累
CREATE TABLE metadata.question_sql_catalog (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    sql_text TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,                   -- { modelRefs, chartType, sourceThreadId }
    verified BOOLEAN DEFAULT FALSE,  -- 人工审核标记
    FOREIGN KEY (project_id) REFERENCES metadata.projects(id)
);

-- 用户偏好 Hints (Odin 式): 跨会话的 Schema 映射记忆
CREATE TABLE metadata.user_preference_hints (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    hint_text TEXT NOT NULL,         -- 如 "用户说的'地名' → origin 列"
    source_query TEXT,               -- 产生该 hint 的原始查询
    confidence FLOAT DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES metadata.users(id)
);

-- 兴趣聚类 (IbR 式): 自动发现的用户兴趣簇
CREATE TABLE metadata.interest_clusters (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    cluster_name VARCHAR,
    cluster_embedding FLOAT[],       -- 簇中心向量
    member_queries TEXT[],            -- 簇内代表性查询
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 推荐反馈日志 (隐式反馈: 点击/忽略/悬停)
-- 与 recommendation_scores 的关系:
--   feedback: 记录用户隐式行为 (点击=接受, ✕=忽略)
--   scores:   记录显式评分 (1-5★)
--   用户点击推荐 → feedback(action=accept) 写入
--   用户忽略推荐 → feedback(action=dismiss) 写入
--   用户主动评分 → scores 写入 (同时可关联一条 feedback)
CREATE TABLE metadata.recommendation_feedback (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
    recommendation_id INTEGER,
    action VARCHAR,                   -- accept/dismiss/hover
    session_context TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES metadata.users(id)
);

-- 推荐评分明细 (显式评分反馈, 评分反馈闭环核心表)
-- 每条评分触发权重调整, 详见 §14.5 评分反馈学习
CREATE TABLE metadata.recommendation_scores (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    recommendation_id INTEGER,
    project_id INTEGER NOT NULL,
    source_layer VARCHAR,              -- SCHEMA/SESSION/PROJECT/GLOBAL
    recommend_type VARCHAR,            -- expand/drilldown/rollup/compare/related/followup/trend/anomaly
    score INTEGER NOT NULL CHECK (score >= 1 AND score <= 5),
    session_context TEXT,              -- 评分时的会话上下文
    source_question TEXT,              -- 被评分的推荐问题原文
    weight_adjustment FLOAT,           -- 该次评分触发的权重调整量
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES metadata.users(id),
    FOREIGN KEY (project_id) REFERENCES metadata.projects(id)
);

-- 来源层权重变更历史 (追踪评分对权重的影响)
CREATE TABLE metadata.layer_weight_history (
    id INTEGER PRIMARY KEY,
    source_layer VARCHAR NOT NULL,     -- SCHEMA/SESSION/PROJECT/GLOBAL
    previous_weight FLOAT,
    new_weight FLOAT,
    reason VARCHAR,                    -- score_feedback/auto_recover/manual
    triggered_by_score_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (triggered_by_score_id) REFERENCES metadata.recommendation_scores(id)
);

-- 默认设置 (部分在 §13.1 中另有说明)
INSERT INTO settings (key, value) VALUES
    ('app_name', '"PrismBI"'),
    ('app_description', '"您的商业智能平台"'),
    ('app_logo', '"/prismbi-icon.svg"'),
    ('app_icon', '"/prismbi-icon.svg"'),
    ('theme_mode', '"light"'),
    ('theme_primary_color', '"#1677ff"'),
    ('theme_font', '"Inter"'),
    ('llm_provider', '"openai"'),
    ('llm_model', '"gpt-4o"'),
    ('llm_endpoint', '"https://api.openai.com/v1"'),
    ('llm_max_tokens', '4096'),
    ('llm_temperature', '0.7'),
    ('llm_extra_params', 'null'),
    ('language', '"en"'),
    ('default_page', '"/home"'),
    ('auto_save', 'true'),
    ('telemetry_enabled', 'false'),
    -- 推荐引擎权重配置
    ('recommender_max_results', '5'),
    ('recommender_schema_weight', '0.22'),
    ('recommender_session_weight', '0.18'),
    ('recommender_user_weight', '0.13'),
    ('recommender_project_weight', '0.13'),
    ('recommender_global_weight', '0.08'),
    ('recommender_llm_weight', '0.08'),
    ('recommender_novelty_weight', '0.05'),
    ('recommender_score_weight', '0.13'),
    ('recommender_score_learning_rate', '0.05'),
    ('recommender_score_half_life_days', '14'),
    ('recommender_low_score_threshold', '2'),
    ('recommender_consecutive_low_alert', '5'),
    ('recommender_weight_auto_recover', 'true'),
    ('recommender_catalog_auto_learn', 'true'),
    ('recommender_llm_quality_check', 'true');

-- 用户会话 (JWT 签发记录, 用于活跃会话管理)
CREATE TABLE metadata.sessions (
    id VARCHAR PRIMARY KEY,              -- JWT sid / 会话 ID
    user_id INTEGER NOT NULL REFERENCES metadata.users(id),
    token_type VARCHAR DEFAULT 'bearer',
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    last_active_at TIMESTAMP,
    ip_address VARCHAR,
    user_agent VARCHAR,
    is_revoked BOOLEAN DEFAULT false
);

-- 以下为 cache./system. schema (非 metadata, 此处仅作记录)
-- cache.dashboard_items_cache: 由应用层按需创建 (TTL 驱动)
-- cache.query_results_cache: 查询结果缓存, 首次查询后建立
-- system.temp_query_registry: 跨源查询临时表注册
-- system.schema_migrations: DuckDB schema 版本管理
-- 以上四表由应用层自动管理, DDL 在代码中定义, 此处不重复列出
```

### 12.3 备份策略

DuckDB 元数据存储在 `backend/data/prismbi.duckdb` 单文件中, 可通过 `PRISMBI_DB_PATH` 覆盖。建议:
- **自动备份**: 系统定时任务每日备份至 `backups/prismbi-{date}.duckdb`
- **导出备份**: 用户可通过导出功能创建 JSON/YAML 快照
- **迁移**: 支持从旧版 SQLite 迁移, 迁移过程自动转换 schema

项目级 DuckDB 数据源与系统元数据库分离存放: `backend/data/projects/{project_id}/{dbname}.duckdb`。`dbname` 会清洗非法字符并自动补 `.duckdb` 后缀, 避免多个项目共享旧的用户目录或临时路径。

### 12.4 导出格式支持

| 格式 | 范围 | 用途 |
|------|------|------|
| **YAML (wren)** | MDL 定义 (模型/视图/关系) | wren-engine 原生兼容, 可被 `wren context init` 导入 |
| **JSON** | 完整项目 (含元数据、Dashboard、线程) | 通用备份/迁移 |
| **CSV** | 指定数据表 | 数据导出 |
| **SQLite** | 完整项目 | 旧版 wren-ui 迁移 |

### 12.5 多项目管理流程

```
┌─────────────────────────────────────────────────────┐
│                   项目管理                            │
│                                                      │
│  创建项目:                                           │
│  1. POST /api/projects { name, dataSource }          │
│  2. DuckDB 插入 project 记录                         │
│  3. wren-engine init project context                 │
│  4. 返回 project detail                             │
│                                                      │
│  切换项目:                                           │
│  1. POST /api/projects/:id/switch                   │
│  2. DuckDB 更新 projects.is_current 标志             │
│  3. 更新当前用户 users.default_project_id             │
│  4. 前端 projectStore 同步当前项目                    │
│  5. /modeling 按当前项目加载 diagram                  │
│                                                      │
│  导出项目:                                           │
│  1. POST /api/projects/:id/export?format=yaml        │
│  2. DuckDB → JSON → wren-engine 格式转换              │
│  3. 返回文件下载                                      │
│                                                      │
│  导入项目:                                           │
│  1. POST /api/projects/import (file upload)          │
│  2. 解析 YAML → DuckDB 写入                          │
│  3. wren-engine 加载项目                              │
│  4. 返回 project detail                             │
└─────────────────────────────────────────────────────┘
```

---

## 13. 系统设置

当前 UI 规范: `/settings`、`/settings/datasources`、`/settings/recommendations`、`/settings/recommendations/scores`、`/settings/profile`、`/settings/profile/sessions` 均沿用 Home/Dashboard/Knowledge 的圆角卡片内容区; 右侧内容区不重复显示页面大标题, Header 显示 `系统设置 >> 数据源/推荐设置/评分历史/个人资料/会话管理` 等标题。

系统设置安全策略: `/api/settings/public` 只返回品牌公开字段和系统默认语言 `language`; `/api/settings` 需要 `settings:read` 且返回前递归脱敏 `api_key/password/secret/token/client_secret` 等敏感项。LLM 与 SSO 保存时若前端回传 `********` 或未传 secret, 后端保留原有密钥而不覆盖为空; 测试连接/模型列表可在收到 mask 时使用已保存的密钥。当前存储层已引入 `services.crypto_service` 的 Fernet 加密, datasource `properties_encrypted`、LLM API key、SSO `client_secret` 所在配置会以 `enc:v1:` token 存储, 并在读取时兼容旧明文 JSON/字符串以便迁移。

语言设置当前实现: “界面语言”选项卡对所有登录用户开放, 不请求后端设置权限, 点击语言按钮只更新当前终端本地 `i18n-store`; “通用”选项卡的 Default Language 与界面语言不联动, 需要 settings 权限并写入系统级 `metadata.settings.language`。设置页的品牌、主题、LLM、通用四个选项卡仅在拥有对应 settings 读取权限时展示, 保存按钮需 settings 更新权限。

品牌设置 UI: 第一行使用三列卡片展示应用图标、网站图标、品牌色; 三张卡片内部都使用嵌套内容卡片规范, 去掉小字号英文说明。应用描述文案纳入 i18n, 中文为“应用描述”。系统初始化默认应用图标和网站图标均为 `/prismbi-icon.svg`; 登录页、主界面 Sidebar、浏览器标题/description/favicon 均读取系统设置中的 `app_name/app_description/app_logo/app_icon`, 保存品牌设置后通过全局 BrandingProvider/brandingStore 同步刷新。主题设置 UI: 第一行展示主题模式, 第二行使用外层卡片包裹三列卡片展示主题色、圆角大小、字体, 保持上下左右对齐。LLM 设置不再显示“LLM 提供方”内标题, 上方六个输入控件按三行卡片组织; System Prompt 文案纳入 i18n, 中文为“系统提示词”。通用设置不再显示“通用设置”内标题, 输入控件按两行卡片组织。

### 13.1 设置分类

| 分组 | 设置项 | 类型 | 说明 |
|------|--------|------|------|
| **品牌** | 应用名称 | string | 显示在标题栏/侧栏顶 |
| | 默认品牌图标 | SVG | 已提供 `public/prismbi-icon.svg` 和 `BrandMark` 组件, 应用于 favicon、登录页和主侧栏 |
| | 应用描述 | string | 默认 `您的商业智能平台`, 显示在品牌预览中, 可通过 Branding 设置修改 |
| | 系统 Logo | image upload | 左上角展示 (SVG/PNG) |
| | 系统图标 | image upload | Favicon / 桌面图标 |
| | 登录页背景 | image upload | 自定义登录页 |
| **主题** | 主题模式 | enum: light/dark/system | 亮色/暗色/跟随系统 |
| | 主色 | color picker | 品牌主色 (#1677ff) |
| | 字体 | string | 界面字体 |
| **LLM** | 提供商 | enum: openai/anthropic/ollama/vllm/github_copilot/opencode_zen/maxkb/自定义 | LLM 后端 |
| | API Key | password | 敏感返回值以 `********` 脱敏; 保存 mask 时保留原密钥 |
| | 模型 | string | 如 gpt-4o, claude-4, deepseek-coder |
| | Endpoint | string | 自定义 API 端点 (vLLM/Ollama/Copilot 等) |
| | 最大 Token | number | 生成限制 |
| | 温度 | number (0-2) | 生成随机性 |
| | 额外参数 | json | 按 provider 传入特有参数 (见 ↓ 详解) |
| **通用** | 默认语言 | enum: ar/zh/en/fr/ru/es | 系统默认语言; 与界面语言本地偏好不联动 |
| **界面语言** | 终端本地语言 | localStorage | 对所有用户开放; 仅影响当前浏览器/APP/移动端登录终端 |
| | 默认页面 | string | 登录后首页 |
| | Telemetry | boolean | 是否发送匿名数据 |
| | 自动保存 | boolean | 编辑时自动保存 |
| **导出/导入** | 导出项目 | action | YAML/JSON/CSV 下载 |
| | 导入项目 | file upload | 从文件恢复 |
| | 迁移旧版 | file upload | 从 SQLite/wren-ui 迁移 |
| **数据源** | 系统数据源列表 | table | 全局注册的数据源 (类型/名称/状态) |
| | 添加系统数据源 | form | 名称/类型/连接参数 (加密存储) |
| | 编辑系统数据源 | form | 修改连接参数 |
| | 删除系统数据源 | action | 删除前检查项目引用 |
| | 测试连接 | action | 验证连接可达性 |
| **系统** | 版本信息 | display | 前端/后端/引擎版本 |
| | 检查更新 | action | 版本更新检测 |
| | 缓存清理 | action | 清理临时/缓存数据 (清空 cache/ schema 过期数据) |

### 13.2 设置存储

- **全局设置**: DuckDB `settings` 表 (Key-Value)
- **用户偏好**: 本地 localStorage (主题、语言等 UI 偏好)
- **品牌资源**: 文件系统 `public/uploads/` 目录
- **凭据安全**: API Key 和敏感连接参数使用 `cryptography.fernet` 加密后存储; 优先读取 `PRISMBI_ENCRYPTION_KEY`, 否则使用 `PRISMBI_ENCRYPTION_KEY_FILE` 或 `backend/data/master.key` 自动生成的 0600 Fernet key。后续仍需补充正式 KMS/轮换方案。

### 13.3 LLM 提供商集成详解

| 提供商 | 协议 | 认证方式 | 默认 Endpoint | 模型示例 | 备注 |
|--------|------|---------|--------------|---------|------|
| **OpenAI** | OpenAI API | API Key | `https://api.openai.com/v1` | gpt-4o, gpt-4o-mini, o3 | 官方兼容 API |
| **Anthropic** | Anthropic API | API Key | `https://api.anthropic.com` | claude-4, claude-3.5-sonnet | Messages API (`/v1/messages`) |
| **GitHub Copilot** | OpenAI 兼容 API | GitHub Token (PAT) | `https://api.githubcopilot.com` | gpt-4o-copilot, claude-3.5-copilot | 需 GitHub Copilot 订阅 |
| **OpenCode Zen** | OpenAI 兼容 API | API Key | `https://zen.opencode.ai/v1` | zen-1, zen-1-mini | 专属 AI BI 优化模型 |
| **MaxKB** | OpenAI 兼容 API | API Key | `http://localhost:8080/v1` | maxkb | 按 OpenAI-compatible `/chat/completions` 和 `/models` 接入; endpoint/model 可覆盖 |
| **Ollama** (本地) | OpenAI 兼容 API | 免认证 | `http://localhost:11434/v1` | llama-3.1, qwen2.5, deepseek-r1 | 本地部署, 需用户自行启动 |
| **vLLM** (本地) | OpenAI 兼容 API | 可选 API Key | `http://localhost:8000/v1` | deepseek-coder, mixtral, qwen2.5 | 高性能推理引擎 |
| **自定义** | OpenAI 兼容 API / 自定义 | 可选 | 用户自定义 URL | 任意 | 兼容任意 OpenAI 格式服务 |

**核心集成模式**: 所有提供商统一使用 **OpenAI 兼容 API 协议** (除 Anthropic 使用 Messages API, 后端做适配), 后端抽象为统一的 `LLMClient` 接口:

当前实现状态:
- `backend/services/llm_service.py` 已提供统一 `LLMService.chat()`。
- 已支持 provider: `openai`, `anthropic`, `github_copilot`, `opencode_zen`, `maxkb`, `ollama`, `vllm`, `custom`。
- `settings.py` 保存 `llm_provider`, `llm_api_key`, `llm_model`, `llm_endpoint`, `llm_max_tokens`, `llm_temperature`, `llm_extra_params`, `llm_system_prompt`; `/api/settings/llm/test` 调真实 provider 做 ping 测试并向前端返回具体错误。
- `/api/settings` 返回时不泄露 `llm_api_key`; 前端显示 mask, 保存 mask 时后端跳过密钥更新, 测试连接/模型列表在收到 mask 时读取已保存密钥。
- `/api/settings/llm/models` 可从 Base URL 查询模型列表并回填前端模型下拉; OpenAI 兼容端点使用 `/models`, Ollama 使用 `/api/tags`, Anthropic 使用内置模型列表。
- 前端 `LLMSettings.tsx` 已暴露上述 provider 选择、endpoint、model 下拉、api key、系统提示词与真实测试连接; `extra_params` UI 仍待补充为高级参数编辑器。
- 系统提示词作为全局 AI 行为约束, 支持 `{{app_name}}/{{language}}/{{timezone}}/{{date_format}}/{{llm_provider}}/{{llm_model}}/{{current_date}}/{{current_datetime}}` 变量。

```
后端 LLM Client 抽象层:
  ┌──────────────────────────────────────────────────┐
  │               LLMClient (ABC)                      │
  │  + chat(messages, model, params) → AsyncIterable  │
  │  + embed(texts, model) → List[float]              │
  └──────────────────────────────────────────────────┘
          ▲           ▲           ▲           ▲
          │           │           │           │
  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
  │OpenAIClient│ │Anthropic │ │Copilot   │ │CustomClient  │
  │           │ │Client   │ │Client    │ │(OpenAI格式)  │
  └───────────┘ └──────────┘ └──────────┘ └──────────────┘
```

**各提供商特有参数 (`extraParams`)**:

| 提供商 | 参数 | 说明 |
|--------|------|------|
| **GitHub Copilot** | `github_token` | GitHub Personal Access Token (替代 apiKey) |
| | `copilot_org` | GitHub Copilot Business organization (可选) |
| **Ollama** | `keep_alive` | 模型保持内存时间 (默认 5m) |
| | `num_ctx` | 上下文长度 (默认 4096) |
| | `num_predict` | 最大生成 token (默认 -1 不限) |
| **vLLM** | `trust_remote_code` | 是否信任远程模型代码 |
| | `guided_json` | JSON Schema 约束 (结构化输出) |
| **OpenCode Zen** | `zen_mode` | zen 优化模式 (默认 analysis) |

**Skills 框架与提供商解耦**: Skills 框架中的 LLM 指令集通过 `LLMClient` 接口调用, 不关心具体提供商。用户更换提供商或在本地/Ollama 间切换时无需修改任何 Skills 定义。

### 13.4 设置 UI

```
/settings 页面
├── 侧边导航
│   ├── Branding & Theme    (品牌与主题)
│   ├── Data Sources        (数据源管理 - 两级)
│   │   ├── 系统数据源列表   (全部注册的数据源)
│   │   └── 添加系统数据源   (填写连接信息)
│   ├── LLM Provider        (AI 模型)
│   ├── Recommendation      (推荐引擎)
│   ├── General             (通用)
│   ├── Export & Import     (导出/导入)
│   └── About               (关于)
│
└── 内容区 (根据选中导航切换)

/项目设置 页面
├── 项目数据源
│   ├── 已绑定的数据源列表
│   ├── "绑定系统数据源" 按钮
│   └── "新建并绑定" 按钮 (自动注册到系统)
├── 项目信息
└── 导出项目
```

### 13.3 环境变量与配置

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `PRISMBI_DB_PATH` | `backend/data/prismbi.duckdb` | DuckDB 系统元数据文件路径 |
| `JWT_SECRET_KEY` | `prismbi-dev-secret-change-in-production` | JWT 签名密钥; 生产环境必须覆盖 |
| `PRISMBI_ENV` / `ENV` | 空 | 设置为 `production`/`prod` 时禁止默认 JWT secret, 除非显式允许 |
| `PRISMBI_ALLOW_DEFAULT_SECRET` | `false` | 生产环境是否允许默认 JWT secret; 仅开发调试使用 |
| `PRISMBI_ENABLE_REGISTRATION` | `false` | 是否开放公开自注册 |
| `PRISMBI_SEED_ADMIN` | `true` | 首次启动是否自动创建 `admin` bootstrap 用户 |
| `PRISMBI_ADMIN_PASSWORD` | 空 | bootstrap admin 初始密码; 未设置时生成随机一次性密码并输出到启动日志 |
| `PRISMBI_ALLOW_DEFAULT_ADMIN_PASSWORD` | `false` | 是否允许显式使用 `admin123`; 仅开发调试使用 |
| `PRISMBI_ENCRYPTION_KEY` | (从 `master.key` 读取) | 数据源凭据加密 Fernet 密钥 |
| `PRISMBI_HOST` | `0.0.0.0` | FastAPI 监听地址 |
| `PRISMBI_PORT` | `8400` | FastAPI 端口 |
| `PRISMBI_CORS_ORIGINS` | `http://localhost:5173` | 允许的前端来源 (逗号分隔) |
| `PRISMBI_RATE_LIMIT` | `100` | 默认速率限制 (req/min) |
| `PRISMBI_LOG_LEVEL` | `INFO` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `PRISMBI_MAX_UPLOAD_MB` | `50` | 文件上传大小限制 (MB) |
| `PRISMBI_BACKUP_DIR` | `backend/data/backups` | DuckDB 备份目录 (当前实现固定于 `services/backup_service.py` 的 `DATA_DIR/backups`, 暂未暴露环境变量覆盖) |
| `WREN_ENGINE_ENDPOINT` | `http://localhost:8000` | wren-engine 引擎地址 |

> 以上变量可通过环境变量或 `.env` 文件配置。首次启动时自动创建 `backend/data/prismbi.duckdb` 和 metadata schema。

---

## 14. 主动推荐引擎

### 14.1 设计目标

1. **主动发现**: 根据用户当前上下文和历史行为, 主动推荐下一步探索方向
2. **四层递进**: Schema 驱动 → 会话级 → 项目级 → 全局级, 数据越丰富推荐越精准
3. **冷启动无感**: 零历史数据时即可通过 MDL 语义模型生成基础推荐
4. **自学习**: 通过 ProxySQL 式 Catalog 自动积累问题-SQL 对, 越用越准
5. **可解释**: 每条推荐附带来源说明和推荐类型标签
6. **评分反馈闭环**: 用户可对推荐进行评分 (1-5星), 系统根据评分动态调整推荐权重和排序
7. **可配置**: 管理员可调整各推荐层权重、评分影响因子、以及 Catalog 审核策略

### 14.2 推荐架构

参考 12 个行业方案调研结论 (详见 `RESEARCH_COMPARISON.md`), 采用**四层渐进式推荐引擎**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PrismBI 主动推荐引擎                               │
│                                                                     │
│  层 0: Schema 驱动 (Schema-driven) — 立即可用, 零数据依赖              │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │  参考: Snowflake LLM Generation 模式                        │      │
│  │  输入: 当前项目 MDL 语义模型 (模型/度量/维度/关系)           │      │
│  │  输出: 模板化推荐问题                                       │      │
│  │  └─ "按 {dim} 查看 {measure}"  / "查看 {modelA} 关联数据"   │      │
│  └───────────────────────────────────────────────────────────┘      │
│                           ↓ 数据积累                                  │
│  层 1: 会话级 (Session-level) — 3 小时后生效                          │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │  参考: Enterprise QG 分类体系 + CTR-GQS 共现查询            │      │
│  │  输入: 当前会话历史 + AI 回复 + 同 Session 共现问题          │      │
│  │  输出: Expansion / Follow-up 推荐问题                       │      │
│  │  └─ "月销售额" → "按地区分解" (Expand)                       │      │
│  │  └─ "返回了 SQL 结果" → "生成图表" (Follow-up)              │      │
│  └───────────────────────────────────────────────────────────┘      │
│                           ↓ 数据丰富                                  │
│  层 2: 项目级 (Project-level) — 3 天后生效                            │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │  参考: ProxySQL Catalog + IbR 兴趣聚类 + TailorSQL Hints    │      │
│  │  输入: 项目内历史查询 + 自学习 Catalog + 兴趣簇              │      │
│  │  输出: 热门查询 / 兴趣关联 / 工作负载 Hint 推荐               │      │
│  │  └─ 项目内 Top-N 高频问题 (去重+新颖性过滤)                  │      │
│  │  └─ 当前上下文 → 匹配兴趣簇 → 簇内推荐                      │      │
│  │  └─ Catalog: { question, sql, frequency } 自动积累          │      │
│  └───────────────────────────────────────────────────────────┘      │
│                           ↓ 数据规模化                                 │
│  层 3: 全局级 (Global-level) — 2 周后增强                             │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │  参考: CFQP 图传播 + Odin 偏好学习 + Snowflake 模型优化     │      │
│  │  输入: 全量用户 + 全量项目数据                               │      │
│  │  输出: 协同过滤推荐 / 个性化 Hints / 语义模型优化建议         │      │
│  │  └─ 相似用户 → 图传播偏好 → 跨用户推荐                      │      │
│  │  └─ "hometown" → origin 列 (Odin 式跨会话 Hints)            │      │
│  │  └─ 建议新增度量/过滤器/同义词 (Snowflake 式优化)            │      │
│  └───────────────────────────────────────────────────────────┘      │
│                                                                     │
│  输出: 每次问答返回 3-5 条推荐 + 分类标签 + 来源说明                   │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │  评分反馈闭环:                                                │      │
│  │  用户评分 (1-5星) → 写入 recommendation_scores              │      │
│  │   → 更新 Catalog/Schema 层权重 → 影响下次排序              │      │
│  │   → 阈值触发: 低评分 (< 2) 自动降低来源层权重               │      │
│  │   → 高分推荐 (≥ 4) 提升 Catalog 优先级                      │      │
│  └───────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

### 14.3 推荐分类体系

| 类型 | BI 含义 | 示例 | 典型来源层 |
|------|---------|------|-----------|
| **Expand** (扩展) | 当前主题的相关维度/度量 | "月销售额" → "按地区分解" | 层 0, 层 1 |
| **Drill-down** (下钻) | 当前维度的细化 | "全国销售" → "华东区销售" | 层 0, 层 2 |
| **Roll-up** (上卷) | 当前维度的汇总 | "各城市销售" → "各省汇总" | 层 0 |
| **Compare** (对比) | 时间/维度对比 | "本月销售" → "环比上月" | 层 0, 层 1 |
| **Related** (关联) | 关联模型数据 | "订单" → "查看客户分析" | 层 0 |
| **Follow-up** (跟进) | 基于回复的下一步 | SQL 结果返回 → "生成图表" | 层 1 |
| **Trend** (趋势) | 时间序列分析 | "销售额" → "近 12 月趋势" | 层 0, 层 2 |
| **Anomaly** (异常) | 发现异常值 | "产品销量" → "上月异常下降" | 层 2, 层 3 |

### 14.4 排序策略

```
最终得分 = Σ (各层得分 × 对应权重)

各层得分:
  S_schema   = 层 0 分: MDL 语义模型匹配度 (模型/度量/维度覆盖)
  S_session  = 层 1 分: 会话上下文相关度 (与当前问题语义相似度)
  S_user     = 层 2+3 用户分: 个人历史偏好 + Odin Hints 匹配 (跨 source_layer 综合分)
  S_project  = 层 2 项目分: 项目内热度 + IbR 兴趣转移概率
  S_global   = 层 3 全局分: CFQP 图传播的相似用户偏好
  S_llm      = LLM 质量分: 对候选问题可回答性的 LLM 评分
  S_novelty  = 新颖性分: 1 - cosine_sim(candidate, recent_questions)
  S_score    = 用户评分分: 基于同类推荐历史评分的加权平均 (归一化 0-1)

评分分计算:
  S_score = weighted_avg(同类推荐历史评分)
      权重 = exp(-0.5 × days_since_rating)  (越近的评分影响越大)
    × 来源层衰减因子 (低评分 → 降低该来源层下次权重)

最终排序:
  rank = 0.22 × S_schema + 0.18 × S_session + 0.13 × S_user
       + 0.13 × S_project + 0.08 × S_global + 0.08 × S_llm
       + 0.05 × S_novelty + 0.13 × S_score
```

权重可在系统设置中由管理员调整。

### 14.5 自学习机制

**Catalog 自动积累** (参考 ProxySQL):

```
用户接受推荐 → 异步写入 question_sql_catalog
  ├── question: 推荐问题原文
  ├── sql_text: 该次问答生成的最终 SQL
  ├── frequency: +1
  └── metadata: { modelRefs, chartType, sourceThreadId }

下次匹配:
  GET /api/recommendations
    → 语义检索 question_sql_catalog
    → 匹配度 > 0.85 → 直接复用 (高可信度, 标注 "catalog")
    → 匹配度 0.60-0.85 → 作为候选参与排序
    → 匹配度 < 0.60 → 走正常推荐管道
```

**偏好学习** (参考 Odin):

```
用户选择 SQL → 提取 Schema 映射:
  用户说 "地名" → 选择的 SQL 用了 origin 列
  → 写入 user_preference_hints: "用户说的'地名' → origin 列"

下次提问含 "地名" → 注入该 Hint 到 LLM Prompt:
  "根据该用户历史偏好, '地名' 通常对应 origin 列"
```

**评分反馈学习**:

```
用户评分流程:
  每次推荐展示时附带 1-5 星评分器
  ├── 5★ (非常有用): +0.3 权重加成, 立即写入 Catalog (若不在其中)
  ├── 4★ (有用): +0.1 权重加成
  ├── 3★ (一般): 无权重变化
  ├── 2★ (不太有用): -0.1 权重衰减, 降低同类推荐在该来源层优先级
  └── 1★ (完全无关): -0.3 权重衰减, 触发管理员通知 (若同一来源层连续 5 次低评分)

评分 → 权重调整:
  ΔW_layer = α × (avg_score - 3) × learning_rate
  其中:
    α = 0.05 (调整步长)
    avg_score = 该来源层最近 20 条评分的移动平均
    learning_rate = 0.1 (初始) → 衰减至 0.01 (稳态)

  上限: 单层权重变化不超过 ±0.10 (防止单次异常评分剧烈抖动)
  下限: 单层权重 ≥ 0.02 (保证各层至少保留基础影响)

评分 → Catalog 排序影响:
  high_score_count (4★+ 次数) 作为 Catalog 条目的额外排序因子
  S_catalog_boost = min(high_score_count / 10, 0.5)
  Catalog 最终分 = baseline_score × (1 + S_catalog_boost)
```

**评分衰减与冷启动**:

```
新条目冷启动:
  - 新生成的推荐问题: 初始 S_score = 0.5 (中性偏正)
  - 获得 3 次以上评分后 → 转换为实际评分均值

评分时间衰减:
  - 评分时间权重: w(t) = exp(-λ × t), λ = 0.05 (约 14 天半衰期)
  - 90 天前的评分不再纳入 S_score 计算

来源层权重自动恢复:
  - 若某来源层连续 7 天未收到低评分 (< 3), 自动恢复 50% 衰减的权重
```

### 14.6 存储设计

DuckDB 表已在 §12.2 中定义, 核心表包括:

| 表 | 用途 | 数据来源 | TTL |
|----|------|---------|-----|
| `recommended_questions_cache` | 各层生成的推荐候选缓存 | 推荐引擎 | 30 min |
| `question_sql_catalog` | 自学习问题-SQL 对 | 用户接受的推荐 → 写入 | 永久 |
| `user_preference_hints` | Odin 式 Schema 映射 Hints | 用户 SQL 选择 → 提取 | 永久 |
| `interest_clusters` | IbR 式兴趣聚类 | 周期性 Batch 聚类 | 每周更新 |
| `recommendation_feedback` | 用户反馈日志 (含评分) | 隐式/显式反馈 | 90 天 |
| `recommendation_scores` | 用户评分明细 + 权重调整记录 | 用户评分 → 系统调整 | 永久 |

LanceDB 向量存储:

| Collection | 用途 | 维数 |
|-----------|------|------|
| `project_query_patterns` | 项目查询模式向量 (TailorSQL Hints 检索) | 768 |
| `user_interest_vectors` | 用户兴趣向量 (CFQP 图传播) | 768 |
| `catalog_embeddings` | Catalog 问题嵌入 (语义检索匹配) | 768 |

### 14.7 前端交互设计

```
推荐展示位置:
┌──────────────────────────────────────────┐
│  问答主界面                                │
│                                            │
│  ┌──────────────────────────────────────┐ │
│  │  问题输入框                            │ │
│  │  [在此输入问题...]  [发送]            │ │
│  └──────────────────────────────────────┘ │
│                                            │
│  推荐区 (折叠面板):                        │
│  ┌─ 你可能还想了解 ──────────────────────┐ │
│  │  🔍 Expand  按地区分解月销售额        │ │
│  │  📊 Compare  环比上月对比             │ │
│  │  📈 Trend    近 12 月趋势看板         │ │
│  │  🔗 Related  查看客户分析             │ │
│  │  [更多推荐 →]                        │ │
│  └──────────────────────────────────────┘ │
│                                            │
│  ┌──────────────────────────────────────┐ │
│  │  对话历史区域                          │ │
│  └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘

每条推荐展示:
  [图标] [类型标签] [问题文本] [来源: Schema/项目热门/Catalog] [☆☆☆☆☆]
  用户交互:
    ├── 点击 → 直接提问 (隐式 positive 反馈)
    ├── 悬停 → 预览 SQL / 图表
    ├── ✕ → 忽略 (隐式 negative 反馈)
    └── 评分 (1-5★):
        ├── 点击星级 → 立即提交评分
        ├── 评分后星标变色 (★ 已评 / ☆ 未评)
        ├── 可重新评分 (覆盖上次)
        └── 不评分不影响推荐展示

评分器交互细节:
  ┌──────────────────────────────────────────────┐
  │  📈 Trend  近 12 月趋势看板                   │
  │  来源: 项目热门  ★★★★☆  4.2 分 (15 人评分)  │
  │                                              │
  │  点击 1★: "完全不相关"                        │
  │  点击 2★: "不太有用"                          │
  │  点击 3★: "一般"                              │
  │  点击 4★: "有用"                              │
  │  点击 5★: "非常有用, 解决了我的问题"           │
  └──────────────────────────────────────────────┘
```

### 14.8 设置项

在系统设置中新增 `推荐引擎` 分组:

| 设置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| 最大推荐数 | number | 5 | 每次问答展示的推荐数量 |
| Schema 权重 | float (0-1) | 0.22 | 层 0 来源权重 |
| 会话级权重 | float (0-1) | 0.18 | 层 1 来源权重 |
| 用户权重 | float (0-1) | 0.13 | 层 2+3 用户个性化权重 |
| 项目权重 | float (0-1) | 0.13 | 层 2 项目热度权重 |
| 全局权重 | float (0-1) | 0.08 | 层 3 协同过滤权重 |
| LLM 质量分权重 | float (0-1) | 0.08 | LLM 对候选的评分权重 |
| 新颖性权重 | float (0-1) | 0.05 | 去重/多样性权重 |
| 用户评分权重 | float (0-1) | 0.13 | 历史评分对排序的影响权重 |
| 评分学习率 | float (0-0.1) | 0.05 | 评分反馈的权重调整步长 α |
| 评分半衰期 | number (天) | 14 | 评分时间衰减 λ 的半衰期天数 |
| 低评分阈值 | number (1-5) | 2 | 低于此值视为低评分, 触发权重衰减 |
| 连续低评分告警 | number | 5 | 同一来源连续 N 次低评分 → 通知管理员 |
| 权重自动恢复 | boolean | true | 低评分来源层 7 天未再低评 → 自动恢复 |
| Catalog 自动学习 | boolean | true | 接受推荐是否自动写入 Catalog |
| LLM 质量检查 | boolean | true | 是否用 LLM 校验推荐问题可回答性 |

---

## 15. 多平台打包

### 15.1 Desktop (桌面端) — Tauri 2.x

| 维度 | 选型说明 |
|------|----------|
| **框架** | Tauri 2.x (Rust) — 比 Electron 小 10x, 内存少 5x |
| **优势** | 项目已有 Rust 代码库 (wren-core), 共享 Rust 生态 |
| **窗口** | 主窗口 + 系统托盘 (后台运行) |
| **更新** | tauri-updater 内置更新机制 |
| **目标** | Windows (MSI/EXE), Linux (AppImage/DEB), macOS (DMG) |

**Tauri 配置要点**:
- 内嵌 Next.js 静态构建输出
- 通过 `tauri-plugin-shell` 管理 Python 后端子进程
- 系统托盘: 快速访问/后台运行
- 原生对话框: 文件选择 (导入/导出)

### 15.2 Mobile (移动端) — Capacitor

| 维度 | 选型说明 |
|------|----------|
| **框架** | Capacitor 7.x — Web 技术打包为移动 App |
| **优势** | 复用 90% 前端代码, 独立移动端 UI layer |
| **模式** | PWA 增强 + 原生 Capabilities |
| **目标** | Android (APK/AAB), iOS (IPA), 鸿蒙 (HAP, 通过) |

**交互方式**:
- 原生 Tab 导航 (底部)
- 下拉刷新
- 触屏滑动切换
- 长按上下文菜单
- 语音输入 (原生 API)

### 15.3 构建命令

```bash
# Desktop (Tauri)
npm run build:web           # 构建 Next.js
npx tauri build             # 打包 Desktop App

# Mobile (Capacitor)
npm run build:web
npx cap sync                # 同步到原生项目
npx cap open android        # 打开 Android Studio
npx cap open ios            # 打开 Xcode
```

---

## 16. 移动端界面

### 16.1 设计原则

1. **只读优先**: 不提供系统设置、不能修改项目元数据
2. **查看为主**: 只能查看数据、提问与系统交互、查看仪表板和图标
3. **组件复用**: 复用 Desktop 版本的组件 + 触屏适配
4. **轻量交互**: 触屏优化的输入、滑动、长按

### 16.2 功能矩阵

| 功能 | Desktop | Mobile |
|------|---------|--------|
| 登录/认证 | ✅ | ✅ |
| 项目切换 | ✅ | ✅ |
| 问答 (自然语言→SQL→结果) | ✅ | ✅ |
| SQL 查看 | ✅ | ✅ (只读展示) |
| 图表查看 | ✅ | ✅ (触屏交互) |
| Dashboard 查看 | ✅ | ✅ (只读) |
| Dashboard 管理 (创建/编辑/删除) | ✅ | ❌ |
| 建模画布 (编辑) | ✅ | ❌ |
| 模型/视图/关系 CRUD | ✅ | ❌ |
| 知识库管理 | ✅ | ❌ |
| API 历史查看 | ✅ | ❌ |
| 系统设置 | ✅ | ❌ |
| 数据源配置 | ✅ | ❌ |
| 导出/导入 | ✅ | ❌ |
| 用户管理 | ✅ | ❌ |

### 16.3 移动端路由

> 当前实现采用与桌面端共享路由, 由 `AppShell + MobileLayout` 提供移动端导航体验。

| 路径 | 对应桌面端 | 说明 |
|------|-----------|------|
| `/home` | `/home` | 问答首页 (推荐问题 + 输入) |
| `/home/[threadId]` | `/home/[threadId]` | 对话线程 (移动端使用 CompactPromptBar) |
| `/home/dashboard` | `/home/dashboard` | 仪表盘列表 |
| `/settings/profile` | `/settings/profile` | 用户个人资料, API Token 管理 |
| `/knowledge/*`、`/modeling`、`/projects*` | 同路径 | 通过 MobileLayout 的 More 面板进入 |

### 16.4 移动端组件

```
mobile/
├── MobileLayout.tsx         # 移动端壳 (Bottom Tab + More BottomSheet)
├── BottomSheet.tsx          # 底部抽屉
├── PullToRefresh.tsx        # 下拉刷新
├── ThreadCard.tsx           # 线程卡片 (精简版)
├── CompactPromptBar.tsx     # 紧凑输入框
├── CompactRecommendation.tsx # 紧凑推荐列表 (2-3 条, 可横向滑动)
├── MobileChartViewer.tsx    # 全屏图表查看器
├── MobileLogin.tsx          # 移动端登录
├── MobileProfile.tsx        # 个人资料页 (用户信息 + API Token 查看)
└── ReadOnlyModelViewer.tsx  # 只读模型结构查看 (字段/类型/描述)
```

### 16.5 Desktop WebSocket 策略

桌面端 WebSocket 连接管理与移动端不同:
- 连接: App 启动 → 连接 WebSocket, 保持长连接
- 心跳: 60s ping/pong (比移动端长, 减少资源占用)
- 断线重连: 指数退避 (1s, 2s, 4s, 8s, max 30s), 上限 10 次
- 休眠恢复: 系统唤醒后立即检测连接状态, 断开则按重连策略恢复
- 降级: 同 §7.2 SSE 降级机制

### 16.6 WebSocket 连接管理 (移动端)

```typescript
// 移动端 WebSocket 连接策略
// 1. App 启动 → 连接 WebSocket
// 2. 后台挂起 → 自动断开 (节省电量)
// 3. 前台恢复 → 自动重连
// 4. 弱网环境 → 降级到 SSE/HTTP polling

function useMobileWebSocket() {
  // AppState 监听 (react-native 原生)
  // Visibility API (PWA)
  // 心跳保活 (30s ping/pong)
}
```

---

## 17. 与旧 wren-ui 架构对比

### 17.1 代码量预估

| 模块 | 旧 wren-ui | PrismBI | 变化 |
|------|-----------|---------|------|
| 页面组件 | ~30 文件 | ~30 文件 (+ settings + mobile + admin) | 精简+新增 |
| UI 组件 | ~165 文件 | ~90 文件 | 合并+解耦 |
| Apollo Server | ~80 文件 | 0 文件 | **完全移除** |
| 后端 Python | 0 | ~40 文件 | 新增 |
| Tauri 桌面壳 | 0 | ~10 文件 | 新增 |
| 状态管理 | Apollo Cache | Zustand + TanStack Query | 简化 |
| 样式 | styled-components | Tailwind CSS | 切换 |
| **推荐引擎** | 0 | ~18 文件 (4 层管线 + Catalog + Hints + 评分器) | **新增** |
| **前端设计体系** | 0 | ~5 文件 (DesignTokens + 状态机 + 复合组件) | **新增** |
| **总计** | **~330 文件** | **~190 文件** | **减量 42%** |

### 17.2 运行时对比

| 指标 | 旧 wren-ui | PrismBI |
|------|-----------|---------|
| 进程数 | 7 | 2 + (Tauri 壳, 可选) |
| 内存占用 | ~4GB+ | ~500MB (引擎) + ~200MB (前端) |
| SQL 查询延迟 | >500ms (多次 RPC) | <100ms (本地调用) |
| 启动时间 | >2min (需编排) | <10s |
| 项目数 | 单项目 | 多项目原生支持 |
| 数据源 | 每项目 1 个 | 系统级注册 + 项目多对多绑定 |
| 跨源查询 | ❌ 不支持 | ✅ 自动分源 + DuckDB 合并 |
| 部署方式 | Docker Compose | Desktop App / pip + npm |
| 离线可用 | 否 | 部分 (LanceDB 本地) |
| 平台覆盖 | Web only | Web + Desktop + Mobile |

---

## 18. 实现阶段规划

### Phase 1: 基础设施 (当前实现状态)

- [x] 初始化 Next.js 16 + TypeScript + Tailwind CSS 项目
- [x] 搭建 FastAPI 后端骨架 (DuckDB + wren-engine SDK 接入仍在演进)
- [x] JWT 认证系统 + bcrypt 密码哈希
- [x] RBAC 权限引擎 (角色/权限/用户角色分配, 移除 is_sys_admin 独立字段)
- [x] DuckDB schema 创建 + 幂等初始化 (含 datasources/project_datasources + 权限表)
- [x] API 客户端 (TanStack Query) + Zustand store (authStore, projectStore, themeStore)
- [x] AppShell / Sidebar / Header 布局
- [x] 多项目管理 (创建/切换/列表, 切换同步 default_project_id)
- [x] 数据源两级管理: 系统注册 + 项目绑定 CRUD
- [x] 全局 UI 组件: Skeleton, EmptyState, ErrorBoundary, Toast, ConfirmDialog
- [x] 备份机制: 每日自动备份 `backend/data/prismbi.duckdb` + restore API + 前端管理页面
- [x] 键盘快捷键体系 + 命令面板 (Ctrl+K)

### Phase 2: 核心功能 (预估 10-14 天)

- [x] **NL2SQL 路由引擎**: 完整 3 层路由 (direct_llm / fewshot_cot / decompose_merge)、问题分析 (QUESTION_ANALYZER_CONTRACT)、Schema 剪枝、Decompose & Merge 复合问题处理、GROUP BY 完整性校验、聚合一致性校验 (warn-only)、ROUTER_CONFIG 集中常量管理、**LLM 重试循环 (带 error feedback)**、**问题分析 LRU 缓存**
- [x] **推荐引擎层 0 (Schema 驱动)**: MDL 自动候选生成已实现 — 从模型/视图/列自动生成 count/top_n/comparison/trend/percentage/aggregate 推荐问题; 会话级 Expansion/Follow-up 推荐已实现; 热门 Catalog 查询自动加权
- [x] **推荐引擎层 1 (会话级)**: Expansion/Follow-up 推荐已实现 — 基于当前对话上下文检测已提及模型, 生成 drilldown/compare/follow_up 推荐
- [x] **推荐 UI 组件**: RecommendedQuestions + RecommendationCard + OnboardingQuestions + StarRating — 全部组件已落地 (home/RecommendedQuestions.tsx, recommendation/RecommendationCard.tsx, home/OnboardingQuestions.tsx, home/StarRating.tsx)
- [x] **评分反馈闭环**: 评分写入、历史统计 API、**权重自动调整** 已实现 — rate 时触发 _adjust_weights_from_scores() 基于近 7 天平均评分自动调整 schema/session/project/catalog 各层权重并记录 layer_weight_history
- [x] **ScoreHistory + RecommenderSettings UI**: 评分历史查看 + 管理员权重配置
- [x] 问答首页 + 推荐问题 — home/page.tsx 集成 RecommendedQuestions + PromptBar + ThreadList
- [x] WebSocket 流式问答 + SSE 降级: WebSocket 已升级为逐 chunk 文本流 (delta text/sql) + 完整结果; SSE "/stream" 端点已实现 text/event-stream chunked 流式推送; 前端 WebSocket 端已接入 streamText 状态变量; SSE 前端 useSSE hook 已就绪
- [x] 线程管理 (创建/切换/历史) + threadStore — threadStore 155行 + threads.py 261行 + 前端 CRUD 完整
- [~] **SQL 展示 (CodeMirror 6) + 虚拟滚动结果表格 (useVirtualScroll)**: SQL 展示已落地 (CodeMirror 6 + SQL 语法高亮); 虚拟滚动结果表格已接入 (>100 行自动切换虚拟滚动)
- [x] Vega-Lite 图表渲染 — ChartContainer 148行 + react-vega/vega-lite/vega-embed 依赖齐全
- [x] **推荐引擎层 2 (项目级)**: Catalog/Hints CRUD + _get_hot_catalog_questions() (frequency 加权排序) + 评分权重自动调整 + create_catalog_entry 频率自动递增; 兴趣聚类仍待实现
- [x] **推荐引擎层 3 (全局级)**: 协同过滤 (CFQP 基于共现推荐) + 偏好学习 (用户 hint 权重 + 偏好类别追踪) + 意图趋势 (14天热门问题 + Catalog 频率趋势) 已实现
- [x] 建模画布 (ReactFlow) + undo/redo (modelingStore) + 模型/视图/关系 CRUD — Canvas.tsx 1110行 + modelingStore **功能 undo/redo** (逆操作执行) + PropertyPanel 完整 CRUD
- [x] **模型-数据源映射**: 为模型选择所属数据源 (PropertyPanel 数据源选择器, source_binding_id 完整 CRUD)
- [x] Dashboard 网格 + 面板管理: Dashboard CRUD、Pin、手动空 widget、preview、layout API 已落地; **viewer-aware 缓存重算** 已实现 — preview 端点缓存 TTL 5min 自动更新、force_refresh 参数、CLS 列级安全策略自动掩码/隐藏列、SQL 实时重执行填充 cache_data; grid 拖拽集成仍待实现

### Phase 3: 高级功能 + 管理后台 (预估 9-12 天)

- [x] 设置向导 (连接→表→关系)
- [x] 知识库 (Instructions + SQL Pairs)
- [x] 内存层 (向量搜索基于确定性嵌入, DuckDB 存储)
- [x] 计算字段编辑
- [x] SQL 调整 / 推理步骤调整
- [x] **跨源查询引擎**: 多数据源绑定、基于 sqlglot AST 的谓词/投影下推、聚合下推、代价优化 (物化行数上限动态调整)、DuckDB 临时表 materialize 合并均已实现; 数据源无需额外驱动时可按模型引用自动跨源 JOIN
- [x] **临时数据生命周期管理**: 响应/API历史手动清理, 临时schema dropping, 过期session/dashboard cache清理, 项目零数据清理
- [x] 系统设置 (品牌/主题/LLM/数据源/通用/导出/推荐引擎)
- [x] **管理后台 UI**: UserTable + UserFormDrawer + RoleTable + RoleFormDrawer + PermissionMatrix + SecurityPoliciesPage + SSOConfigPage
- [x] **管理 API**: 用户 CRUD, 角色 CRUD, 权限矩阵配置, SSO/OIDC 配置
- [x] **审计日志**: 后端采集 + AuditLogTable UI + 导出
- [x] **API Token 管理**: 个人 Token CRUD + Bearer 认证 + scope 收窄 + last_used_at
- [x] 审计日志系统 + 审计 UI
- [x] RLS/CLS 管理UI (Security Policies 页面, 含行级/列级策略CRUD)
- [x] RLS/CLS 与 MDL 层集成: RLS 支持 CTE/subquery/alias/join 表引用下推; CLS 支持结果过滤/掩码、HIDE 直接引用拦截、**表达式级 MASK SQL 重写** (CONCAT/UPPER 等函数中 MASK 列替换为字面量)、**列血缘追踪** (`compute_column_lineage`) + **表达式 MASK 检测** (`detect_masked_columns_in_expressions`)
- [x] **SSO/OIDC 集成**: 完整 OIDC 授权码流程 (discovery/authorize/code exchange/ID token 验证/nonce 校验/email 碰撞检测/claim→role 映射/自动用户创建); redirect URI 白名单验证; 管理后台 SSO 配置页面

### Phase 4: 多平台 (预估 5-7 天)

- [x] Tauri 桌面壳集成 — Cargo.toml (tauri 2 + shell/dialog/fs/updater/process 插件), main.rs (系统托盘 + 后端进程管理 + 命令), tauri.conf.json (CSP/bundle/icons/sidecar), capabilities/default.json (权限), tauri.ts (前端绑定 + 文件对话框), build-desktop.sh, generate-icons.sh
- [x] 移动端 UI (MobileShell + 只读视图) — MobileLayout 4-tab + More BottomSheet; CompactPromptBar; MobileChartViewer; ReadOnlyModelViewer; MobileLogin; MobileProfile; ThreadCard; CompactRecommendation; PullToRefresh; AppShell onRefresh + 响应式线程页
- [x] Capacitor 移动打包 — capacitor.config.ts 已创建; @capacitor/core/cli/android/ios/splash-screen/status-bar/keyboard/haptics 已安装; 移动打包配置就绪
- [x] 导出/导入 (YAML/JSON/CSV) — ExportService 后端完整; projectsApi.exportProject/importProject/migrateFromSqlite 前端 API; DataManagement 组件 (导出YAML/JSON + 导入文件 + 迁移 SQLite); i18n 翻译键
- [x] 旧版 wren-ui SQLite 迁移工具 — migration_service.py 支持 project/model/model_field/relation/datasource/knowledge/instruction/sql_pair/thread/thread_response/dashboard/dashboard_item 共 12 种表; 前端 API + DataManagement UI

### Phase 5: 打磨 (预估 3-5 天)

- [x] 性能优化 (懒加载、代码分割、bundle 分析) — ChartEditor/VegaEmbed/Canvas/ModelTree/PropertyPanel 全部 next/dynamic; ChartEditor 带 skeleton loading; DashboardGrid/code-split admin pages; bundle analyzer 集成
- [x] 错误边界 + 全局重试机制 + 离线检测 — error.tsx/global-error.tsx/not-found.tsx; TanStack Query exponential backoff retry (4xx 不重试, 2xx 最多2次); OfflineBanner; per-section ErrorBoundary 存在
- [x] 响应式布局验收 (xs/sm/md/lg 四断点全覆盖) — MobileLayout 4-tab + More menu; CompactPromptBar; MobileChartViewer; threadIdx 页面响应式; AppShell onRefresh; 17 loading.tsx 骨架屏
- [x] 国际化 (i18n) 词条翻译 + 文案走查 — 24 语言 (1078+ 键); ConnectionForm 44 硬编码字符串 i18n; RTL 支持 (ar/fa/ur); locale-aware formatDate/formatNumber/formatRelativeTime
- [x] 无障碍 (a11y): 标签、焦点管理、屏幕阅读器支持 — skip-to-content; useFocusTrap; useRouteFocus; prefers-reduced-motion CSS; StreamContent aria-live; Sidebar ARIA landmark; MobileLayout aria-current
- [x] 文档 + 开发指南 — README 部署/SSO/环境变量表; CONTRIBUTING 代码风格; DESIGN.md v4.0 变更记录

### 当前验证与已知限制 (2026-06-08)

- 已通过: `npm run typecheck`, `npm run lint`, `npm run build`。
- 后端测试使用 `python -m pytest`; 测试 fixture 使用真实 session-bound JWT 与 active `metadata.sessions`。
- `npm audit --json` 剩余 2 个 moderate: Next 16.2.6 捆绑的 `postcss <8.5.10`; npm 给出的 `fixAvailable` 会错误建议降级到 `next@9.3.3`, 不可执行 `npm audit fix --force`。
- `npm outdated --json` 当前仅显示 `eslint` 最新为 10.4.0; 因 Next 16 相关 ESLint 插件 peer 兼容仍以 ESLint 9 为稳定线, 当前固定 `eslint ^9.39.4`。
- 前端 API client 支持 FormData 原样发送, 401 会清理旧 `auth_token/auth_user/auth-store` 并清空 React Query cache; `authStore` 与 `useAuth` 统一只通过 Zustand persist 的 `auth-store` 保存 session/token。
- 后端敏感信息返回已覆盖 settings、SSO config 与 project `connection_info`; API history 审计 payload 递归脱敏并扩大 `api_key/apikey/access_key/private_key/connection_info/properties` 等关键词。datasource properties、project connection_info、metadata.connections config、LLM API key、SSO config 已接入 Fernet 存储加密与旧明文兼容读取/迁移。仍需继续补全 rate limit 和 KMS/密钥轮换。
- 生产安全修复已完成: `get_effective_permissions` 项目/系统角色隔离; Admin 角色分配按 scope 校验; 系统数据源删除检查项目引用; 项目 datasource unbind 检查模型引用; Dashboard pin/preview 校验 `response_id` 属于同一 project; token scope 在 RBAC 后二次收窄; sqlglot 只读 guard; API token bcrypt hash + prefix lookup 认证; bootstrap admin 随机密码写入 0600 文件; 空项目语义彻底清理; RLS CTE/subquery/alias/join 下推; CLS HIDE 列引用拒绝 + 表达式级 MASK 重写。
- 跨源查询已支持谓词/投影下推、聚合下推、代价优化 (物化行数上限动态调整); 非 DuckDB live discovery/执行仍依赖可选 Python 驱动、数据库权限和连接配置。
- Dashboard viewer-aware 缓存重算已实现: preview 端点检测 RLS 策略时重新执行 SQL, CLS 掩码/隐藏按 viewer 权限应用。
- WebSocket 已升级为逐 chunk 流 (delta text/sql) + 步骤进度推送 (understand/retrieve/organize/execute/answer 五阶段); SSE `/ask/stream` 端点已实现 text/event-stream chunked 流式推送 + 步骤进度。
- SSO/OIDC 完整实现: OIDC discovery/authorize/callback/ID token 校验/nonce 防重放/redirect URI 白名单验证/claim→role 映射/email 碰撞检测/自动用户创建; 30 个测试用例通过。
- `/api/ask/stream` 已升级为真 SSE 分块输出 (delta step → delta text 逐 chunk → result)。
- Desktop (Tauri 2.x 系统托盘 + 后端进程管理) + Mobile (Capacitor 4-tab 布局) 双平台就绪。
- 仍未解决的重点风险: Fernet key 轮换/KMS 未完成; 跨源合并对大表的谓词过滤下推覆盖率仍需更多生产测试; WebSocket 当前是后端计算完成后逐 chunk 发送, 尚非 LLM 生成阶段逐 token 真流式; dashboard grid 拖拽布局持久化前端集成仍待完善。

---

## 19. 评审确认记录

### 第 1 轮评审确认 (2026-05-16)

| 评审点 | 结论 | 说明 |
|--------|------|------|
| **1. JWT 认证** | ✅ **保留** | JWT + bcrypt 认证, DuckDB 存储用户 |
| **2. 项目存储** | ✅ **重新设计** | DuckDB 持久化, 支持多项目, 可导出 YAML |
| **3. Dashboard 持久化** | ✅ **纳入 DuckDB** | Dashboard/Items 存储在 DuckDB |
| **4. 流式传输** | ✅ **WebSocket 优先 + SSE 备选** | WebSocket 主通道, SSE 降级方案 |
| **5. 代码编辑器** | ✅ **CodeMirror 6** | 替代 Monaco, 体积 ~1MB vs ~5MB |
| **6. UI 组件库** | ✅ **待定评审** | 后续评估 shadcn/ui / Park UI 等更优方案 |
| **7. LLM 集成** | ✅ **Skills 框架完全替代** | 新管道完全取代旧 wren-ai-service |
| **8. 引擎模式** | ✅ **统一 wren-core-py** | 仅使用 wren-core-py, 不再多模式切换 |
| **9. 系统设置 (新增)** | ✅ **纳入设计** | 品牌/主题/LLM/通用/导出 5 类设置 |
| **10. 多平台打包 (新增)** | ✅ **纳入设计** | Desktop (Tauri) + Mobile (Capacitor) |
| **11. 移动端 (新增)** | ✅ **纳入设计** | 只读, WebSocket 通信, 组件复用 |

### 第 2-7 轮迭代修正 (2026-05-16)

| 轮次 | 修正要点 |
|------|---------|
| **第 2 轮** | 目录树重构(v1.2)、API 架构优化、Zustand→TanStack Query 重构、数据流图细化、多源并行查询、图表渲染、事件溯源 |
| **第 3 轮** | 引擎交互对齐、目录结构扁平化、API 命名改进、角色权限细化、事件溯源完善、系统设置模块 |
| **第 4 轮** | DuckDB 元数据§13拆分与完善、主动推荐引擎§14深化Schema/Budget/Bandit三层、多平台和移动端纳入设计 |
| **第 5 轮** | 实现阶段规划、与旧架构对照表、评审确认记录、§12跨项目引用修复、§6组件说明书更新、目录树§4简化 |
| **第 6 轮** | API历史/消息清理端点、404/429/502错误码补充、Project CRUD统一、用户名移除email字段、SSE降级策略、模型图独立、API JSON格式栈一致性 |
| **第 7 轮** | 回滚深度扁平化目录至分层结构、模型/视图/关系命名`_name`→`name`、用户表移email添status、`auth/register`转为可选、关闭事件溯源同步SQLite、移除`wren-ui/`目录引用 |

### 第 8 轮评审修正 (2026-05-16)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. `:pid`→`:id` 命名统一** | 所有 project 子资源路径统一为 `:id`, 消除 `:pid` / `:id` 混用 | §7.2 项目管理/成员/数据源 |
| **2. `{ data, error }` 格式落实** | 统一响应格式原则补充成功/失败范例; 所有 POST 端点补充 `→ { ... }` 响应体 | §7.1, §7.2 |
| **3. `members/:roleId`→`members/:memberId`** | 修复路径参数命名错误, PUT/DELETE 统一为 `:memberId` | §7.2 成员管理 |
| **4. 技术栈对齐** | Socket.IO→ws (设计仅用原生 WebSocket); driver.js 标注"待设计" | §3.1 |
| **5. HTTP 错误状态码** | 补充 400/401/403/404/429/500 定义 | §7.1 |
| **6. SSE 端点入表** | `POST /api/ask/stream` 加入 API 接口清单 | §7.2 问答 |
| **7. POST 响应一致性** | 为 15+ 个缺失响应格式的 POST 端点补充 `→ { id }` / `→ { ... }` 响应 | §7.2 全章节 |
| **8. `GET /api/diagram` 补全** | 响应体增加 `calculatedFields` 字段 | §7.2 建模 |

### 第 9 轮评审修正 (2026-05-16)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. 路由表补全** | 补充 6 条缺失路由 (`/admin/users/:id`, `/admin/roles/:id`, `/admin/sso`, `/projects/:id/members`, `/settings/profile`, `/settings/profile/sessions`) | §5.1 |
| **2. 移动端路由补全** | 补充 2 条缺失路由 (`/mobile/modeling`, `/mobile/modeling/:modelName`) | §5.2 |
| **3. 用户表 DDL 一致** | §12.2 补充 `email`, `last_login_at`, `updated_at` 三列, 与 §11.5 一致 | §12.2 |
| **4. API 设计规范增强** | 添加认证标记、查询参数 camelCase 约定、WebSocket token 安全策略、分页参数约定 | §7.1 |
| **5. 查询参数统一 camelCase** | 修正遗留 snake_case: `project_id`→`projectId`, `source_layer`→`sourceLayer`, `max_results`→`maxResults`; `&size`→`&pageSize` | §7.2 |
| **6. REST 路径修正** | `DELETE /api/threads/responses`→`DELETE /api/responses` (符合 REST 层级) | §7.2 |
| **7. `(public)` 标记** | login/register 明确标注为公开端点 | §7.2 |
| **8. UI 状态表补全** | 增加 11 个缺失页面的 Loading/Empty/Error/Ready 四态定义 (对话线程/设置向导三步/个人资料/会话管理/SSO/项目设置/项目成员/评分历史) | §9.6.3 |
| **9. 命名优化** | `stats`→`statistics` 全拼写 | §7.2 |

### 第 10 轮评审修正 (2026-05-16)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. DDL 外键补全** | `recommended_questions_cache.user_id` 补充 `REFERENCES metadata.users(id)` | §12.2 |
| **2. DDL 列名对齐 API** | `projects.data_source_type`→`type` (与 API POST 请求体字段名一致) | §12.2 |
| **3. API 请求体补全** | 为 8 个 POST 端点补充请求体字段定义 (模型/视图/关系/计算字段/Dashboard/面板/指令/SQL-Pair); 为 5 个 PUT 端点补全请求体; 为 2 个 PUT 端点补全响应格式 | §7.2 |
| **4. 交互模式扩展** | 新增建模拖拽、Dashboard 网格布局、设置向导三步、图表切换 4 种关键交互模式 | §9.6.4 |
| **5. 组件 Props 约定** | 新增 §6.2 组件 Props 接口约定, 包含原子组件/业务组件/Store 类型的 TypeScript 范例 | §6.2 |
| **6. 页面 Props 补全** | 为 ProjectSwitchModal、线程列表等页面的 Loading/Empty/Error 状态增加说明 | §9.6.3 |

### 第 11 轮评审修正 (2026-05-16)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. DDL 模型表补全** | 新增 `models`、`views`、`relations`、`calculated_fields` 四张 DDL 表 (元数据缓存, 主存储为 MDL YAML) | §12.2 |
| **2. Dashboard 面板 DDL** | 新增 `chart_config JSON` 列 (存储图表配置: 字段映射/聚合/图表类型) | §12.2 |
| **3. 环境变量/配置** | 新增 §13.3 环境变量表 (DB 路径/JWT 密钥/端口/CORS/速率限制等 11 项) | §13.3 |
| **4. §10 交叉引用修复** | 修正为指向 §12.2(业务表) + §11.5(权限表), 而非错误的 §12 | §10.4 |
| **5. 错误恢复路径补全** | 22 个页面全部补充错误恢复机制 (重试按钮/回退/单卡片重试) | §9.6.3 |
| **6. React 性能优化** | 新增 §9.6.10 React 渲染优化表 (Memo/useMemo/useCallback/Lazy+Suspense/IntersectionObserver/动态 import/CSS contain) | §9.6.10 |
| **7. AbortController 补全** | 问答流取消生成加入 Phase 2 任务清单 | §18 |

### 第 12 轮评审修正 (2026-05-16)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. HTTP 方法规范** | chart + memories 读操作→GET; token/session 吊销→POST .../revoke; 用户禁用→POST .../deactivate | §7.2 |
| **2. Sessions DDL 表** | 新增 `metadata.sessions` 表 (JWT sid 主键, 含 user_id/expires_at/is_revoked) | §12.2 |
| **3. 知识库搜索参数** | instructions + sql-pairs 列表端点补充 `?search &sort &page &pageSize` | §7.2 |
| **4. Cache/System DDL** | 补充 4 表设计说明 (cache.dashboard_items_cache, cache.query_results_cache, system.temp_query_registry, system.schema_migrations) | §12.2 |
| **5. 无障碍 a11y 设计** | 新增 §9.6.13, 定义语义 HTML/ARIA/焦点管理/键盘导航/WCAG 2.1 AA/屏幕阅读器/触屏目标/减少运动 8 项要求 | §9.6.13 |
| **6. 前端界面优化** | a11y 设计原则 + 8 项具体实现要求 | §9.6.13 |

### 第 13 轮评审修正 (2026-05-29)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. NL2SQL 路由引擎设计** | 新增 §9.7 完整记录 3 层路由 (direct_llm / fewshot_cot / decompose_merge)、问题分析合约、Schema 剪枝策略、Decompose & Merge 管线、后处理验证器 (孤儿 CTE/列验证/GROUP BY/聚合一致性)、ROUTER_CONFIG 常量 | §9.7 |
| **2. 问答线程更新** | §9.2 标记 Ask Service 已接入路由引擎, 更新保留功能列表 | §9.2 |
| **3. 实现阶段规划更新** | Phase 2 补充 NL2SQL 路由引擎已完成项 | §18 |
| **4. 文档版本同步** | v2.1 → v2.2, 同步日期 2026-05-24 → 2026-05-29; 实现基线概述补充路由引擎描述 | 页首 |

### 第 16 轮评审修正 (2026-05-30)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. askRouteDesign 合并** | 将 askRouteDesign.md 设计文档的理念与实现对齐: 已实现 3 层路由 (direct_llm / fewshot_cot / decompose_merge)、问题分析合约、Schema 剪枝、Decompose & Merge、后处理验证器、重试循环、分析缓存、无匹配引导 (GUIDANCE_PROMPT)、确定性 SQL 模板已移除。设计文档中规划的项目信息意图片段 (project_info intent) 当前合并到 _project_general_chat 路径 | §9.7 |
| **2. 路由引擎 Bug 修复** | max_sub_questions 截断: decompose-merge 子问题超过 5 个时截断; metadata_summary_max_models 截断: 超过 10 个模型时截断; use_examples 策略标志: 根据策略条件性注入知识 SQL 示例; _analysis_cache 线程安全: 增加 threading.Lock; guidance_llm_available/schema_pruning_enabled: 新增 ROUTER_CONFIG 键并接入代码; _generic_result_answer 丢失 sub_questions: 增加参数传递复合问题上下文; 通用聊天快捷路径: _looks_like_general_chat 路径现在也传入 metadata_summary; fewshot_cot 策略: 知识示例可用时增强策略提示引用示例 | ask_service.py 多处 |
| **3. GROUP BY/聚合验证反馈** | 验证 warn 现在写入响应 reasoning 字段 (前缀 [WARN]), 不触发重试 | §9.7.5 |
| **4. 文档版本同步** | v2.4 → v2.5; ROUTER_CONFIG 文档补充 guidance_llm_available 和 schema_pruning_enabled 说明 | §9.7.6 |

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. 备份与恢复 API** | 新增 `services/backup_service.py` (create/list/get/download/restore/delete) + `routers/admin.py` 备份路由 + `backup` 权限 (create/restore/delete/read/download) | §7.2, §11.5 |
| **2. 备份管理前端** | 新增 `admin/backup/page.tsx` (备份列表/创建/下载/恢复/删除) + Sidebar 导航 + Header 面包屑 + 中英文 i18n | §5.1, §6.2 |
| **3. 键盘快捷键与命令面板** | 新增 `hooks/useCommandPalette.ts` + `components/ui/CommandPalette.tsx` (Ctrl+K 搜索导航/命令) + AppShell 集成 | §9.6.4 |
| **4. 模型-数据源映射** | PropertyPanel 新增 `sourceBindingId` 字段 + 数据源选择器下拉菜单 + 保存时传递 `source_binding_id` 到 API + modeling page `selectedNode` 包含 `sourceBindingId` | §9.4 |
| **5. 虚拟滚动结果表格** | ResultTable 组件接入 `useVirtualScroll` hook, 行数 >100 时自动切换虚拟滚动渲染 | §9.6.9 |
| **6. NL2SQL 重试循环** | `_generate_sql()` 按 tier 的 max_retries 重试: 失败时收集 error feedback 注入下次 LLM 调用; 修复后重新验证, 通过即返回; 重试耗尽返回最佳努力结果 | §9.7.1 pipeline step 6-7, §9.7.5 |
| **7. 问题分析 LRU 缓存** | 新增 `_analysis_cache` (max 128), 以 `project_id::question::previous_questions` 为 key 跳过重复 LLM 调用 | §9.7.1 pipeline step 2 |
| **8. 文档版本同步** | v2.2 → v2.4; 实现基线概述补充备份/恢复、命令面板、模型-数据源映射、虚拟滚动、重试循环和 LRU 缓存 | 页首 |

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. LLM 重试循环** | `_generate_sql()` 按 tier 的 max_retries 重试: 失败时收集 error feedback 注入下次 LLM 调用; 修复后重新验证, 通过即返回; 重试耗尽返回最佳努力结果 | §9.7.1 pipeline step 6-7, §9.7.5 |
| **2. 问题分析 LRU 缓存** | 新增 `_analysis_cache` (max 128), 以 `project_id::question::previous_questions` 为 key 跳过重复 LLM 调用 | §9.7.1 pipeline step 2 |
| **3. 修复再验证** | 孤儿 CTE 和列修复后, 对修复输出再次运行原验证器; 通过则返回, 仍失败则进入 retry 循环 | §9.7.5 |
| **4. 文档版本同步** | v2.2 → v2.3, 同步日期 2026-05-29 → 2026-05-30; 实现基线概述补充重试循环和 LRU 缓存 | 页首 |
| **5. ROUTER_CONFIG 说明更新** | 标注 max_retries 从"预留"改为"已接入重试循环" | §9.7.6 |

### Round 18

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. `_summarize_query_result` NameError 修复** | `sub_questions` 在 early return 前未定义, 已提取至函数开头 | `ask_service.py` |
| **2. `_build_metadata_summary` 截断修复** | 保留 `all_models` 引用用于 `models_count`, 避免截断后计数错误 | `ask_service.py` |
| **3. GROUP BY/聚合警告格式化** | `warning_parts` 现在将 list 转为可读字符串 (逗号分隔), 而非 Python repr | `ask_service.py` |
| **4. 备份路径穿越防护** | `_validate_backup_name()` 校验字符白名单 + 拒绝 `..` + `os.path.realpath` 校验 | `backup_service.py` |
| **5. Zip 穿越防护** | `_validate_zip_members()` 在 `extractall()` 前检查所有成员路径是否在目标目录内 | `backup_service.py` |
| **6. 原子替换 & 预恢复日志** | 数据库/WAL/密钥文件使用 `shutil.copy2` + `os.replace()` 原子替换; 恢复前写 `.restore_in_progress` 日志 | `backup_service.py` |
| **7. 连接管理修复** | `restore_backup` 使用 `db.close_connection()` (正确设置 `_con=None`) 替代手动 `con.close()`; 失败时调用 `init_db()` 兜底 | `backup_service.py` |
| **8. 备份/恢复互斥锁** | `_backup_lock` 和 `_restore_lock` 分别保护 `create_backup` 和 `restore_backup` 防止并发 | `backup_service.py` |
| **9. 备份名称 UUID 后缀** | 备份名添加 `_{uuid4.hex[:8]}` 防止同一秒内名称碰撞 | `backup_service.py` |
| **10. Zip 内容验证** | 恢复前检查 DuckDB 文件大小 (>=1024) 和 DuckDB 完整性 (`SELECT 1`); 项目文件跳过 `.` 开头和含 `..` 的条目 | `backup_service.py` |
| **11. `os.walk` 符号链接** | `followlinks=False` 防止软链接数据泄露 | `backup_service.py` |
| **12. 备份文件读取移入锁** | DB/WAL/密钥文件读取移入 `connection_lock()` 保护范围内 | `backup_service.py` |
| **13. `backup:download` 权限分离** | 新增 `backup:download` 权限, 下载归档需要此权限而非 `backup:read`; 前端下载按钮受此权限控制 | `db/__init__.py`, `admin.py`, `backup/page.tsx` |
| **14. `RestoreRequest.name` 改为必填** | 从 `Optional[str]` 改为 `str` | `admin.py` |
| **15. 恢复后清理日志** | 成功恢复后删除 `.restore_in_progress` 日志文件 | `backup_service.py` |
| **16. 文档版本** | v2.5 → v2.6 | 页首 |

### Round 19

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. 推荐引擎层 0 — MDL 自动候选生成** | `recommendation_service.py` 完整重写: `_generate_mdl_candidates()` 从项目语义模型自动生成 count/top_n/comparison/trend/percentage/aggregate 类推荐问题; `get_recommendations()` 合并 MDL 候选 + 会话级推荐 + Catalog 热门查询, 去重并按 confidence 排序; `get_onboarding()` 为新项目生成入门推荐 | `recommendation_service.py` |
| **2. 推荐引擎层 1 — 会话级 Expansion/Follow-up** | `_generate_session_followups()` 基于当前对话上下文检测已提及模型, 生成 drilldown/compare/follow_up 类型推荐 | `recommendation_service.py` |
| **3. 推荐引擎层 2 — 热门 Catalog 查询** | `_get_hot_catalog_questions()` 从 `question_sql_catalog` 查询已验证高频问题; `create_catalog_entry()` 去重时自动累积 frequency; 权重自动调整按近 7 天评分上下浮动 | `recommendation_service.py`, `recommendations.py` |
| **4. 评分反馈闭环 — 权重自动调整** | `_adjust_weights_from_scores()` 在 rate API 后自动触发: 读取近 7 天评分, 按 avg_score 调整各层权重 (高分+0.05, 中+0.02, 低分-0.10), 写入 `layer_weight_history` + `metadata.settings` | `recommendation_service.py`, `recommendations.py` |
| **5. SSE 流式端点** | `POST /ask/stream` 改为 `StreamingResponse(text/event-stream)`: 先发 `delta(state=running)`, 异步计算后逐 chunk 发 `delta(text)`, 最后发完整 `result` | `routers/ask.py` |
| **6. WebSocket 逐 chunk 流升级** | `ws.py` 升级: 执行 `run_in_executor` 后逐 12 字符 chunk 发 `delta(text)`, SQL 完整发 `delta(sql)`, 最后发完整 `result`; 前端 threadId 页面新增 `wsStreamText` 状态, `pendingResponse.answerDetail.content` 接入流式文本 | `routers/ws.py`, `[threadId]/page.tsx` |
| **7. 前端推荐 API 对接** | `recommendationsApi.onboarding()` 改为调用 `/recommendations/{projectId}/onboarding`; `OnboardingQuestions` 组件支持 `model_names?: string[]` 数组展示; `recommendationStore` 类型更新 | `api.ts`, `OnboardingQuestions.tsx`, `recommendationStore.ts` |
| **8. 备份安全修复** | 路由层 `ValueError` 统一转 400; `_do_ask` 函数在 ws.py 中恢复; `_validate_backup_name` 重复调用确认无碍 | `admin.py`, `ws.py` |
| **9. 文档版本** | v2.6 → v2.7; Phase 2 多项标记为已完成; 实现基线描述更新 | 页首 |

---

> **文档状态**: v4.0 (v3.4+v3.5+v4.0 合并同步 — SSO 安全加固 + Mobile 完善 + 24 语言 i18n + 性能优化 + 跨源增强 + RLS/CLS 表达式级 Mask + 步骤进度 + 文档同步至代码实现)
> **已确认**: SSO nonce/state/CSRF/redirect 白名/email 碰撞检测已实现; Mobile 底部导航增加 More 菜单; 24 种语言翻译完成; ChartEditor 懒加载

---

> **文档状态**: v4.0 (已按当前代码同步, 后续继续作为活文档维护)

### Round 20 (Phase 3 实现)

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. dashboards.py 连接泄漏** | `preview_item()` 在 `connection_lock()` 外使用 `con` 引用调用 `_response_chart_data()`; 修复: 将 DB 读取移入锁内, 缓存写入使用独立锁块 | `routers/dashboards.py` |
| **2. modelingStore undo 错误** | `updateModel` 的 undo `before` 字段错误地使用 `data`(更新值) 而非原始模型字段; 修复: 使用 `Object.keys(data)` 提取变更字段, 从原模型构建 `beforeFields` 和 `afterFields` | `frontend/src/stores/modelingStore.ts` |
| **3. recommendations.py 元数据双重序列化** | 生成候选的 `metadata` 字段对 dict 调用 `json.dumps()` 导致客户端收到字符串而非对象; 修复: 统一反序列化为 dict 再存储; `_list_recommendation_rows()` 移入锁内 | `routers/recommendations.py` |
| **4. recommendation_service.py 循环导入** | `_models_for_project`/`_relations_for_project` 顶层导入造成循环依赖风险; 修复: 移入函数内部延迟导入; `ESCAPE '\\\\'` → `ESCAPE '\\'` 修正 | `services/recommendation_service.py` |
| **5. ask.py SSE 内部异常泄露** | `StreamingResponse` 的 `except Exception as exc` 将 `str(exc)` 发送给客户端; 修复: 替换为通用消息 | `routers/ask.py` |
| **6. asyncio 弃用警告** | `asyncio.get_event_loop()` 在 async context 中已弃用; 修复: 两处替换为 `asyncio.get_running_loop()` | `routers/ask.py`, `routers/ws.py` |
| **7. RLS/CLS 管理页面** | 新增 `admin/security-policies` 页面, 含行级/列级策略 Tab 切换、项目/角色筛选、CRUD 表单、模型选择联动; 侧边栏添加安全策略和 SSO 入口 | `frontend/src/app/admin/security-policies/page.tsx`, `Sidebar.tsx` |
| **8. SSO 配置页面** | 新增 `admin/sso` 页面, 含 provider/issuer/clientId/clientSecret 配置、claim→role 映射 JSON 编辑器、enable 开关、回调 URL 提示 | `frontend/src/app/admin/sso/page.tsx` |
| **9. Cleanup Service 实现** | 实现 `cleanup_temp_schemas()` (DuckDB schema 清理)、`cleanup_cache()` (Dashboard >24h 缓存清理)、`cleanup_expired_sessions()`、`cleanup_stale_temp_data()` | `services/cleanup_service.py` |
| **10. Memory Service 实现** | 基于 DuckDB `metadata.memories` 表实现搜索/存储/列出/遗忘; 使用确定性哈希嵌入做余弦相似度搜索; 新增 schema 迁移 | `services/memory_service.py`, `db/__init__.py`, `routers/exports.py` |
| **11. i18n 补充** | en.json/zh.json 新增安全策略、SSO 配置、通用操作等翻译条目 | `frontend/src/lib/i18n/locales/*.json` |
| **12. 文档版本** | v2.8 → v2.9; Phase 3 大部分条目标记为 [x] 或 [~] | §18 Phase 3 |

| 评审点 | 修正内容 | 位置 |
|--------|---------|------|
| **1. 推荐引擎层0 MDL自动候选** | `recommendation_service.py` 完整重写: `_generate_mdl_candidates()` 从语义模型自动生成 count/top_n/comparison/trend/percentage/aggregate 推荐; `get_recommendations()` 合并 schema + session + catalog + collaborative + preference + trending 六层候选, 去重+排序; `get_onboarding()` 新端点 | `recommendation_service.py`, `recommendations.py` |
| **2. 推荐引擎层1 会话级** | `_generate_session_followups()` 基于对话上下文检测已提及模型, 生成 drilldown/compare/follow_up | `recommendation_service.py` |
| **3. 推荐引擎层2 热门Catalog** | `_get_hot_catalog_questions()` frequency 加权排序; `create_catalog_entry()` 去重时自动递增 frequency | `recommendation_service.py` |
| **4. 推荐引擎层3 全局级** | `_collaborative_filtering()` 共现推荐 + 线程相似问题; `_preference_learning()` 用户 hint + 偏好类别追踪; `_intent_trends()` 14天热门问题趋势 | `recommendation_service.py` |
| **5. 评分权重自动调整** | `_adjust_weights_from_scores()` 在 rate API 后自动触发, 基于近7天平均评分调整各层权重 | `recommendation_service.py`, `recommendations.py` |
| **6. SSE真实流式端点** | `POST /ask/stream` 改为 `StreamingResponse(text/event-stream)`: delta(state=running) → delta(text 逐 chunk) → delta(result) | `routers/ask.py` |
| **7. WebSocket逐chunk流** | `ws.py` 升级: 答案按12字符 chunk 发送 delta(text), SQL 完整发送 delta(sql), 前端 `wsStreamText` 状态实时更新 | `routers/ws.py`, `[threadId]/page.tsx` |
| **8. Dashboard viewer-aware缓存重算** | preview 端点: (a) 缓存 TTL 5min 自动提供缓存数据; (b) 缓存过期时从 response_data 重填 chart_config; (c) SQL 空结果时调用 `execute_project_sql` 实时执行; (d) 更新 `cache_data` + `cache_created_at`; (e) 应用 CLS 列级安全策略 (HIDE/MASK) 给 viewer; (f) `force_refresh` 参数 | `routers/dashboards.py` |
| **9. 前端onboarding API对接** | `recommendationsApi.onboarding()` 调用 `/{projectId}/onboarding`; `OnboardingQuestions` 支持 `model_names?: string[]` | `api.ts`, `OnboardingQuestions.tsx`, `recommendationStore.ts` |
| **10. Phase 2 标记完成** | 推荐 UI 组件、问答首页、线程管理、Vega-Lite 图表、建模画布全部标记 [x] | §18 Phase 2 |
| **11. modelingStore功能undo/redo** | `undo()`/`redo()` 现在执行逆操作 (addModel↔removeModel, updateModel before/after 等) | `modelingStore.ts` |
| **12. 文档版本** | v2.7 → v2.8; Phase 2 几乎全部标记为完成 | 页首 |  
> **待确认**: SSO/OIDC 首个 Provider 与 claim→role 映射、wren-engine SQL 生成接入路径  
> **下一步**: 补齐真 token/SSE 流式 Ask、SSO/OIDC、跨源查询谓词/投影下推、Dashboard viewer-aware 重算与 CLS 列血缘增强

### 变更记录 v2.9 (2026-05-31)

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **init SQL 文件读取防护加固** | 新增 `read_csv`, `read_csv_auto`, `read_parquet`, `read_delta`, `delta_scan`, `iceberg_scan` 到 `INIT_SQL_BLOCKED_PATTERNS`，防止通过 init_sql 读取服务器任意文件 | `datasources.py` |
| **2** | **config_overrides 解密Bug修复** | `_safe_json_loads(decrypt_json(r[4], {}) ...)` 会将已解密的dict当JSON解析导致返回`{}`，修复为 `decrypt_json(r[4], {}) if encrypted else _safe_json_loads(r[4], {})` | `datasources.py` |
| **3** | **SetupConnectionPage React Hook规则** | `hasPermission`早返回在所有`useState`之前导致Rules of Hook崩溃；移动到所有Hook之后渲染 | `setup/connection/page.tsx` |
| **4** | **WebSocket pong超时修复** | `resetPongTimeout()`仅在连接打开和收到pong时调用，不再每次ping重置；修复超时永不触发问题 | `ws.ts` |
| **5** | **LLM配置读取加锁** | `_get_timeout_settings()`和`get_llm_config()`使用`connection_lock()`包装DB读取，避免并发读写冲突 | `llm_service.py` |
| **6** | **httpx.WriteTimeout重试** | LLM `_retryable_post`的异常列表新增`httpx.WriteTimeout` | `llm_service.py` |
| **7** | **LLM空choices IndexError** | `raw.get("choices", [{}])[0]` 改为安全访问 `(raw.get("choices") or [{}])[0] if choices else ""` | `llm_service.py` |
| **8** | **Anthropic content=null TypeError** | `raw.get("content", [])` 改为 `(raw.get("content") or [])` 防止null遍历 | `llm_service.py` |
| **9** | **RLS update修复** | 前端API类型 `Omit<RowSecurityPolicy, 'id'|'created_at'|'filter_expression'>` 移除 `filter_expression` 排除，允许更新自动生成的filter表达式 | `api.ts` |
| **10** | **线程响应不再暴露原始异常** | `create_response`的500错误从`detail=str(exc)`改为通用消息`detail="Internal error processing question"` | `threads.py` |
| **11** | **分析缓存竞争修复** | `_analyze_question`的TTL检查移入`_analysis_cache_lock`内部，消除缓存结果TOCTOU | `ask_service.py` |
| **12** | **LoginRequest密码长度限制** | `max_length=1024`降为`128`，防bcrypt DoS | `schemas.py` |
| **13** | **ThreadResponse接口清理** | 移除重复`threadId`字段，统一使用`thread_id` | `api.ts`, `threadStore.ts` |
| **14** | **SQL生成验证容错** | `_validate_sql_columns`返回`None`时不再当作坏列，而是跳过验证；asyncio create_task的ask不再抛出ValueError而是返回用户友好错误 | `ask_service.py` |
| **15** | **CTE孤儿检测优化** | `_validate_no_orphaned_cte`增加Subquery引用检测和文本回退搜索，减少误报 | `ask_service.py` |
| **16** | **前端API类型修复** | `ApiModelDef`增加`description/fields/relation_defs/updated_at`，`ApiViewDef`增加`description/sql/fields/updated_at`，关系接口移除`type`只用`relation_type`，SSO接口增加`enabled`字段 | `api.ts` |
| **17** | **项目设置案例数据源** | 已添加的案例数据源在添加弹窗中显示为禁用+已添加标签，防止重复添加 | `projects/[id]/settings/page.tsx` |
| **18** | **聊天输入框改为多行** | `PromptBar`从单行input改为textarea，发送按钮在右下角，与聊天卡片宽度对齐，移除预览行数选择器 | `PromptBar.tsx` |
| **19** | **New Conversation翻译** | 后端hardcode "New Conversation"在前端通过`displayThreadSummary()`统一翻译 | `utils.ts`, `ThreadList.tsx`, Header.tsx |
| **20** | **项目设置placeholder国际化** | Display Name和Description输入框placeholder走i18n，增加`project.displayNamePlaceholder`和`project.descriptionPlaceholder`中英文键 | `page.tsx`, `en.json`, `zh.json` |

### 变更记录 v3.0 (2026-05-31)

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **WebSocket Ask 实时步骤进度** | `ws.py` 新增 `StepProgress` 类，将同步 `progress_cb` 桥接到 `asyncio.Queue`; `ask_question()` 新增 `progress_cb` 参数，在 understand/retrieve/organize/execute/answer 五个阶段回调; WebSocket 流式发送 `delta(content_type=step)` 消息，payload 为 `{"key":"understand","detail":"..."}` JSON | `ws.py`, `ask_service.py` |
| **2** | **前端步骤进度解析** | `[threadId]/page.tsx` 新增 `wsStepProgress` 状态; WebSocket 消息处理增加 `content_type === 'step'` 分支，解析 JSON payload 更新进度; `result`/`error` 时清零 | `page.tsx` |
| **3** | **ThinkingSteps 组件 liveSteps** | `ThinkingSteps` 新增 `liveSteps` prop; 有 `liveSteps` 时用其实时更新步骤状态（已完成/运行中），优先级高于自动 tick | `ThinkingSteps.tsx` |
| **4** | **ResponseCard 传递 liveSteps** | `ResponseCard` 新增 `liveSteps` prop 并传递给 `ThinkingSteps`; 待处理问题使用 WebSocket 实时步骤 | `ResponseCard.tsx`, `page.tsx` |
| **5** | **Setup 进度条组件** | 新增 `TaskProgress` UI 组件，显示任务步骤（pending/running/finished/failed）列表; `SetupConnectionPage` 三处创建进度从纯文本改为 `TaskProgress` 组件 | `TaskProgress.tsx`, `setup/connection/page.tsx` |
| **6** | **Setup 阶段 i18n** | `en.json`/`zh.json` 新增 `setup.stage_project/datasource/discovering/models/relations` 中英文键 | `en.json`, `zh.json` |
| **7** | **ask_question answer 回调** | 在 `_compose_final_answer` 前增加 `progress_cb("answer", ...)` 回调，补齐最后一步 | `ask_service.py` |
| **8** | **StepProgress 线程安全修复** | `asyncio.Queue.put_nowait()` 从 `run_in_executor` 线程调用不安全; 改为捕获事件循环引用，使用 `loop.call_soon_threadsafe(queue.put_nowait, ...)` 安全调度 | `step_progress.py`, `ws.py` |
| **9** | **StepProgress 提取共享模块** | 将 `StepProgress` 和 `_STEP_KEYS` 从 `ws.py` 提取到 `services/step_progress.py`，避免 `ask.py` 和 `ws.py` 之间的循环导入 | `step_progress.py`, `ask.py`, `ws.py` |
| **10** | **SSE /ask/stream 步骤进度** | SSE 流式端点同样使用 `StepProgress` + `asyncio.Queue`，在 `run_in_executor` 执行期间逐步骤发送 `delta(step)` 事件，与 WebSocket 一致 | `ask.py` |
| **11** | **前端新 ask 清除步骤进度** | `askViaWebSocket` 开头调用 `setWsStepProgress([])` 清除上次进度，避免旧步骤短暂残留 | `page.tsx` |

### 变更记录 v3.1 (2026-05-31)

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **Error/NotFound/global-error 页面** | 新增 Next.js App Router 错误处理: `error.tsx` (路由级)、`global-error.tsx` (全局)、`not-found.tsx` (404); 含 i18n 注册、重试按钮、返回首页 | `app/error.tsx`, `app/global-error.tsx`, `app/not-found.tsx` |
| **2** | **离线检测** | 新增 `useOnlineStatus` hook (基于 `useSyncExternalStore`); `OfflineBanner` 组件固定在顶部显示离线提示; 已集成到 `layout.tsx` | `hooks/useOnlineStatus.ts`, `components/ui/OfflineBanner.tsx`, `app/layout.tsx` |
| **3** | **路由级 loading.tsx** | 为 home/setup/modeling/admin/settings/knowledge/projects 路由添加 `loading.tsx` 骨架屏 | 各路由目录 `loading.tsx` |
| **4** | **useMediaQuery + 响应式侧边栏** | 新增 `useMediaQuery`/`useBreakpoint`/`useIsMobile` hooks; `AppShell` 在 `md` 以下自动折叠侧边栏; `<main>` 添加 `id="main-content"` | `hooks/useMediaQuery.ts`, `layouts/AppShell.tsx` |
| **5** | **Skip-to-content + useFocusTrap** | `layout.tsx` 添加 skip-to-content 链接; 新增 `useFocusTrap` 可复用 hook; `main` 元素添加 `tabIndex={-1}` | `app/layout.tsx`, `hooks/useFocusTrap.ts`, `layouts/AppShell.tsx` |
| **6** | **i18n 错误/离线翻译键** | `en.json`/`zh.json` 新增 error.title/unexpected/critical/criticalDesc/goHome/offline/offlineDesc/notFoundDesc 等翻译键 | `en.json`, `zh.json` |
| **7** | **SSO/OIDC 完整实现** | 后端: `sso_service.py` 实现 OIDC discovery/authorize/code exchange/ID token verification/claim→role 映射/自动用户创建; `auth.py` 新增 `GET /auth/sso/login`(重定向)、`GET /auth/sso/callback`(回调)、`POST /auth/sso/token`(API 端点); 前端: 登录页动态检测 SSO 启用状态并显示 SSO 按钮; SSO 回调 token 自动完成登录 | `services/sso_service.py`, `routers/auth.py`, `models/schemas.py`, `login/page.tsx` |
| **8** | **RLS/CLS 表达式级 MASK 重写** | `plan_secured_sql` 新增 `_apply_cls_mask_to_sql()`: 在 SQL 执行前将 MASK 列引用替换为字面量常量(如 `'***'`), 避免 CONCAT(name, salary) 泄露真实数据 | `security_policy_service.py` |
| **9** | **跨源谓词/投影下推** | `_model_source_select` 从 `SELECT * FROM t LIMIT 5000` 升级为支持 `WHERE` 子句下推和列投影下推; 新增 `_extract_predicate_pushdown` (从 WHERE 中提取模型相关条件) 和 `_extract_projection_pushdown` (从 SELECT 中提取模型相关列) | `ask_service.py` |
| **10** | **README.md + CONTRIBUTING.md** | 项目 README (架构、快速开始、项目结构、关键特性) 和 CONTRIBUTING (开发环境、代码风格、PR 规范、测试) | `README.md`, `CONTRIBUTING.md` |
| **11** | **跨源聚合下推 + 成本优化** | 新增 `_detect_aggregate_pushdown` 检测 GROUP BY/聚合函数; `_should_pushdown_aggregate` 判断是否将聚合下推到源; 聚合下推时扩大物化行数上限 5 倍并合并引用列到投影 | `ask_service.py` |
| **12** | **列血缘追踪 + 表达式 MASK 检测** | `compute_column_lineage` 从 SQL AST 追踪输出列到源列的映射; `detect_masked_columns_in_expressions` 检测 MASK 列在表达式中被引用的位置; `plan_secured_sql` 返回 `mask_in_expressions` 和 `column_lineage` 信息 | `security_policy_service.py` |
| **13** | **Dashboard viewer-aware 重算** | `preview_item` 检测 RLS 策略存在时重新执行 SQL 查询替代使用缓存数据, 确保不同权限用户看到不同行级数据; CLS 掩码和隐藏逻辑继续在结果行上应用 | `dashboards.py` |
| **14** | **i18n 多语言翻译** | 新增 7 种语言完整翻译 (es/fr/de/ja/ko/pt/ru 各 1049 个键); `locales.ts` 注册新语言到 MESSAGES 对象; 语言切换器支持 9 种语言 (en/zh/es/fr/de/ja/ko/pt/ru) | `locales/*.json`, `locales.ts` |
| **15** | **Vega 动态导入** | `ChartContainer` 的 `VegaEmbed` 从静态导入改为 `next/dynamic` 懒加载 (`ssr: false`), 减少 Vega 库对首屏加载的阻塞 | `ChartContainer.tsx` |
| **16** | **项目导出/导入服务** | `ExportService` 完整实现: `export_project` 输出项目+模型+关系+数据源绑定+知识库为 YAML/JSON; `import_project` 从 YAML/JSON 创建完整项目及其模型和关系; `export_audit_logs` 输出审计日志为 CSV/JSON; 路由: `GET /projects/{id}/export`、`POST /projects/import/file` | `export_service.py`, `projects.py` |
| **17** | **Accessibility: motion-safe + ARIA** | ThinkingSteps 添加 `role="region"` 和 `aria-live="polite"`; 脉冲指示器使用 `motion-safe:animate-pulse` 替代 `animate-pulse`; OfflineBanner 添加 `aria-live="assertive"` | `ThinkingSteps.tsx`, `OfflineBanner.tsx` |
| **18** | **跨源聚合下推 + 成本优化** | 新增 `_detect_aggregate_pushdown` 检测 GROUP BY/聚合; `_should_pushdown_aggregate` 判断是否下推; 聚合下推时扩大物化行数上限 5 倍 | `ask_service.py` |
| **19** | **列血缘追踪 + 表达式 MASK 检测** | `compute_column_lineage` 追踪输出列到源列; `detect_masked_columns_in_expressions` 检测 MASK 列在表达式中的引用; `plan_secured_sql` 返回 `mask_in_expressions` 和 `column_lineage` | `security_policy_service.py` |
| **20** | **Dashboard viewer-aware 重算** | `preview_item` 检测 RLS 策略时用 `execute_project_sql` 重新执行 SQL, 确保不同权限用户看到不同行数据 | `dashboards.py` |
| **21** | **Tauri 桌面壳升级** | `Cargo.toml` edition 2024→2021, 添加 `tauri-plugin-shell` + `open` 依赖; `main.rs` 重写为使用 Shell plugin 侧边栏管理后端进程; 新增 `tauri.ts` 前端绑定 (isTauri/getAppInfo/onBackendStatus); CSP 添加 ws/font/img 策略 | `src-tauri/`, `frontend/src/lib/tauri.ts` |
| **22** | **MobileLayout 路由集成** | `MobileLayout` 新增路由导航 (home/dashboard/settings), 使用 `usePathname`/`useRouter`; `AppShell` 使用 `useIsMobile()` 条件渲染移动/桌面布局; ARIA 增强 (`aria-label`, `aria-current`) | `MobileLayout.tsx`, `AppShell.tsx` |
| **23** | **i18n 15语言翻译** | 7+6=13 种新语言完整翻译 (es/fr/de/ja/ko/pt/ru/ar/hi/id/it/nl/pl), 每种 1049 键; `locales.ts` 注册所有 15 种语言 (含 en/zh) | `locales/*.json`, `locales.ts` |
| **24** | **Vega 动态导入** | `ChartContainer` 的 `VegaEmbed` 改为 `next/dynamic` 懒加载, 减少 Vega 库对首屏阻塞 | `ChartContainer.tsx` |
| **25** | **Bundle Analyzer** | `next.config.ts` 集成 `@next/bundle-analyzer`; `npm run analyze` 脚本 | `next.config.ts`, `package.json` |
| **26** | **useRouteFocus** | 新增 `useRouteFocus` hook — 路由变化时自动聚焦 `#main-content`; 集成到 `AuthGuard` | `hooks/useRouteFocus.ts`, `AuthGuard.tsx` |
| **27** | **项目导出/导入服务** | `ExportService` 完整实现: YAML/JSON 项目导出 (模型+关系+数据源+知识库); 项目导入 + ID 映射重建; 审计日志 CSV/JSON 导出; `GET /projects/{id}/export`, `POST /projects/import/file` | `export_service.py`, `projects.py` |
| **28** | **Legacy SQLite 迁移** | `migration_service.py` 实现从 wren-ui SQLite 读取项目/模型/字段/关系/数据源/知识库并写入 PrismBI DuckDB; `POST /projects/migrate/sqlite` 端点 | `migration_service.py`, `projects.py` |
| **29** | **generate-icons.sh** | Tauri 图标生成脚本 (ImageMagick/Pillow) | `src-tauri/generate-icons.sh` |

### 变更记录 v3.4 (2026-06-01)

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **SSO redirect URI 白名单验证** | `_validate_redirect_uri()` 校验 URI 绝对路径和 http/https 协议; `allowed_redirect_origins` 配置项校验来源白名单; callback 和 token 端点统一调用验证 | `sso_service.py`, `auth.py` |
| **2** | **SSO nonce 防重放** | `get_authorization_url` 新增 `nonce` 参数发送到 OIDC provider; `verify_id_token` 新增 `nonce` 校验匹配 ID token 中的 nonce 声明, 防止重放攻击 | `sso_service.py`, `auth.py` |
| **3** | **SSO state/nonce 生命周期管理** | `generate_state()`/`store_state()`/`consume_state()` 三函数管理 SSO state + nonce; state 存储在 `metadata.settings` 带时间戳; `consume_state` 原子删除 + 10 分钟 TTL 校验 | `sso_service.py` |
| **4** | **SSO email 碰撞检测** | `sso_login_or_create` 新用户创建前检查 email 是否已被非 SSO 用户占用, 阻止碰撞并给出明确错误信息 | `sso_service.py` |
| **5** | **SSO 30 个测试用例** | `test_auth.py` 新增 `TestSSOService` 测试类: redirect URI 验证 (有效/相对路径/javascript/白名单/无白名单), claim→role 映射, login_or_create (新建/已有/inactive/碰撞/缺sub), 端点 (禁用/无效 state/禁用 token), state 生命周期 | `tests/test_auth.py` |
| **6** | **MobileLayout More 菜单** | 底部导航 3→4 Tab (Home/Dashboard/More/Settings); More 打开 BottomSheet 提供 Knowledge/Modeling/Projects 导航; `useIsMobile` 识别更多路由高亮 More | `MobileLayout.tsx` |
| **7** | **CompactPromptBar** | 移动端专用紧凑输入框: 单行 input + 圆形发送按钮, 适合小屏幕使用 | `mobile/CompactPromptBar.tsx` |
| **8** | **MobileChartViewer** | 移动端图表查看器: 使用 BottomSheet 全屏展示 ChartContainer, 标题可配置 | `mobile/MobileChartViewer.tsx` |
| **9** | **AppShell onRefresh** | Mobile Layout 集成 `router.refresh()` 作为下拉刷新回调, PullToRefresh 现在激活 | `layouts/AppShell.tsx` |
| **10** | **24 语言 i18n** | 新增 9 种语言翻译 (bn/ur/ms/vi/th/tr/uk/fa/sw 各 1049 键); locales.ts 注册所有 24 种语言到 MESSAGES | `locales/*.json`, `locales.ts` |
| **11** | **ChartEditor 懒加载** | ResponseCard 中 ChartEditor 从静态导入改为 `next/dynamic` 懒加载, 带骨架屏 loading, 减少 Vega 编辑器对首屏的阻塞 | `ResponseCard.tsx` |
| **12** | **SSO [~] → [x]** | DESIGN.md Phase 3 SSO/OIDC 标记从 [~] 更新为 [x] | §18 Phase 3 |

### 变更记录 v3.5 (2026-06-01)

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **ConnectionForm i18n** | 44 个硬编码英文字符串替换为 i18n 键; 包括认证方式、文件上传、AWS/Athena/Redshift/Databricks 配置字段; FileUpload/KeyValueList/StringList 子组件使用 `useI18nStore(t)` | `setup/ConnectionForm.tsx`, `locales/en.json`, `locales/zh.json` |
| **2** | **17 个 loading.tsx 路由骨架** | 为 login/dashboard/thread/dashboard-detail/knowledge/settings/admin/api-management 等路由添加 loading.tsx 骨架屏, 使用 i18n | 各 `app/*/loading.tsx` |
| **3** | **TanStack Query 指数退避重试** | `QueryClientProvider` 从 `retry: 1` 升级为 `retry: shouldRetry`(4xx 不重试, 其他最多2次)+`retryDelay: exponentialBackoff`(1s/2s/4s/...) | `QueryClientProvider.tsx` |
| **4** | **StreamContent 无障碍** | 添加 `role="region"` + `aria-live="polite"` + `aria-label`; 光标动画改用 `motion-safe:animate-pulse` 替代 `animate-pulse` | `StreamContent.tsx` |
| **5** | **prefers-reduced-motion CSS** | globals.css 添加 `@media (prefers-reduced-motion: reduce)` 全局规则: 禁用动画/过渡时长(0.01ms)、停止迭代动画、自动滚动行为 | `globals.css` |
| **6** | **Sidebar ARIA 地标** | Desktop nav 添加 `aria-label` 为 "Main navigation" | `Sidebar.tsx` |
| **7** | **RTL 支持** | `i18nStore` 添加 `isRTLLocale()` 检测 RTL 语言 (ar/fa/ur/he); `setLocale` 时自动设置 `document.documentElement.dir` 和 `lang` | `i18nStore.ts` |
| **8** | **Locale 格式化工具** | 新增 `formatDate()`, `formatNumber()`, `formatRelativeTime()` 基于 `Intl.DateTimeFormat/NumberFormat/RelativeTimeFormat` | `i18nStore.ts` |
| **9** | **Expression-level MASK SQL 增强** | `_apply_cls_mask_to_sql` 重写: 不只匹配 `exp.Column` 节点, 还通过 `find_all` 检测表达式中的掩码列引用 (如 `UPPER(secret_col)`, `CONCAT(name, salary)`), 递归替换为字面量 | `security_policy_service.py` |
| **10** | **Mobile 线程页** | `[threadId]/page.tsx` 使用 `useIsMobile()`: 小屏隐藏 `ThreadList`; 内容区响应式 `px-3 md:px-5`; PromptBar/CompactPromptBar 条件渲染 | `[threadId]/page.tsx` |
| **11** | **Deployment/SSO 文档** | README.md 新增: 生产部署、Docker、Nginx 反向代理、SSO/OIDC 配置步骤、完整环境变量表、i18n 更新到 24 语言 | `README.md` |

### 变更记录 v4.0 (2026-06-01)

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **Tauri 桌面壳完善** | Cargo.toml 新增 tauri-plugin-dialog/fs/updater/process + tray-icon feature; main.rs 重写: 系统托盘 (show/quit)、`get_backend_status` 命令、`open_external` 命令、后端进程管理; capabilities/default.json 新增 20+ 权限; tauri.conf.json 更新 CSP (OpenAI/Anthropic/API 域名); generate-icons.sh 生成 5 个图标 | `src-tauri/Cargo.toml`, `src-tauri/src/main.rs`, `src-tauri/tauri.conf.json`, `src-tauri/capabilities/default.json`, `src-tauri/icons/*`, `scripts/build-desktop.sh` |
| **2** | **tauri.ts 前端绑定增强** | 新增 `openFileDialog()`, `saveFileDialog()`, `getBackendStatus()` 函数; `isTauri()` 类型修复; `onBackendStatus()` 事件监听保留 | `frontend/src/lib/tauri.ts` |
| **3** | **移动端组件** | 新增 ReadOnlyModelViewer (只读模型字段查看器); MobileLogin (移动登录页); MobileProfile (个人资料+API Token); ThreadCard (简化线程卡片); CompactRecommendation (紧凑推荐列表) | `components/mobile/*.tsx` |
| **4** | **Capacitor 移动打包** | capacitor.config.ts 配置 (appId/appName/webDir/splash/statusbar/keyboard); 安装 @capacitor/core/cli/android/ios/splash-screen/status-bar/keyboard/haptics | `frontend/capacitor.config.ts`, `package.json` |
| **5** | **导出/导入前端集成** | `projectsApi` 新增 `exportProject` (Blob 下载 YAML/JSON), `importProject` (FormData 文件上传), `migrateFromSqlite` (SQLite 迁移上传); 新增 `DataManagement` 组件含 3 个区块: 导出项目/导入项目/从 wren-ui 迁移 | `lib/api.ts`, `components/settings/DataManagement.tsx` |
| **6** | **迁移服务增强** | `LEGACY_TABLES` 从 6 种表扩展到 12 种: 新增 instruction/sql_pair/thread/thread_response/dashboard/dashboard_item; `migrate_sqlite_to_prismbi` 新增 instructions/sql_pairs/threads+responses/dashboards+items 迁移逻辑; 返回统计增加 instructions/sql_pairs/threads/dashboards 计数 | `services/migration_service.py` |
| **7** | **i18n DataManagement 翻译键** | en.json 新增 16 个 DataManagement 相关翻译键 (exportProject/importProject/migrate 等描述文字) | `locales/en.json` |

### 变更记录 v4.1 (2026-06-08) — 文档同步至代码

| # | 变更 | 详情 | 文件 |
|---|------|------|------|
| **1** | **文档版本 v3.4 → v4.0** | 设计文档页首版本同步为 v4.0, 同步日期更新至 2026-06-08 | `DESIGN.md` |
| **2** | **实现基线更新** | 补充 SSO/OIDC 完整集成、推荐引擎层 3、跨源谓词/投影/聚合下推、CLS 表达式级 MASK 重写、列血缘追踪、步骤进度、Desktop/Mobile 双平台、24 语言 i18n 等描述 | `DESIGN.md (§1 基线)` |
| **3** | **Phase 3 标记同步** | `跨源查询引擎` [~]→[x], `RLS/CLS 与 MDL 层集成` [~]→[x] (补充列血缘追踪、表达式级 MASK、聚合下推) | `DESIGN.md (§18 Phase 3)` |
| **4** | **已知限制更新** | 移除已修复的限制 (SSO/OIDC、跨源下推、Dashboard viewer-aware、WebSocket 逐 chunk/步骤进度、SSE 分块、表达式 MASK), 更新遗留风险描述 | `DESIGN.md (§18 限制)` |
