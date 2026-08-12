# Install the local D&D system on Windows

This flow installs SagaSmith Agent, the authoritative D&D runtime, Agent and D&D Web UIs, D&D/ModuleGen Skills, and the redistributable D&D content catalog from sibling source repositories on Windows 11.

## Scope

`install-all.bat` uses committed lockfiles to sync the Agent and D&D MCP environments. The MCP editable sources install Core and the D&D system runtime. It then builds the Agent and D&D UIs, checks Full D&D and Module Pack generation Skills, validates the public D&D catalog, and creates the repo-local D&D data home when needed.

It does not clone or update repositories, overwrite `config/config.json`, read provider secrets, modify campaigns, import or activate Packs, or copy books and adventures that cannot be redistributed.

## Requirements and layout

Install `uv`, Python 3.11+, Node.js 22.12+, and npm. Keep these repositories as siblings:

```text
SagaSmith/
  SagaSmith-agent/
  sagasmith-core/
  sagasmith-dnd/
  SagaSmith-dnd-mcp/
  SagaSmith-dnd-skills/
  SagaSmith-module-gen-skills/
  SagaSmith-dnd-content-library/
  SagaSmith-dnd-ui/
```

Check the toolchain in PowerShell:

```powershell
uv --version
uv python find ">=3.11"
node --version
npm --version
```

## Install and verify

```powershell
cd C:\path\to\SagaSmith\SagaSmith-agent
.\install-all.bat
```

Software installation failures return a nonzero exit code. A missing or incomplete Agent config is reported as a next step rather than overwritten.

Stop any running Agent or MCP before installation because Windows locks active executables. `--verify-only` remains safe while services are running.

```powershell
# Audit without installing or building
.\install-all.bat --verify-only

# Backend-only install/audit; not a complete product installation
.\install-all.bat --skip-ui

.\install-all.bat --help
```

## Configure and start

Create the repo-local config after the software install:

```powershell
uv run nanobot onboard --wizard --config config\config.json --workspace workspace
```

Apply the local D&D connection without replacing provider secrets, then verify and start:

```powershell
.venv\Scripts\python.exe scripts\configure_dnd_local.py --apply
.\install-all.bat --verify-only
.\start.bat
```

`start.bat` repeats preflight, starts one authoritative streamable-HTTP D&D MCP, serves the built D&D Workbench through its gateway, starts the Agent, and cleans up both child processes when the Agent exits.

Run `stop.bat` from another terminal for an explicit stop. It reads only the exact process IDs
written by `start.bat`, stops the Agent, Workbench gateway, and MCP, then removes the runtime marker.

Use `scripts\local_dnd_data.py doctor`, `backup`, `verify`, and `restore --yes` while `start.bat` is stopped. A restore retains the displaced workspace in a timestamped `workspace.pre-restore-*` directory.

## What “all content” means

The source install covers current public software, Skills, and the redistributable catalog. The committed catalog currently contains two CC-BY SRD preset Packs. A complete private library is built locally from sources the user owns and cannot be distributed by the public installer.

Private Packs follow the current mechanical first pass → Agent evidence review and draft repair → immutable finalize lifecycle. Lobby content controls then handle import, dependency resolution, and campaign activation. A successful software installation does not silently place content in a campaign.

For the Chinese guide, see [Windows 全工作区安装](install-full-workspace-windows.zh-CN.md).
