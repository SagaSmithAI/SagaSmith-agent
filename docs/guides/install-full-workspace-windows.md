# Install and operate the local SagaSmith stack

SagaSmith Agent is the user-facing entry point. D&D, Call of Cthulhu, and
Narrative are independent optional modes; selecting one never installs or
configures either of the others.

The implementation is a Python CLI. There is no BAT installer or parallel
start/stop protocol.

## Requirements

- Python 3.11 or newer and `uv`
- Node.js 22.12 or newer with npm when building Web UIs
- sibling source repositories for `--source workspace`
- a configured Agent provider before starting the gateway

Create the initial Agent config if it does not exist:

```powershell
uv run nanobot onboard --wizard --config config\config.json --workspace workspace
```

## Choose modes

Repeat `--mode` to install any combination. Omitting it installs all three.

```powershell
# D&D only
uv run nanobot sagasmith install --mode dnd

# CoC and Narrative
uv run nanobot sagasmith install --mode coc --mode narrative

# D&D, CoC, and Narrative
uv run nanobot sagasmith install
```

Use `--skip-ui` for a backend-only development install and `--verify-only` for
a non-installing audit. `--source release` uses the bundled
`sagasmith-stack-lock.json` to select one immutable commit per component.
`--release-manifest <path>` accepts another audited lock; use
`--release-ref <coordinated-tag>` only when the same tag exists in every selected
repository.

The installer reconciles only SagaSmith-owned MCP and Skill entries. It backs
up an existing config before changing it and preserves providers, secrets,
channels, unrelated MCP servers, and unrelated Skill roots.

## Lifecycle

```powershell
uv run nanobot sagasmith doctor
uv run nanobot sagasmith start
uv run nanobot sagasmith status
uv run nanobot sagasmith logs
uv run nanobot sagasmith stop
```

The exact child process IDs and commands are recorded under
`workspace/.sagasmith-local`. Stop targets only those recorded processes.

Default local endpoints are:

| Surface | Address |
|---|---|
| Agent WebUI | `http://127.0.0.1:8765/` |
| D&D Workbench gateway | `http://127.0.0.1:8766/` |
| D&D MCP | `http://127.0.0.1:8767/mcp` |
| CoC Workbench gateway | `http://127.0.0.1:8768/` |
| CoC MCP | `http://127.0.0.1:8769/mcp` |
| Narrative MCP | Agent-owned session-scoped stdio |

Each logical Agent session gets its own SagaSmith MCP connection and mutable
native tool registry. `tools/list_changed` therefore changes only that chat's
authoritative schema.

## Backup, restore, and upgrade

Stop the stack before backup, restore, upgrade, or rollback.

```powershell
uv run nanobot sagasmith backup C:\backups\sagasmith.zip
uv run nanobot sagasmith verify-backup C:\backups\sagasmith.zip
uv run nanobot sagasmith restore C:\backups\sagasmith.zip --yes
uv run nanobot sagasmith upgrade
uv run nanobot sagasmith rollback --yes
```

Backups contain the three separate domain state roots and the stack manifest.
They exclude provider secrets, logs, source books, and source checkouts.
Upgrade refuses dirty repositories, creates a backup first, fast-forwards only,
reinstalls, and runs verification. Rollback also refuses dirty repositories.

Uninstall removes managed config while preserving data by default:

```powershell
uv run nanobot sagasmith uninstall --yes
uv run nanobot sagasmith uninstall --yes --purge-data
```

## Content boundary

Software installation never imports or activates a Pack. Private books and
adventures remain local and follow mechanical draft, Agent evidence review,
immutable finalization, Lobby import, and explicit activation.
