#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "  PrismBI Desktop Build (macOS / Linux / Windows 10+)"
echo "  Tauri WebView Shell — connects to external backend"
echo "========================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_TAURI_DIR="$ROOT_DIR/src-tauri"

command -v rustc >/dev/null 2>&1 || { echo "ERROR: Rust not found. Install from https://rustup.rs/"; exit 1; }

OS="$(uname -s)"
echo "Platform: $OS"
echo "  Rust: $(rustc --version)"
echo ""

if [ "$OS" = "Linux" ]; then
    echo "[0/1] Linux system dependencies check..."
    echo "  If build fails, install with:"
    echo "    Debian/Ubuntu: sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libappindicator3-dev librsvg2-dev patchelf"
    echo "    Fedora: sudo dnf install gtk3-devel webkit2gtk4.1-devel libappindicator-gtk3-devel librsvg2-devel"
    echo "    openSUSE: sudo zypper install libwebkit2gtk-4_1-devel libgtk-3-devel libappindicator3-devel librsvg-devel patchelf"
    echo ""
fi

echo "[1/1] Building Tauri application..."
cd "$SRC_TAURI_DIR"
cargo tauri build

echo ""
echo "========================================================"
echo "  Build complete!"
echo "  Output: $SRC_TAURI_DIR/target/release/bundle/"
echo ""
echo "  The app is a frontend shell that connects to an"
echo "  external Next.js frontend at http://localhost:5173 by default."
echo ""
echo "  To change the frontend URL:"
echo "    - Right-click tray icon → 'Frontend URL...'"
echo "    - Or set environment variable: PRISMBI_FRONTEND_URL=http://host:port"
echo "    - Or edit: ~/.config/ai.prism.bi/frontend_url.txt"
echo "========================================================"
ls -la "$SRC_TAURI_DIR/target/release/bundle/" 2>/dev/null || true