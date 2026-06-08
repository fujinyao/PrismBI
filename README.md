# PrismBI

Next-generation AI-powered Business Intelligence platform.

## Architecture

- **Backend**: Python (FastAPI) + DuckDB + wren-core
- **Frontend**: Next.js 16 + React 19 + TypeScript + Tailwind CSS + Zustand
- **AI/LLM**: Multi-provider LLM integration (OpenAI, Anthropic, local models)
- **Real-time**: WebSocket-based ask progress, SSE streaming

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 20+
- npm or pnpm

### Backend

```bash
cd backend
pip install -e ".[dev]"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET_KEY` | Yes (prod) | Secret key for JWT signing |
| `PRISMBI_JWT_SECRET_KEY_FILE` | No | File path for persistent development JWT key when `JWT_SECRET_KEY` is unset |
| `PRISMBI_ENABLE_REGISTRATION` | No | Enable self-registration (`true`/`false`) |
| `PRISMBI_ENV` | No | Environment (`prod`/`production` for strict mode) |
| `PRISMBI_MAX_SESSION_DAYS` | No | Max session duration in days (default: 30) |
| `PRISMBI_FRONTEND_URL` | No | Frontend base URL for SSO redirects |

## Testing

### Backend

```bash
cd backend
python -m pytest --tb=short -q
```

### Frontend

```bash
cd frontend
npx next build  # TypeScript check + build
```

## Project Structure

```
PrismBI/
├── backend/
│   ├── main.py              # FastAPI application entry point
│   ├── routers/             # API endpoints
│   │   ├── auth.py          # Authentication + SSO/OIDC
│   │   ├── ws.py            # WebSocket ask with step progress
│   │   ├── ask.py           # Ask endpoint + SSE streaming
│   │   ├── admin.py         # Admin CRUD (users, roles, SSO, RLS/CLS)
│   │   ├── projects.py      # Project management
│   │   └── datasources.py   # Datasource management
│   ├── services/
│   │   ├── ask_service.py   # NL2SQL engine + cross-source query
│   │   ├── llm_service.py   # Multi-provider LLM integration
│   │   ├── security_policy_service.py  # RLS/CLS enforcement
│   │   ├── sso_service.py   # OIDC discovery + token verification
│   │   └── step_progress.py # WebSocket step progress bridge
│   ├── db/                  # DuckDB schema + migrations
│   └── models/              # Pydantic schemas
├── frontend/
│   ├── src/
│   │   ├── app/             # Next.js App Router pages
│   │   ├── components/      # React components
│   │   ├── hooks/           # Custom hooks (useOnlineStatus, useFocusTrap, useMediaQuery)
│   │   ├── stores/          # Zustand state stores
│   │   └── lib/             # API client, i18n, utilities
│   └── package.json
├── DESIGN.md                # Architecture & design document
└── README.md
```

## Key Features

- **NL2SQL**: Natural language to SQL with multi-provider LLM
- **Cross-Source Queries**: Join data from PostgreSQL, MySQL, ClickHouse, MS SQL, Trino, DuckDB
- **WebSocket Step Progress**: Real-time ask progress streaming (understand → retrieve → organize → execute → answer)
- **SSO/OIDC**: OpenID Connect login with claim-to-role mapping
- **RLS/CLS**: Row-level and column-level security with SQL-level expression MASK rewriting
- **Dashboard**: Interactive dashboards with Vega-Lite charts
- **Knowledge Base**: Instructions + SQL pairs for domain-specific guidance
- **Recommendation Engine**: Multi-layer recommendations (MDL, session, catalog, collaborative)
- **Admin Console**: Users, roles, permissions, SSO, audit logs, API tokens
- **i18n**: 24 languages (en, zh, es, fr, de, ja, ko, pt, ru, ar, hi, id, it, nl, pl, bn, ur, ms, vi, th, tr, uk, fa, sw) with 1078+ translation keys
- **Accessibility**: Skip-to-content, focus traps, ARIA landmarks, reduced-motion support, live regions for streaming

## Deployment

### Production

```bash
# Backend
cd backend
pip install -e ".[dev]"
PRISMBI_ENV=production JWT_SECRET_KEY=your-secret-key python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

# Frontend
cd frontend
npm install
npm run build
npm start  # Starts Next.js production server
```

