@echo off
setlocal EnableExtensions
chcp 65001 >nul
title SagaSmith Local D^&D

cd /d "%~dp0.."
set "AGENT_ROOT=%CD%"
set "MCP_ROOT=%CD%\..\SagaSmith-dnd-mcp"
set "UI_DIST=%CD%\..\SagaSmith-dnd-ui\dist"
set "MCP_EXE=%MCP_ROOT%\.venv\Scripts\sagasmith-dnd-mcp.exe"
set "MCP_PYTHON=%MCP_ROOT%\.venv\Scripts\python.exe"
set "AGENT_PYTHON=%CD%\.venv\Scripts\python.exe"
set "RUNTIME_MARKER=%CD%\workspace\.sagasmith-runtime.json"

if not exist "config\config.json" (
    echo [ERROR] Missing config\config.json
    echo         Run install-all.bat, then scripts\configure_dnd_local.py --apply.
    pause
    exit /b 2
)
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
if not exist "%AGENT_PYTHON%" (
    echo [ERROR] SagaSmith Agent Python runtime not found: %AGENT_PYTHON%
    pause
    exit /b 5
)
if not exist "%UI_DIST%\index.html" (
    echo [ERROR] D^&D Workbench build not found: %UI_DIST%\index.html
    echo         Run install-all.bat before starting the agent.
    pause
    exit /b 6
)

"%MCP_PYTHON%" "%MCP_ROOT%\scripts\validate_agent_runtime.py" --config "config\config.json" --agent-root "%AGENT_ROOT%"
if errorlevel 1 (
    echo [ERROR] SagaSmith D^&D Skills and MCP configuration preflight failed.
    echo         Run: .venv\Scripts\python.exe scripts\configure_dnd_local.py --apply
    pause
    exit /b 9
)

