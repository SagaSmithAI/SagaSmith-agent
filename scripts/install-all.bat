@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title SagaSmith Local D^&D Installer

set "SKIP_UI=0"
set "VERIFY_ONLY=0"

:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="--skip-ui" (
    set "SKIP_UI=1"
    shift
    goto :parse_args
)
if /I "%~1"=="--verify-only" (
    set "VERIFY_ONLY=1"
    shift
    goto :parse_args
)
if /I "%~1"=="--help" goto :help
if /I "%~1"=="-h" goto :help
echo [ERROR] Unknown option: %~1
echo         Run install-all.bat --help for supported options.
exit /b 2

:args_done
if defined SAGASMITH_INSTALL_AGENT_ROOT (
    cd /d "%SAGASMITH_INSTALL_AGENT_ROOT%"
) else (
    cd /d "%~dp0.."
)
set "AGENT_ROOT=%CD%"
for %%I in ("%AGENT_ROOT%\..") do set "SAGASMITH_ROOT=%%~fI"

echo [INFO] SagaSmith workspace: %SAGASMITH_ROOT%
echo [INFO] Agent repository:    %AGENT_ROOT%

for %%D in (
    "sagasmith-core"
    "sagasmith-dnd"
    "SagaSmith-dnd-mcp"
    "SagaSmith-dnd-skills"
    "SagaSmith-module-gen-skills"
    "SagaSmith-dnd-content-library"
) do (
    if not exist "%SAGASMITH_ROOT%\%%~D\" (
        echo [ERROR] Missing required sibling repository: %%~D
        echo         Expected directory: %SAGASMITH_ROOT%\%%~D
        goto :failed
    )
)
if "%SKIP_UI%"=="0" (
    if not exist "%AGENT_ROOT%\webui\" (
        echo [ERROR] Missing SagaSmith Agent WebUI: %AGENT_ROOT%\webui
        goto :failed
    )
    for %%D in ("SagaSmith-dnd-ui") do (
        if not exist "%SAGASMITH_ROOT%\%%~D\" (
            echo [ERROR] Missing required sibling repository: %%~D
            echo         Expected directory: %SAGASMITH_ROOT%\%%~D
            goto :failed
        )
    )
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv was not found on PATH. Install uv, reopen the terminal, and retry.
    goto :failed
)
uv python find ">=3.11" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11 or newer was not found by uv.
    echo         Install it with: uv python install 3.12
    goto :failed
)

if "%SKIP_UI%"=="0" (
    where node >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Node.js was not found on PATH. Install Node.js 22.12 or newer.
        goto :failed
    )
    node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit(Math.max(0,22012-a*1000-b))" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Node.js 22.12 or newer is required.
        node --version
        goto :failed
    )
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] npm was not found on PATH. Install it with Node.js and retry.
        goto :failed
    )
)

if "%VERIFY_ONLY%"=="1" goto :verify

for %%P in ("sagasmith-dnd-mcp.exe" "nanobot.exe") do (
    tasklist /FI "IMAGENAME eq %%~P" /NH | %SystemRoot%\System32\find.exe /I "%%~P" >nul
    if not errorlevel 1 (
        echo [ERROR] %%~P is running and its Windows executable is locked.
        echo         Stop start.bat or the active runtime task, then rerun the installer.
        echo         Use --verify-only when you only need a non-mutating audit.
        goto :failed
    )
)

echo [INSTALL] D^&D MCP + Core + D^&D runtime
pushd "%SAGASMITH_ROOT%\SagaSmith-dnd-mcp" >nul
uv sync --all-extras --frozen
if errorlevel 1 (
    popd >nul
    goto :failed
)
popd >nul

echo [INSTALL] SagaSmith Agent and all channel extras
pushd "%AGENT_ROOT%" >nul
uv sync --all-extras --frozen
if errorlevel 1 (
    popd >nul
    goto :failed
)
popd >nul

if "%SKIP_UI%"=="0" (
    echo [INSTALL] SagaSmith Agent WebUI
    pushd "%AGENT_ROOT%\webui" >nul
    call npm ci
    if errorlevel 1 (
        popd >nul
        goto :failed
    )
    call npm run build
    if errorlevel 1 (
        popd >nul
        goto :failed
    )
    popd >nul

    echo [INSTALL] SagaSmith D^&D UI
    pushd "%SAGASMITH_ROOT%\SagaSmith-dnd-ui" >nul
    call npm ci
    if errorlevel 1 (
        popd >nul
        goto :failed
    )
    call npm run build
    if errorlevel 1 (
        popd >nul
        goto :failed
    )
    popd >nul

)

