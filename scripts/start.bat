@echo off
cd /d "%~dp0.."

:: ============================================================
::  dnd-dm-agent startup (WebUI + Telegram only, no QQ)
:: ============================================================

title dnd-dm-agent

echo ============================================
echo   dnd-dm-agent
echo ============================================

:: ---------- nanobot gateway (WebUI + Telegram, NapCat disabled) ----------
echo [..] Starting nanobot gateway (GPU + ChromaDB)...
start "nanobot-gateway" cmd /k "cd /d %cd% && set DND_EMBEDDING_DEVICE=cuda && set CHROMA_DB_PATH=%cd%\data\chroma && set NANOBOT_NO_NAPCAT=1 && uv run nanobot gateway --config config\config.json"

echo.
echo ============================================
echo   WebUI:  http://127.0.0.1:18765
echo ============================================
pause
