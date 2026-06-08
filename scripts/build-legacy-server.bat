@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo   PrismBI Legacy Build (Windows 7+ Compatible)
echo   Packages Python backend + pywebview into a single app
echo   No Tauri/WebView2 needed - works on Win7 SP1+
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Python 3.10+ not found. Install from https://python.org/
    exit /b 1
)

where node >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Node.js not found. Install 18+ from https://nodejs.org/
    exit /b 1
)

set "ROOT_DIR=%~dp0.."
set "FRONTEND_DIR=%ROOT_DIR%\frontend"
set "BACKEND_DIR=%ROOT_DIR%\backend"
set "LEGACY_DIR=%ROOT_DIR%\legacy-server"
set "DIST_DIR=%ROOT_DIR%\dist\prismbi-legacy"

echo [1/6] Building frontend...
cd /d "%FRONTEND_DIR%"
call npm install
if %errorlevel% neq 0 (echo ERROR: npm install failed & exit /b 1)
call npm run build
if %errorlevel% neq 0 (echo ERROR: Frontend build failed & exit /b 1)

:: Copy standalone server output so launcher.py can start it with Node.js
mkdir "%FRONTEND_DIR%\out" 2>nul
if exist "%FRONTEND_DIR%\.next\standalone" (
    xcopy /E /I /Y /Q "%FRONTEND_DIR%\.next\standalone\*" "%FRONTEND_DIR%\out\" >nul
) else (
    echo WARNING: .next\standalone not found - frontend UI may not work
)
if exist "%FRONTEND_DIR%\.next\static" (
    mkdir "%FRONTEND_DIR%\out\.next" 2>nul
    xcopy /E /I /Y /Q "%FRONTEND_DIR%\.next\static" "%FRONTEND_DIR%\out\.next\static\" >nul
)

echo [2/6] Installing Python dependencies...
cd /d "%BACKEND_DIR%"
pip install -e . 2>nul
if %errorlevel% neq 0 (
    echo WARNING: pip install -e . failed -- installing dependencies directly
    pip install fastapi "uvicorn[standard]" duckdb python-jose bcrypt websockets cryptography sqlglot pydantic pydantic-settings python-multipart orjson httpx sse-starlette
)
pip install pywebview || echo WARNING: pywebview not installed - native window unavailable

echo [3/6] Installing PyInstaller...
pip show pyinstaller >nul 2>nul || pip install pyinstaller

echo [4/6] Packaging backend into standalone app...
cd /d "%BACKEND_DIR%"
pyinstaller --clean --noconfirm --name prismbi-backend ^
    --icon "%ROOT_DIR%\src-tauri\icons\icon.ico" ^
    --add-data "db;db" ^
    --add-data "models;models" ^
    --add-data "routers;routers" ^
    --add-data "services;services" ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.lifespan.on ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.protocols.websockets.wsproto_impl ^
    --hidden-import duckdb ^
    --hidden-import bcrypt ^
    --hidden-import jose ^
    --hidden-import cryptography ^
    --hidden-import sqlglot ^
    --hidden-import lancedb ^
    --hidden-import pyarrow ^
    --hidden-import orjson ^
    --hidden-import httpx ^
    --hidden-import pydantic_settings ^
    --hidden-import sse_starlette ^
    --hidden-import multipart ^
    --hidden-import webview ^
    --hidden-import asyncio ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    --exclude-module IPython ^
    --exclude-module jupyter ^
    main.py
if %errorlevel% neq 0 (echo ERROR: Backend packaging failed & exit /b 1)

echo [5/6] Assembling distribution package...
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
mkdir "%DIST_DIR%"
mkdir "%DIST_DIR%\backend"
mkdir "%DIST_DIR%\frontend\out"
mkdir "%DIST_DIR%\data"

xcopy /E /I /Y /Q "%BACKEND_DIR%\dist\prismbi-backend\*" "%DIST_DIR%\backend\" >nul
xcopy /E /I /Y /Q "%FRONTEND_DIR%\out\*" "%DIST_DIR%\frontend\out\" >nul
copy /Y "%LEGACY_DIR%\launcher.py" "%DIST_DIR%\" >nul
copy /Y "%LEGACY_DIR%\start.py" "%DIST_DIR%\" >nul
copy /Y "%ROOT_DIR%\src-tauri\icons\icon.ico" "%DIST_DIR%\icon.ico" >nul

