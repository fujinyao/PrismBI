@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo   PrismBI Desktop Build (Windows 10+/11)
echo   Tauri WebView Shell — connects to external backend
echo ========================================================
echo.

where rustc >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Rust not found. Install from https://rustup.rs/
    exit /b 1
)

echo   Rust:
rustc --version
echo.

set "SRC_TAURI_DIR=%~dp0..\src-tauri"

echo [1/1] Building Tauri application...
cd /d "%SRC_TAURI_DIR%"
call cargo tauri build
if %errorlevel% neq 0 (echo ERROR: Tauri build failed & exit /b 1)

echo.
echo ========================================================
echo   Build complete!
echo.
echo   Output: %SRC_TAURI_DIR%\target\release\bundle\
echo.
echo   The app is a frontend shell that connects to an
echo   external Next.js frontend at http://localhost:5173 by default.
echo.
echo   To change the frontend URL:
echo     - Right-click tray icon - 'Frontend URL...'
echo     - Or set environment variable: PRISMBI_FRONTEND_URL=http://host:port
echo     - Or edit: %%APPDATA%%\ai.prism.bi\frontend_url.txt
echo ========================================================

dir /b "%SRC_TAURI_DIR%\target\release\bundle\*" 2>nul
exit /b 0