@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
set "RUNTIME_MARKER=%CD%\workspace\.sagasmith-runtime.json"

if not exist "%RUNTIME_MARKER%" (
    echo [INFO] SagaSmith local D^&D runtime is not marked as running.
    exit /b 0
)

powershell -NoProfile -Command "$m=Get-Content -LiteralPath '%RUNTIME_MARKER%' -Raw | ConvertFrom-Json; @($m.agent_pid,$m.dnd_gateway_pid,$m.dnd_mcp_pid) | Where-Object { $_ -is [int] -or $_ -is [long] } | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
if errorlevel 1 (
    echo [ERROR] Could not read the runtime marker or stop its exact processes.
    exit /b 1
)
del /q "%RUNTIME_MARKER%"
echo [OK] SagaSmith Agent, D^&D Workbench gateway, and D^&D MCP were stopped.
exit /b 0