echo [6/6] Creating launcher scripts...

(
echo @echo off
echo title PrismBI
echo setlocal
echo.
echo set "PORT="
echo set "NOGUI="
echo set "DATADIR="
echo.
echo :parse_args
echo if "%%~1"=="" goto end_parse
echo if /i "%%~1"=="--port" ^(set "PORT=%%~2" ^& shift^)
echo if /i "%%~1"=="--no-gui" set "NOGUI=1"
echo if /i "%%~1"=="--data-dir" ^(set "DATADIR=%%~2" ^& shift^)
echo if /i "%%~1"=="--debug" set "DEBUG=1"
echo shift
echo goto parse_args
echo :end_parse
echo.
echo if "%%PORT%%"=="" set "PORT=8400"
echo if "%%DATADIR%%"=="" set "DATADIR=%%~dp0data"
echo.
echo if not exist "%%DATADIR%%" mkdir "%%DATADIR%%"
echo.
echo echo ============================================================
echo echo  PrismBI - Business Intelligence Platform
echo echo ============================================================
echo echo.
echo echo  Starting on http://localhost:%%PORT%%
echo echo.
echo.
echo python "%%~dp0launcher.py" --port %%PORT%% --data-dir "%%DATADIR%%" %%NOGUI%% %%DEBUG%%
) > "%DIST_DIR%\prismbi.bat"

(
echo @echo off
echo echo Stopping PrismBI...
echo taskkill /IM prismbi-backend.exe /F 2^>nul
echo timeout /t 2 /nobreak ^>nul
echo echo Done.
) > "%DIST_DIR%\stop.bat"

(
echo @echo off
echo title PrismBI - Server Mode
echo set "PORT=%%1"
echo if "%%PORT%%"=="" set "PORT=8400"
echo set "DATADIR=%%~dp0data"
echo if not exist "%%DATADIR%%" mkdir "%%DATADIR%%"
echo.
echo :: Set required environment variables
echo set "PRISMBI_PORT=%%PORT%%"
echo set "PRISMBI_DATA_DIR=%%DATADIR%%"
echo set "PRISMBI_DB_PATH=%%DATADIR%%\prismbi.duckdb"
echo set "PRISMBI_CORS_ORIGINS=http://localhost:%%PORT%%,http://127.0.0.1:%%PORT%%"
echo.
echo echo PrismBI Server starting on http://localhost:%%PORT%%
echo echo Press Ctrl+C to stop.
echo timeout /t 3 /nobreak ^>nul
echo start "" "http://localhost:%%PORT%%"
echo "%%~dp0backend\prismbi-backend.exe" --host 0.0.0.0 --port %%PORT%%
) > "%DIST_DIR%\server.bat"

(
echo PrismBI - Legacy Edition (Windows 7+ Compatible)
echo ========================================================
echo.
echo QUICK START:
echo   Double-click prismbi.bat to launch the desktop app
echo   Double-click server.bat for server-only mode
echo.
echo COMMAND LINE OPTIONS:
echo   prismbi.bat                     Launch desktop app
echo   prismbi.bat --no-gui            Server mode only
echo   prismbi.bat --port 9000        Use custom port
echo   prismbi.bat --data-dir C:\data Use custom data directory
echo.
echo SYSTEM REQUIREMENTS:
echo   - Windows 7 SP1 or later
echo   - No additional software needed
echo.
echo DATA:
echo   Database and settings stored in: data\ folder
) > "%DIST_DIR%\README.txt"

echo.
echo ========================================================
echo   Legacy build complete!
echo.
echo   Output: %DIST_DIR%\
echo.
echo   This package runs on Windows 7 SP1+ and later.
echo   - Double-click prismbi.bat for desktop app mode
echo   - Double-click server.bat for server-only mode
echo.
echo   To distribute: zip the entire folder.
echo ========================================================
exit /b 0
