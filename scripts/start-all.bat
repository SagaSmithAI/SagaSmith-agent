@echo off
setlocal EnableExtensions
chcp 65001 >nul
title SagaSmith Agent + TTRPG MCP + UI Gateway

rem This script is intentionally the single Windows entry point. The D&D MCP
rem uses stdio, so Nanobot starts and owns it as a child process from config.
cd /d "%~dp0.."

if not exist "config\config.json" (
    echo [ERROR] Missing config\config.json
    echo         Run install-all.bat, then follow docs\guides\configure-mcp-tools.md.
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
set "MCP_PYTHON=..\SagaSmith-dnd-mcp\.venv\Scripts\python.exe"
set "COC_MCP_EXE=..\SagaSmith-coc-mcp\.venv\Scripts\sagasmith-coc-mcp.exe"
if not exist "%MCP_EXE%" (
    echo [ERROR] D^&D MCP executable not found: %MCP_EXE%
    echo         Run install-all.bat before starting the agent.
    pause
    exit /b 4
)
if not exist "%MCP_PYTHON%" (
    echo [ERROR] D^&D MCP Python runtime not found: %MCP_PYTHON%
    pause
    exit /b 5
)
if not exist "%COC_MCP_EXE%" (
    echo [ERROR] CoC MCP executable not found: %COC_MCP_EXE%
    echo         Run install-all.bat before starting the agent.
    pause
    exit /b 6
)

"%MCP_PYTHON%" ..\SagaSmith-dnd-mcp\scripts\validate_agent_runtime.py --config config\config.json --agent-root .
if errorlevel 1 (
    echo [ERROR] SagaSmith Skills and MCP configuration preflight failed.
    pause
    exit /b 9
)

if not exist "workspace\.sagasmith-dnd-mcp" mkdir "workspace\.sagasmith-dnd-mcp"
if not exist "workspace\.sagasmith-coc-mcp" mkdir "workspace\.sagasmith-coc-mcp"
set "SAGASMITH_DND_MCP_HOME=%CD%\workspace\.sagasmith-dnd-mcp"
set "SAGASMITH_DND_SKILLS_DIR=%CD%\..\SagaSmith-dnd-skills"
set "SAGASMITH_MODULEGEN_SKILLS_DIR=%CD%\..\SagaSmith-module-gen-skills"
set "SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS=%CD%\..\reference\DnD-Books\5e\Books"
set "SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS=%CD%\..\reference\DnD-Books\5e\Campaign"
if not defined SAGASMITH_DND_MCP_RULE_OCR set "SAGASMITH_DND_MCP_RULE_OCR=1"
if not defined SAGASMITH_DND_MCP_RULE_OCR_SCALE set "SAGASMITH_DND_MCP_RULE_OCR_SCALE=2.0"
if not defined SAGASMITH_DND_MCP_MODULE_OCR set "SAGASMITH_DND_MCP_MODULE_OCR=1"
if not defined SAGASMITH_DND_MCP_MODULE_OCR_SCALE set "SAGASMITH_DND_MCP_MODULE_OCR_SCALE=2.0"
set "SAGASMITH_COC_MCP_HOME=%CD%\workspace\.sagasmith-coc-mcp"
set "SAGASMITH_COC_SKILLS_DIR=%CD%\..\SagaSmith-coc-skills"
set "SAGASMITH_DND_GATEWAY_HOST=127.0.0.1"
if not defined SAGASMITH_DND_GATEWAY_PORT set "SAGASMITH_DND_GATEWAY_PORT=8766"

echo Starting principal-aware D^&D UI gateway on http://127.0.0.1:%SAGASMITH_DND_GATEWAY_PORT% ...
for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath '%MCP_PYTHON%' -ArgumentList @('-m','sagasmith_dnd_mcp.gateway') -WorkingDirectory '..\SagaSmith-dnd-mcp' -WindowStyle Hidden -PassThru; $p.Id"') do set "GATEWAY_PID=%%P"
if not defined GATEWAY_PID (
    echo [ERROR] Failed to start the D^&D UI gateway.
    pause
    exit /b 7
)

powershell -NoProfile -Command "$deadline = (Get-Date).AddSeconds(12); do { try { $response = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:%SAGASMITH_DND_GATEWAY_PORT%/api/health' -TimeoutSec 1; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 300 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo [ERROR] D^&D UI gateway did not become ready.
    taskkill /PID %GATEWAY_PID% /T /F >nul 2>&1
    pause
    exit /b 8
)

echo Starting SagaSmith Agent...
echo D^&D and CoC MCP servers are configured as Nanobot stdio children.
echo D^&D UI gateway is ready; it shares the MCP-owned store and routes writes through MCP tools.
echo.
uv run nanobot gateway --foreground --config config\config.json
set "EXIT_CODE=%ERRORLEVEL%"
taskkill /PID %GATEWAY_PID% /T /F >nul 2>&1

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] SagaSmith Agent exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