if not exist "%AGENT_ROOT%\workspace\.sagasmith-dnd-mcp" mkdir "%AGENT_ROOT%\workspace\.sagasmith-dnd-mcp"

:verify
echo [INFO] Verifying installed runtimes and content contracts...
for %%F in (
    "%SAGASMITH_ROOT%\SagaSmith-dnd-mcp\.venv\Scripts\sagasmith-dnd-mcp.exe"
    "%AGENT_ROOT%\.venv\Scripts\nanobot.exe"
) do (
    if not exist "%%~F" (
        echo [ERROR] Missing installed executable: %%~F
        goto :failed
    )
)

set "DND_PYTHON=%SAGASMITH_ROOT%\SagaSmith-dnd-mcp\.venv\Scripts\python.exe"
"%DND_PYTHON%" -c "import sagasmith_core, sagasmith_dnd, sagasmith_dnd_mcp"
if errorlevel 1 (
    echo [ERROR] D^&D runtime import verification failed.
    goto :failed
)
for %%F in (
    "%SAGASMITH_ROOT%\SagaSmith-dnd-skills\full\SKILL.md"
    "%SAGASMITH_ROOT%\SagaSmith-module-gen-skills\SKILL.md"
) do (
    if not exist "%%~F" (
        echo [ERROR] Missing required Skill contract: %%~F
        goto :failed
    )
)
if not exist "%SAGASMITH_ROOT%\SagaSmith-dnd-skills\full\skills\" (
    echo [ERROR] Missing Full D^&D Skill library.
    goto :failed
)

"%DND_PYTHON%" "%SAGASMITH_ROOT%\SagaSmith-dnd-content-library\scripts\validate_catalog.py"
if errorlevel 1 (
    echo [ERROR] Public D^&D content catalog validation failed.
    goto :failed
)

if "%SKIP_UI%"=="0" (
    for %%F in (
        "%AGENT_ROOT%\nanobot\web\dist\index.html"
        "%SAGASMITH_ROOT%\SagaSmith-dnd-ui\dist\index.html"
    ) do (
        if not exist "%%~F" (
            echo [ERROR] Missing Web UI build artifact: %%~F
            goto :failed
        )
    )
)

if exist "%AGENT_ROOT%\config\config.json" (
    "%DND_PYTHON%" "%SAGASMITH_ROOT%\SagaSmith-dnd-mcp\scripts\validate_agent_runtime.py" --config "%AGENT_ROOT%\config\config.json" --agent-root "%AGENT_ROOT%"
    if errorlevel 1 (
        echo [NEXT] Software installation passed, but Agent config, Skills, or D^&D MCP preflight failed.
        echo        Update config\config.json using docs\guides\configure-mcp-tools.md.
        set "CONFIG_READY=0"
    ) else (
        echo [OK] Existing repo-local Agent configuration passed preflight.
        set "CONFIG_READY=1"
    )
) else (
    set "CONFIG_READY=0"
    echo [NEXT] No config\config.json was changed or created.
    echo        Run the repo-local onboard wizard, then configure the D^&D MCP as documented:
    echo        uv run nanobot onboard --wizard --config config\config.json --workspace workspace
    echo        docs\guides\configure-mcp-tools.md
)

echo [OK] SagaSmith local D^&D installation is valid.
echo      Agent, D^&D MCP, UIs, Skills, and the redistributable public catalog are ready.
echo      Private or commercial Packs were not copied, imported, or activated.
if "%CONFIG_READY%"=="1" echo [NEXT] Start with: start.bat
exit /b 0

:failed
echo [ERROR] SagaSmith local D^&D installation did not complete.
exit /b 1

:help
echo Usage: install-all.bat [--verify-only] [--skip-ui]
echo.
echo Installs the D^&D-first local SagaSmith system on Windows:
echo   - Agent plus all Python extras
echo   - D^&D MCP plus editable Core/system runtimes
echo   - Agent and D^&D Web UIs
echo   - D^&D/ModuleGen Skills contract checks
echo   - redistributable public D^&D content catalog validation
echo.
echo Options:
echo   --verify-only  Do not install or build; validate the current workspace.
echo   --skip-ui      Skip Node.js checks, UI builds, and UI artifact checks.
echo.
echo The installer never overwrites config\config.json and never imports or
echo activates private/commercial content Packs in a campaign.
exit /b 0