if not exist "workspace\.sagasmith-dnd-mcp" mkdir "workspace\.sagasmith-dnd-mcp"
set "SAGASMITH_DND_MCP_HOME=%CD%\workspace\.sagasmith-dnd-mcp"
set "SAGASMITH_DND_SKILLS_DIR=%CD%\..\SagaSmith-dnd-skills"
set "SAGASMITH_MODULEGEN_SKILLS_DIR=%CD%\..\SagaSmith-module-gen-skills"
set "SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS=%CD%\..\reference\DnD-Books\5e\Books"
set "SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS=%CD%\..\reference\DnD-Books\5e\Campaign"
if not defined SAGASMITH_DND_MCP_RULE_OCR set "SAGASMITH_DND_MCP_RULE_OCR=1"
if not defined SAGASMITH_DND_MCP_RULE_OCR_SCALE set "SAGASMITH_DND_MCP_RULE_OCR_SCALE=2.0"
if not defined SAGASMITH_DND_MCP_MODULE_OCR set "SAGASMITH_DND_MCP_MODULE_OCR=1"
if not defined SAGASMITH_DND_MCP_MODULE_OCR_SCALE set "SAGASMITH_DND_MCP_MODULE_OCR_SCALE=2.0"
set "SAGASMITH_DND_MCP_TRANSPORT=streamable-http"
set "SAGASMITH_DND_MCP_HTTP_HOST=127.0.0.1"
set "SAGASMITH_DND_MCP_HTTP_PORT=8767"
set "SAGASMITH_DND_MCP_URL=http://127.0.0.1:%SAGASMITH_DND_MCP_HTTP_PORT%/mcp"
set "SAGASMITH_DND_GATEWAY_HOST=127.0.0.1"
set "SAGASMITH_DND_GATEWAY_PORT=8766"
set "SAGASMITH_DND_UI_DIST=%UI_DIST%"
for /f "delims=" %%U in ('powershell -NoProfile -Command "$c=Get-Content -LiteralPath 'config\config.json' -Raw -Encoding UTF8 ^| ConvertFrom-Json; $port=$c.channels.websocket.port; if(-not $port){$port=8765}; 'http://127.0.0.1:' + $port + '/'"') do set "SAGASMITH_AGENT_WEBUI_URL=%%U"
if not defined SAGASMITH_AGENT_WEBUI_URL set "SAGASMITH_AGENT_WEBUI_URL=http://127.0.0.1:8765/"
>"%RUNTIME_MARKER%" echo {"status":"starting"}

echo [START] Authoritative D^&D MCP: %SAGASMITH_DND_MCP_URL%
for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath '%MCP_EXE%' -WorkingDirectory '%MCP_ROOT%' -WindowStyle Hidden -PassThru; $p.Id"') do set "MCP_PID=%%P"
if not defined MCP_PID (
    echo [ERROR] Failed to start the D^&D MCP.
    if exist "%RUNTIME_MARKER%" del /q "%RUNTIME_MARKER%"
    pause
    exit /b 7
)
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(30); do { try { $client=[Net.Sockets.TcpClient]::new('127.0.0.1',%SAGASMITH_DND_MCP_HTTP_PORT%); $client.Dispose(); exit 0 } catch {}; Start-Sleep -Milliseconds 250 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo [ERROR] D^&D MCP did not become ready.
    powershell -NoProfile -Command "Stop-Process -Id %MCP_PID% -Force -ErrorAction SilentlyContinue"
    if exist "%RUNTIME_MARKER%" del /q "%RUNTIME_MARKER%"
    pause
    exit /b 8
)

echo [START] D^&D Workbench: http://127.0.0.1:%SAGASMITH_DND_GATEWAY_PORT%/
for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath '%MCP_PYTHON%' -ArgumentList @('-m','sagasmith_dnd_mcp.gateway') -WorkingDirectory '%MCP_ROOT%' -WindowStyle Hidden -PassThru; $p.Id"') do set "GATEWAY_PID=%%P"
if not defined GATEWAY_PID (
    echo [ERROR] Failed to start the D^&D Workbench gateway.
    goto :cleanup_error
)
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(20); do { try { $r=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:%SAGASMITH_DND_GATEWAY_PORT%/api/health' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 250 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo [ERROR] D^&D Workbench gateway did not become ready.
    goto :cleanup_error
)

echo [START] SagaSmith Agent: %SAGASMITH_AGENT_WEBUI_URL%
echo         D^&D native tools remain server-owned and refresh by tools/list_changed.
if exist "workspace\sagasmith-agent.stdout.log" del /q "workspace\sagasmith-agent.stdout.log"
if exist "workspace\sagasmith-agent.stderr.log" del /q "workspace\sagasmith-agent.stderr.log"
for /f %%P in ('powershell -NoProfile -Command "$p = Start-Process -FilePath '%AGENT_PYTHON%' -ArgumentList @('-m','nanobot','gateway','--foreground','--config','config\config.json') -WorkingDirectory '%AGENT_ROOT%' -RedirectStandardOutput '%AGENT_ROOT%\workspace\sagasmith-agent.stdout.log' -RedirectStandardError '%AGENT_ROOT%\workspace\sagasmith-agent.stderr.log' -WindowStyle Hidden -PassThru; $p.Id"') do set "AGENT_PID=%%P"
if not defined AGENT_PID (
    echo [ERROR] Failed to start SagaSmith Agent.
    goto :cleanup_error
)
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(30); do { try { $r=Invoke-WebRequest -UseBasicParsing '%SAGASMITH_AGENT_WEBUI_URL%' -TimeoutSec 1; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } } catch {}; Start-Sleep -Milliseconds 250 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo [ERROR] SagaSmith Agent WebUI did not become ready.
    goto :cleanup_error
)
>"%RUNTIME_MARKER%" echo {"agent_pid":%AGENT_PID%,"dnd_mcp_pid":%MCP_PID%,"dnd_gateway_pid":%GATEWAY_PID%}
echo         Runtime logs: workspace\sagasmith-agent.stdout.log and .stderr.log
powershell -NoProfile -Command "$p=Get-Process -Id %AGENT_PID% -ErrorAction SilentlyContinue; if ($null -eq $p) { exit 1 }; $p.WaitForExit(); exit $p.ExitCode"
set "EXIT_CODE=%ERRORLEVEL%"
goto :cleanup

:cleanup_error
set "EXIT_CODE=10"

:cleanup
if defined GATEWAY_PID powershell -NoProfile -Command "Stop-Process -Id %GATEWAY_PID% -Force -ErrorAction SilentlyContinue"
if defined MCP_PID powershell -NoProfile -Command "Stop-Process -Id %MCP_PID% -Force -ErrorAction SilentlyContinue"
if defined AGENT_PID powershell -NoProfile -Command "Stop-Process -Id %AGENT_PID% -Force -ErrorAction SilentlyContinue"
if exist "%RUNTIME_MARKER%" del /q "%RUNTIME_MARKER%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] SagaSmith local D^&D runtime exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
