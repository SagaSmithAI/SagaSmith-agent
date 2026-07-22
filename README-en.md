# SagaSmith Agent

[中文](README.md) · [English](README-en.md) · [Platform overview](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md)

<p align="center"><img src="images/Sagasmith.png" alt="SagaSmith Agent" width="168"></p>

**The identity, session, and multi-channel Agent Host for SagaSmithAI.** Built on [NanoBot](https://github.com/HKUDS/nanobot), it connects models, chat channels, workspace identity, memory, and MCP services. Domain rules, books, modules, and campaign databases belong to independent MCP servers.

> The Agent is the GM at the table, not a second backend that secretly copies every rules engine.

## Platform responsibility

```mermaid
flowchart LR
    U[QQ · Discord · Telegram · WebUI · API] --> A[SagaSmith Agent]
    A --> I[SOUL · IDENTITY · session memory]
    A --> M[MCP clients]
    M --> D[D&D MCP<br/>lobby · play · combat]
    M --> X[Other domain MCPs]
```

SagaSmith Agent owns trusted channel identity, the multi-turn agent loop, workspace/persona, session memory and compaction, model/provider adapters, channel integrations, scheduling, long tasks, subagents, built-in tools, WebUI, and an optional OpenAI-compatible API.

It does **not** write D&D/CoC databases directly, reimplement rules/combat/module parsing, infer permission from display names, or bypass a matching MCP workflow with a CLI or temporary script.

## D&D: the MCP-first path

[SagaSmith D&D MCP](https://github.com/SagaSmithAI/SagaSmith-dnd-mcp) owns campaigns, rules, modules, characters, knowledge, branches, snapshots, and combat. The Agent keeps only 12 exposure/diagnostic tools enabled. Every chat session opens its own server-side exposure and loads a narrow `lobby`, `play`, or `combat` group.

```text
inbound message
→ Host injects principal
→ exposure_open
→ search / inspect / load
→ exposure_call (NanoBot static-schema fallback)
→ MCP validates phase / campaign / role / actor / revision
→ result returns to session and channel
```

One MCP process can therefore maintain different tool surfaces for different channels, users, and campaigns, without trusting model-supplied authority.

## Start the full Windows workspace

[`start.bat`](start.bat) is the single Agent + D&D MCP + D&D UI Gateway entry point. NanoBot starts the stdio MCP as a child process; the script also starts a principal-aware HTTP/SSE adapter on `127.0.0.1:8766`.

Expected sibling layout:

```text
SagaSmith/
  SagaSmith-agent/
  SagaSmith-dnd-mcp/
  SagaSmith-dnd-skills/
  SagaSmith-module-gen-skills/
  reference/DnD-Books/
```

```powershell
cd SagaSmith-dnd-mcp
uv sync --all-extras

cd ..\SagaSmith-coc-mcp
uv sync --all-extras

cd ..\SagaSmith-agent
uv sync --all-extras
# configure provider, model, channels, and both SagaSmith MCP servers
.\start.bat
```

The script checks `uv`, the local config, Full D&D Skills exposure, the D&D core-tool allowlist, the PDF timeout, and both sibling MCP executables. It prepares their workspace homes, waits for the D&D UI Gateway health check, and then starts the foreground Agent gateway. It stops the UI adapter when the Agent exits. See [the MCP guide](docs/guides/configure-mcp-tools.md). Keep machine paths and secrets out of Git; reference provider keys through environment variables.

The UI connects to `http://127.0.0.1:8766` by default. Non-loopback access requires an explicit bearer token and origin allowlist; without a token, the adapter rejects remote requests.

## Generic quick start

Requires Python 3.11+:

```bash
uv sync
uv run nanobot onboard --wizard
uv run nanobot status
uv run nanobot agent -m "Hello"
```

Or inside a controlled virtual environment:

```bash
python -m pip install -e .
nanobot onboard --wizard
nanobot gateway
```

## MCP rules

- Use stdio for trusted local servers. HTTP/SSE is protected by the shared SSRF guard; private targets require narrow allowlisting.
- `enabledTools` is the Host's outer allowlist. Domain phase, role, and exposure should narrow access again on the server.
- `injectPrincipal` authenticates the caller field; grant targets remain model-visible and separate.
- Domain MCPs own persistence and domain Skills. The Agent workspace owns persona, sessions, and cross-domain orchestration.

## Memory layers

| Layer | Owner | Purpose |
|---|---|---|
| Session history | Agent | recent chat continuity |
| Dream/compaction | Agent | compressed long-conversation context |
| Campaign snapshots/branches | Domain runtime | authoritative restorable world timelines |
| Campaign memory | Domain MCP/Core | branch-aware facts across sessions |
| Actor knowledge | Domain MCP/Core | independent PC/NPC knowledge and visibility |

Agent summaries never replace authoritative campaign state and must not leak GM-only facts into player-visible sessions.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check nanobot tests

cd webui
bun install
bun run build
bun run test
```

Docs: [Quick Start](docs/quick-start.md) · [Configuration](docs/configuration.md) · [Architecture](docs/architecture.md) · [MCP](docs/guides/configure-mcp-tools.md) · [Security](SECURITY.md)

## Status and license

Active Alpha. SagaSmith-specific work and NanoBot upstream retain their respective attribution and notices. The repository is MIT licensed; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
