# Install the full Windows workspace

This flow installs the current SagaSmith Agent, authoritative D&D/CoC runtimes, three Web UIs, Agent Skills, and the redistributable content catalog from sibling source repositories on Windows 11.

## Scope

`install-all.bat` uses committed lockfiles to sync the Agent with all extras and both MCP environments. The MCP editable sources install Core and the matching system runtimes. It then runs `npm ci` and builds the Agent, D&D, and CoC UIs; checks Full D&D, Full CoC, and Module Pack generation Skills; validates the committed public D&D catalog; and creates empty repo-local MCP homes when needed.

It does not clone or update repositories, overwrite `config/config.json`, read provider secrets, modify campaigns, import or activate Packs, or copy books and adventures that cannot be redistributed.

## Requirements and layout

Install `uv`, Python 3.11+, Node.js 22.12+, and npm. Keep these repositories as siblings:

```text
SagaSmith/
  SagaSmith-agent/
  sagasmith-core/
  sagasmith-dnd/
  sagasmith-coc/
  SagaSmith-dnd-mcp/
  SagaSmith-coc-mcp/
  SagaSmith-dnd-skills/
  SagaSmith-coc-skills/
  SagaSmith-module-gen-skills/
  SagaSmith-dnd-content-library/
  SagaSmith-dnd-ui/
  sagasmith-coc-ui/
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

Follow [Configure MCP tools](configure-mcp-tools.md) to add Full Skills and both MCP servers. Then run:

```powershell
.\install-all.bat --verify-only
.\start.bat
```

`start.bat` repeats the runtime preflight, starts the D&D UI Gateway and foreground Agent gateway, and cleans up the UI Gateway child when the Agent exits.

## What “all content” means

The source install covers current public software, Skills, and the redistributable catalog. The committed catalog currently contains two CC-BY SRD preset Packs. A complete private library is built locally from sources the user owns and cannot be distributed by the public installer.

Private Packs follow the current mechanical first pass → Agent evidence review and draft repair → immutable finalize lifecycle. Lobby content controls then handle import, dependency resolution, and campaign activation. A successful software installation does not silently place content in a campaign.

For the Chinese guide, see [Windows 全工作区安装](install-full-workspace-windows.zh-CN.md).
