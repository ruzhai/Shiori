@echo off
start "Shiori Backend" python api_server.py
timeout /t 2 /nobreak >nul
cd electron-frontend && npm start
