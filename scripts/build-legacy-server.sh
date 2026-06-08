#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "  PrismBI Legacy Build (Win7+ / Old Systems)"
echo "  Native desktop app via pywebview + CEF"
echo "  Falls back to browser if pywebview unavailable"
echo "========================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_DIR="$ROOT_DIR/backend"
LEGACY_DIR="$ROOT_DIR/legacy-server"
DIST_DIR="$ROOT_DIR/dist/prismbi-legacy"

command -v node >/dev/null 2>&1 || { echo "ERROR: Node.js not found"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: Python 3 not found"; exit 1; }

echo "[1/6] Building frontend..."
cd "$FRONTEND_DIR"
npm install
npm run build

# Copy the standalone server output so launcher.py can start it with Node.js
mkdir -p "$FRONTEND_DIR/out"
cp -r "$FRONTEND_DIR/.next/standalone/"* "$FRONTEND_DIR/out/" 2>/dev/null || {
    echo "WARNING: standalone output not found at .next/standalone — frontend UI may not work"
}
# Also copy static assets for the standalone server
if [ -d "$FRONTEND_DIR/.next/static" ]; then
    mkdir -p "$FRONTEND_DIR/out/.next"
    cp -r "$FRONTEND_DIR/.next/static" "$FRONTEND_DIR/out/.next/static" 2>/dev/null || true
fi

echo "[2/6] Installing Python dependencies..."
cd "$BACKEND_DIR"
if ! pip3 install -e . 2>/dev/null; then
    echo "WARNING: pip install -e . failed — installing dependencies directly"
    pip3 install fastapi "uvicorn[standard]" duckdb python-jose bcrypt websockets cryptography sqlglot pydantic pydantic-settings python-multipart orjson httpx sse-starlette
fi
pip3 install pywebview || echo "WARNING: pywebview not installed — native window unavailable"

echo "[3/6] Installing PyInstaller..."
pip3 show pyinstaller >/dev/null 2>&1 || pip3 install pyinstaller

echo "[4/6] Packaging backend into standalone app..."
cd "$BACKEND_DIR"

# Verify backend source directories exist
for d in db models routers services; do
    if [ ! -d "$BACKEND_DIR/$d" ]; then
        echo "WARNING: $BACKEND_DIR/$d not found — some features may be missing"
    fi
done

pyinstaller --clean --noconfirm --name prismbi-backend \
    --icon "$ROOT_DIR/src-tauri/icons/icon.ico" \
    $(for d in db models routers services; do [ -d "$BACKEND_DIR/$d" ] && echo "--add-data $BACKEND_DIR/$d:$d"; done) \
    --hidden-import uvicorn.logging \
    --hidden-import uvicorn.lifespan.on \
    --hidden-import uvicorn.protocols.http.auto \
    --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.protocols.websockets.wsproto_impl \
    --hidden-import duckdb \
    --hidden-import bcrypt \
    --hidden-import jose \
    --hidden-import cryptography \
    --hidden-import sqlglot \
    --hidden-import lancedb \
    --hidden-import pyarrow \
    --hidden-import orjson \
    --hidden-import httpx \
    --hidden-import pydantic_settings \
    --hidden-import sse_starlette \
    --hidden-import multipart \
    --hidden-import webview \
    --hidden-import asyncio \
    --exclude-module tkinter \
    --exclude-module matplotlib \
    --exclude-module IPython \
    --exclude-module jupyter \
    main.py

echo "[5/6] Assembling distribution package..."
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/backend" "$DIST_DIR/frontend/out" "$DIST_DIR/data"

cp -r "$BACKEND_DIR/dist/prismbi-backend/"* "$DIST_DIR/backend/" 2>/dev/null || true
cp -r "$FRONTEND_DIR/out/"* "$DIST_DIR/frontend/out/" 2>/dev/null || true
cp "$LEGACY_DIR/launcher.py" "$DIST_DIR/"
cp "$LEGACY_DIR/start.py" "$DIST_DIR/"
cp "$ROOT_DIR/src-tauri/icons/icon.ico" "$DIST_DIR/icon.ico" 2>/dev/null || true

echo "[6/6] Creating launcher scripts..."

cat > "$DIST_DIR/prismbi.sh" << 'LAUNCHER'
#!/bin/bash
# PrismBI Desktop (Win7+ Compatible)
# Uses pywebview for native window, falls back to browser
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/data"

python3 "$DIR/launcher.py" "$@"
LAUNCHER
chmod +x "$DIST_DIR/prismbi.sh"

cat > "$DIST_DIR/server.sh" << 'SERVER'
#!/bin/bash
# PrismBI Server Mode - Access from any browser on the network
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-8400}"

echo ""
echo "  PrismBI Server"
echo "  Backend: http://localhost:$PORT"
echo "  Press Ctrl+C to stop"
echo ""

# Set required environment variables
export PRISMBI_PORT="$PORT"
export PRISMBI_DATA_DIR="$DIR/data"
export PRISMBI_DB_PATH="$DIR/data/prismbi.duckdb"
export PRISMBI_CORS_ORIGINS="http://localhost:$PORT,http://127.0.0.1:$PORT"

mkdir -p "$DIR/data"

if command -v xdg-open >/dev/null 2>&1; then
    (sleep 3 && xdg-open "http://localhost:$PORT") &
elif command -v open >/dev/null 2>&1; then
    (sleep 3 && open "http://localhost:$PORT") &
fi

"$DIR/backend/prismbi-backend" --host 0.0.0.0 --port "$PORT"
SERVER
chmod +x "$DIST_DIR/server.sh"

cat > "$DIST_DIR/README.txt" << 'README'
PrismBI - Legacy Edition (Windows 7+ / macOS / Linux)
=======================================================

QUICK START:
  macOS/Linux:
    ./prismbi.sh                    Launch desktop app (native window)
    ./prismbi.sh --no-gui           Server-only mode (browser access)
    ./prismbi.sh --port 9000        Use custom port
    ./server.sh 9000                Start server on port 9000

  Windows:
    prismbi.bat                     Launch desktop app (native window)
    server.bat                      Start server only (network accessible)

DESKTOP APP MODE:
  When pywebview is available, PrismBI opens as a native
  desktop window - just like the Windows 10+ Tauri version.
  This uses CEF (Chromium Embedded Framework) internally.

  If pywebview is not available, it automatically falls back
  to opening your default web browser.

SERVER MODE:
  Run --no-gui flag or server.bat/server.sh for server-only
  mode. Access from any device on the network.

SYSTEM REQUIREMENTS:
  - Windows 7 SP1 or later / macOS 10.13+ / Linux (glibc 2.17+)
  - No additional software needed
  - Browser: Chrome 49+, Firefox 52+, Safari 13+, Edge 14+

DATA:
  Database and settings stored in: data/ folder
  To backup, copy the data/ folder to a safe location.
README

echo ""
echo "========================================================"
echo "  Legacy build complete!"
echo "  Output: $DIST_DIR"
echo ""
echo "  This package runs on Windows 7 SP1+ and later."
echo "  - ./prismbi.sh for native desktop app (pywebview)"
echo "  - ./server.sh for server-only mode"
echo ""
echo "  To distribute: zip/tar the entire folder."
echo "========================================================"
