# Contributing to PrismBI

## Development Setup

1. Fork and clone the repository
2. Backend: `cd backend && pip install -e ".[dev]"`
3. Frontend: `cd frontend && npm install`
4. Create a feature branch from `main`

## Code Style

### Python (Backend)

- Follow PEP 8 with ruff formatting
- Type hints on all function signatures
- Docstrings on public functions/classes
- No unrelated changes in a single PR

### TypeScript/React (Frontend)

- Strict TypeScript (no `any` casts without justification)
- Functional components with hooks
- Zustand for state management
- Tailwind CSS for styling
- i18n: all user-facing strings through `t()` function

## Commit Messages

Use conventional commits:

- `feat: add SSO login flow`
- `fix: correct cross-source predicate pushdown`
- `refactor: extract StepProgress to shared module`
- `docs: update README`

## Pull Requests

- Keep PRs focused on a single concern
- Include tests for new behavior (backend: pytest, frontend: build passes)
- Ensure `python -m pytest --tb=short -q` passes
- Ensure `npx next build` passes with no type errors
- Add i18n keys for both `en.json` and `zh.json`

## Testing

### Backend

```bash
cd backend
python -m pytest --tb=short -q
```

### Frontend

```bash
cd frontend
npx next build
```

## Security

- Never commit secrets or API keys
- Use environment variables for all sensitive configuration
- Report security vulnerabilities privately

## Questions

Open an issue or check DESIGN.md for architecture details.