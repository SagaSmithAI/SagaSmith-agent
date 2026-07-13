@echo off
chcp 65001 >nul
title nanobot - Gateway

cd /d "%~dp0"
uv run nanobot gateway --foreground --config config\config.json

pause