### Docker

```bash
docker build -t prismbi .
docker run -p 8000:8000 -p 3000:3000 \
  -e JWT_SECRET_KEY=your-secret-key \
  -e PRISMBI_ENV=production \
  -v $(pwd)/data:/app/data \
  prismbi
```

### SSO/OIDC Setup

1. Configure your OIDC provider (Google, Okta, Azure AD, etc.)
2. Set the **Authorized redirect URI** to: `https://your-domain/api/auth/sso/callback`
3. In PrismBI Admin → SSO Settings:
   - Enable SSO
   - Set Issuer URL (e.g. `https://accounts.google.com`)
   - Set Client ID and Client Secret from your provider
   - Configure claim-to-role mapping (e.g. `{"groups": "admin"}` maps `groups` claim to `admin` role)
4. Optionally set `allowed_redirect_origins` in SSO config to restrict redirect URIs

### Reverse Proxy (Nginx)

```nginx
server {
    listen 443 ssl;
    server_name prismbi.example.com;

    # SSE: keep stream unbuffered and persistent
    location = /api/ask/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket ask endpoint
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### All Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET_KEY` | Yes (prod) | - | Secret key for JWT token signing |
| `PRISMBI_JWT_SECRET_KEY_FILE` | No | `./data/jwt_secret.key` | Persistent development JWT key path when `JWT_SECRET_KEY` is unset |
| `PRISMBI_ENABLE_REGISTRATION` | No | `false` | Enable self-registration |
| `PRISMBI_ENV` | No | - | `prod`/`production` for strict JWT mode |
| `PRISMBI_MAX_SESSION_DAYS` | No | `30` | Maximum session duration in days |
| `PRISMBI_FRONTEND_URL` | No | `/` | Frontend base URL (SSO redirects) |
| `PRISMBI_DATA_DIR` | No | `./data` | DuckDB database directory |
| `PRISMBI_ENCRYPTION_KEY` | Yes (prod) | - | Fernet key for encrypting sensitive settings |
| `PRISMBI_ALLOW_DEFAULT_SECRET` | No | `false` | Allow default JWT secret (dev only) |
| `LLM_PROVIDER` | No | `openai` | Default LLM provider |
| `LLM_MODEL` | No | `gpt-4o` | Default LLM model |
| `LLM_ENDPOINT` | No | `https://api.openai.com/v1` | LLM API endpoint |
| `LLM_API_KEY` | No | - | LLM API key (or set in settings UI) |
| `NEXT_PUBLIC_ASK_HTTP_FALLBACK` | No | `0` | Frontend build-time flag. `0` keeps long-connection mode (`WS -> SSE`) and disables short HTTP fallback |
| `NEXT_PUBLIC_ASK_SSE_IDLE_TIMEOUT_MS` | No | `0` | Frontend build-time SSE idle timeout. `0` disables client-side idle abort for `/api/ask/stream` |

### LLM HTTP Resilience Settings (Runtime)

These values are managed in **Admin -> Settings -> LLM -> Advanced** and take effect at runtime (no restart required).

| Setting Key | Default | Description | Recommended Range |
|---|---|---|---|
| `llm_max_retries` | `3` | Max HTTP retries per LLM request before surfacing an error | `2-4` |
| `llm_retry_base_delay_ms` | `200` | Initial backoff delay for retry loop | `100-500` |
| `llm_retry_max_delay_ms` | `2000` | Upper bound of exponential backoff delay | `1000-5000` |
| `llm_http_circuit_enabled` | `true` | Enables LLM HTTP circuit breaker fast-fail | `true` in production |
| `llm_http_circuit_failure_threshold` | `3` | Consecutive failures required to open the circuit for one provider:endpoint:model key | `3-5` |
| `llm_http_circuit_open_seconds` | `60` | Open-window duration before allowing retry attempts again | `30-120` |

Tuning guidance:
- Lower `llm_max_retries` if provider outages cause high tail latency.
- Increase `llm_http_circuit_open_seconds` when repeated upstream failures happen in short bursts.
- Keep `llm_http_circuit_failure_threshold` at `3` unless your provider has transient single-request blips.
