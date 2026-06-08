#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_DIR="$ROOT_DIR/backend"

cleanup() {
  echo ""
  echo "Shutting down PrismBI dev servers..."
  if [ -n "${BACKEND_PID:-}" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -n "${FRONTEND_PID:-}" ]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  echo "Done."
}
trap cleanup EXIT INT TERM

echo "=== PrismBI Development Server ==="
echo ""

if [ ! -f "$BACKEND_DIR/.venv/bin/activate" ]; then
  echo "[backend] Creating virtual environment..."
  python3.12 -m venv "$BACKEND_DIR/.venv"
fi

echo "[backend] Activating virtual environment..."
# shellcheck disable=SC1091
source "$BACKEND_DIR/.venv/bin/activate"

echo "[backend] Installing dependencies..."
pip install -q -e "$BACKEND_DIR" 2>&1 | tail -1

echo "[backend] Starting uvicorn on :8400..."
cd "$BACKEND_DIR"
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8400 \
  --reload \
  --reload-dir "$BACKEND_DIR" \
  --log-level info &
BACKEND_PID=$!

echo "[frontend] Installing dependencies..."
cd "$FRONTEND_DIR"
npm install --silent 2>&1 | tail -1

echo "[frontend] Starting Next.js dev server on :5173..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "  Backend:  http://localhost:8400"
echo "  API docs: http://localhost:8400/docs"
echo "  Frontend: http://localhost:5173"
echo "========================================"
echo "  Press Ctrl+C to stop all servers"
echo "========================================"

wait
