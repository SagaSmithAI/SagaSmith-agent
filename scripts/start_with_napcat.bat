@echo off
cd /d "%~dp0.."

:: ============================================================
::  dnd-dm-agent startup
::  NapCat QQ + nanobot gateway
:: ============================================================

title dnd-dm-agent

echo ============================================
echo   dnd-dm-agent
echo ============================================

:: ---------- NapCat QQ ----------
echo [..] Starting NapCat QQ...
start "NapCat-QQ" cmd /c "cd /d tools\NapCat.Shell && launcher.bat 1534055688"

:: ---------- nanobot gateway ----------
set "ROOT=%~dp0.."
echo [..] Starting nanobot gateway (auto embedding mode + ChromaDB)...
start "nanobot-gateway" cmd /k "cd /d %ROOT% && set DND_EMBEDDING_MODE=auto && set CHROMA_DB_PATH=%ROOT%\data\chroma && uv run nanobot gateway --config %ROOT%\config\config.json"

echo.
echo ============================================
echo   WebUI:  http://127.0.0.1:18765
echo ============================================
pause
