@echo off
echo Stopping old backend processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im python3.exe >nul 2>&1
taskkill /f /im python3.12.exe >nul 2>&1
echo Waiting for port 5000 to be released...
:waitport
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":5000.*LISTENING" >nul
if %errorlevel% equ 0 goto waitport
echo Starting backend...
start "Shiori Backend" python api_server.py
timeout /t 3 /nobreak >nul
cd electron-frontend && npm start
