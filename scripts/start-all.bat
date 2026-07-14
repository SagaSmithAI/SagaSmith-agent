@echo off
setlocal EnableExtensions
chcp 65001 >nul
title SagaSmith Agent + D&D MCP

rem This script is intentionally the single Windows entry point. The D&D MCP
rem uses stdio, so Nanobot starts and owns it as a child process from config.
cd /d "%~dp0.."

if not exist "config\config.json" (
    echo [ERROR] Missing config\config.json
    pause
    exit /b 2
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv was not found on PATH. Install uv or activate the project environment.
    pause
    exit /b 3
)

set "MCP_EXE=..\SagaSmith-dnd-mcp\.venv\Scripts\sagasmith-dnd-mcp.exe"
if not exist "%MCP_EXE%" (
    echo [ERROR] D&D MCP executable not found: %MCP_EXE%
    echo         Install SagaSmith-dnd-mcp into its .venv before starting the agent.
    pause
    exit /b 4
)

if not exist "workspace\.sagasmith-dnd-mcp" mkdir "workspace\.sagasmith-dnd-mcp"

echo Starting SagaSmith Agent...
echo D&D MCP is configured as a Nanobot stdio child and will connect during agent startup.
echo.
uv run nanobot gateway --foreground --config config\config.json
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] SagaSmith Agent exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
