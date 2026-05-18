@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================
echo   Shiori Launcher
echo ========================================
echo.

:: ── Python venv ────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating Python venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Is Python installed?
        pause & exit /b 1
    )
) else (
    echo [1/4] venv found
)
call .venv\Scripts\activate.bat

:: ── Python deps ────────────────────────────
echo [2/4] Checking Python dependencies...
pip install -r requirements.txt flask flask-cors -q
if errorlevel 1 (
    echo [ERROR] Failed to install Python dependencies
    pause & exit /b 1
)

:: ── Node.js deps ───────────────────────────
echo [3/4] Checking Node.js dependencies...
if not exist "electron-frontend\node_modules" (
    cd electron-frontend
    call npm install
    cd ..
) else (
    echo        node_modules found
)

:: ── Port ───────────────────────────────────
echo [4/4] Checking port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000.*LISTENING"') do (
    echo        Port occupied by PID=%%a, killing...
    taskkill /f /pid %%a >nul 2>&1
    timeout /t 1 /nobreak >nul
)

echo.
echo ========================================
echo   Launching...
echo ========================================
echo.

start "Shiori Backend" .venv\Scripts\python.exe api_server.py
timeout /t 3 /nobreak >nul

cd electron-frontend && npm start

endlocal
