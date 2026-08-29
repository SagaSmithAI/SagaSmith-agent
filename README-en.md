# SagaSmith Agent

[中文](README.md) · [English](README-en.md) · [Website](https://sagasmithai.github.io) · [Platform overview](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md) · [SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web) · [Content catalog](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

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

## Choose the runtime first

| Goal | Entrypoint | State and authority boundary | Start here |
|---|---|---|---|
| A complete GM Agent on your computer | `sagasmith-agent-local` / `nanobot` | The local user owns config, workspace, Channels, and selected MCPs | [Local Kit install](#install-and-start-the-full-windows-workspace) |
| A SagaSmith Web room | `sagasmith-agent-worker` | Web is the Host/supervisor; the worker accepts one trusted turn envelope and one player message | [Hosted Worker contract](#hosted-worker-request-and-result-contract) |
| Codex, Claude Code, or another local Host | `sagasmith-auth-bridge` | One trusted binding per requester/conversation; the bridge re-signs for the target MCP | [Host adapters](docs/sagasmith-host-adapters.md) |
| Generic NanoBot features | `nanobot` | Use this repository's providers, channels, tools, WebUI, and API configuration | [Documentation map](docs/README.md) |

Local Kit and Hosted Worker share the Agent loop and MCP handlers, but they are not the same
security surface. Local lets its owner opt into local capabilities. The Hosted distribution is
audited after build and excludes Channels, WebUI, the local installer, and shell/filesystem/web/
cron/subagent tools.

## Local and Hosted distributions

- `sagasmith-agent-local` / `Dockerfile` is the complete user-operated Agent with Channels,
  WebUI, local stack management, and explicitly configured local tools.
- `sagasmith-agent-worker` / `Dockerfile.hosted` is the per-session Web worker. Its config must
  set `tools.distribution="hosted"`; it loads only the selected session MCP tools and the
  structured-response/activity tools injected by Web.

The Hosted image is non-root and contains neither channel SDKs nor a second copy of Agent core.
CI builds and audits the Local and Hosted artifacts separately.

## MCP 2026-07-28 and the Hosted boundary

The bundled release lock requires Python SDK v2 and MCP 2026-07-28. Generic MCP configuration
can still use `protocolMode: "auto"` to fall back to legacy initialize, and `"legacy"` remains an
explicit operational rollback. The modern path sends no `Mcp-Session-Id`. Every call carries a short-lived
`sagasmith.auth-context/v2` delegation bound to the target MCP, workload, requester, resource
owner, acting Host/character, audience, concrete operations, `room_turn_id`, and `base_revision`.
Hosted Worker requires a dedicated `SAGASMITH_WORKER_SERVICE_TOKEN`, keeps trusted context
structurally separate from player text, and connects only the MCP for the current `system_id`.
Standard MCP text/image/audio/resource/embedded-resource results are retained while `host_media`
envelopes feed the Web artifact pipeline. Its trusted supervisor also supplies a stable, unique
`--workspace-id`; the worker binds an opaque owner to that ID and the canonical workspace path so
retries and restarts safely reuse persisted state without relying on an ephemeral port.

### Hosted Worker request and result contract

Web sends exactly one `role=user` text message to `POST /v1/chat/completions`. Authority fields
must remain in the separate `trusted_context` object. Pydantic rejects extra fields, wildcards,
duplicate operations, expired delegations, and delegations longer than 15 minutes. The required
shape is represented by this credential-free example:

```json
{
  "session_id": "service-session-id",
  "messages": [{"role": "user", "content": "player text"}],
  "trusted_context": {
    "caller_principal": "workload:sagasmith-web",
    "workload_identity": "sagasmith-agent-hosted-worker",
    "requester_principal": "user:requester",
    "resource_owner_principal": "user:campaign-owner",
    "acting_host_principal": "campaign:gm",
    "acting_character_id": "character-id-or-empty",
    "authorized_audience": "player",
    "allowed_operations": ["campaign_query", "resolution"],
    "room_turn_id": "durable-room-turn-id",
    "campaign_id": "campaign-id",
    "system_id": "dnd5e",
    "base_revision": 42,
    "expires_at": "replace-with-now-plus-at-most-15-minutes",
    "idempotency_key": "stable-business-operation-key",
    "conversation_principal": "room:conversation",
    "tenant_id": "tenant-or-empty",
    "traceparent": "",
    "tracestate": "",
    "baggage": ""
  }
}
```

Browser tokens, Web callback tokens, and credentials for another audience are never passed to a
domain MCP. Agent issues a fresh delegation for the target service, exact operation, and remaining
hard expiry. The response retains its OpenAI-compatible shell and adds bounded `tool_receipts`,
`structured_output`, original standard `mcp_results`, and Host-only `host_media`.
`mcp_results[].result` preserves MCP `CallToolResult` text, image, audio, resource, embedded
resource, `structuredContent`, and `isError` semantics. Web converts `host_media` into artifact or
object-store IDs; it does not replace MCP results with a private wire protocol.

## D&D: the MCP-first path

The D&D MCP in [sagasmith-dnd](https://github.com/SagaSmithAI/sagasmith-dnd) owns campaigns, rules, modules, characters, knowledge, branches, snapshots, and combat. Its modern catalogue is deterministic for the same authorization and carries private cache hints. The Agent selects a bounded facade subset for the current system, phase, and task; each call still revalidates identity, role, phase, revision, and allowed operation at the MCP.

```text
inbound message
→ Host injects principal
→ skill_query(read/search/section) for bounded Skill sections
→ read the stable, bounded MCP catalogue
→ select exact facade tool IDs for this system / phase / task
→ pass explicit campaign / revision / server-issued guidance handle
→ call the selected native MCP tool directly
→ MCP validates phase / campaign / role / actor / revision
→ a first or changed host_context_binding causes an in-turn hard barrier
→ isolated_evaluate / portray_npc returns a proposal from a fresh zero-tool context
→ result returns to session and channel
```

Modern catalogue contents never change as a side effect of another request on the same connection. Authorization can still produce a private deterministic catalogue, while legacy `tools/list_changed` remains only for compatibility and real catalogue changes. Neither catalogue selection nor an opaque handle grants authority.

### Keeping the model tool list small

The Hosted path uses three filters so the model never receives all low-level tools from all three
domains:

1. Trusted `system_id` connects only the MCP whose `systemIds` match the current campaign.
2. The MCP exposes a stable, deterministically sorted, cacheable catalogue for the same
   authorization; in-connection exposure side effects do not mutate `tools/list`.
3. Web derives concrete `allowed_operations` from system, phase, caller permissions, and the task.
   Agent verifies that every ID exists, then projects only that facade/workflow subset into the
   turn's model registry. Hosted requests are rejected when this projection exceeds 16 operations,
   so an authorization mistake cannot flood the model context with a low-hit-rate catalogue.

`enabledTools` is a deployment allowlist and `allowed_operations` is a per-turn projection. Neither
replaces the domain MCP's per-call role, phase, campaign, revision, and idempotency checks. Tools
omitted from a model turn stay in the stable underlying catalogue, which refreshes for a real
authorization/catalogue change rather than every combat write.

### MCP Tasks are only for genuinely long tools

Agent enters SEP-2663 claim/poll/update/cancel only when modern `server/discover` negotiated
`io.modelcontextprotocol/tasks` and a tool call returned `resultType: "task"`. Every `tasks/get`,
`tasks/update`, and `tasks/cancel` uses a newly signed single-operation delegation and
`Mcp-Name: <taskId>`; the opaque task ID is a name, not a capability. A terminal task is restored to
the original tool's standard `CallToolResult`. Ordinary tools remain synchronous under
`toolTimeout`; only real import/OCR/compile/high-resolution-render work uses `taskTimeout`.

When campaign, principal, role, audience, branch, or restore state changes, the
Agent stops later calls from the same model response and rebuilds without old
messages, summaries, workspace/Dream memory, cached retrieval, or receipts.
`isolated_evaluate` uses fixed schemas for actor, audience, faction, source, and
DM-ruling proposals and can evaluate independent signed bundles concurrently;
`portray_npc` retains the richer named-NPC dialogue contract. Both run without
tools or child-session persistence and neither can author authoritative state.
NPC bundle v2 carries an MCP-owned structured conversation and a fixed
host-neutral delegation contract, never the Agent channel transcript.

### Shareable content remains MCP-owned

The Agent does not parse, rewrite, or cache a second creature catalog. Unified
PC/NPC/monster actor cards, bundled SRD preset packs, and module packages with a
Scene Atlas, assets, reviewed content, and pregenerated actors are imported and
exported through D&D MCP Lobby exposures. Imports return fresh actor ids. The
Agent must discard source database identity and must not copy old session,
workspace, or ActorKnowledge context into the new actor. A chat attachment may
transport a package, but only MCP validation, allowlisted file access, and public
transactions can place it in a campaign.

## Install and start the full Windows workspace

Requirements are Windows 11, [uv](https://docs.astral.sh/uv/), Python 3.11+, and Node.js 22.12+ with npm. Keep the current SagaSmith repositories as siblings; see the [full Windows workspace guide](docs/guides/install-full-workspace-windows.md) for the complete layout, component boundaries, and troubleshooting.

```text
SagaSmith/
  SagaSmith-agent/              # Agent, channels, and WebUI
  sagasmith-core/               # neutral persistence and Pack infrastructure
  sagasmith-dnd/                # D&D Domain, MCP, Skills, UI, and module generator
  sagasmith-coc/                # CoC Domain, MCP, Skills, UI, and module generator
  sagasmith-narrative/          # Narrative Domain, MCP, Skills, and project generator
  SagaSmith-dnd-content-library/# public catalog with per-Pack rights (optional)
```

The three domain repositories are the only current source entry points for
Domain, MCP, Skills, UI where present, and authoring workflows. The former
standalone MCP, Skills, UI, and generic Module Generator repositories are
archived; the installer neither reads them nor treats them as fallbacks.

The bundled `sagasmith.release-lock/v3` pins Core and all three active domain repositories to
audited immutable commits. It also records MCP 2026-07-28, auth-context v2, and the shared
authority contract as required compatibility metadata. Unknown components are rejected, so an
archived split repository cannot silently become a release input.
The manifest is an unpublished compatibility lock, not a release announcement or tag.

Install a named release profile from the Agent repository. `--mode` remains
available for custom combinations; omitting both selects `multi-system`:

```powershell
cd SagaSmith-agent
uv run nanobot sagasmith install --profile dnd-only
uv run nanobot sagasmith install --profile coc-only
uv run nanobot sagasmith install --profile narrative-only
uv run nanobot sagasmith install --profile multi-system
uv run nanobot sagasmith install --mode coc --mode narrative
uv run nanobot sagasmith install
```

`sagasmith-local-kit.json` fixes the profiles, components, ports, templates, and
shared `sagasmith.authoritative-mcp/v2` contract. Every profile supports
`--transport stdio`, `--transport streamable-http`, or the backward-compatible
`mixed` default. Both transports use the same handlers, schemas, errors,
revisions, idempotency, and authority semantics; HTTP remains loopback-only.

The Python installer keeps D&D, CoC, and Narrative optional and independently
versioned. It reconciles only SagaSmith-owned config fields, builds only
selected UIs, never imports or activates a Pack, and does not depend on
SagaSmith Web, PostgreSQL, Redis, object storage, accounts, quota, or Forge.

Create the repo-local Agent configuration after installation:

```powershell
uv run nanobot onboard --wizard --config config\config.json --workspace workspace
```

Then follow the [MCP configuration guide](docs/guides/configure-mcp-tools.md). Re-audit an existing installation with:

```powershell
uv run nanobot sagasmith install --verify-only
uv run nanobot sagasmith doctor --json
```

Doctor reports MCP/config, domain databases, Skills, provider readiness, and
the selected transport separately. Credential-free Discord, QQ, Telegram,
Codex, Claude Code, OpenClaw, and generic Agent templates live in
[`examples/local-agent-kit`](examples/local-agent-kit/README.md).

The local performance baseline does not call an LLM or open existing campaign
data. It starts one loopback MCP at a time in a temporary directory and records
cold start, warm `server_capabilities` calls in one session, and idle RSS:

```powershell
uv run nanobot sagasmith benchmark --profile dnd-only --iterations 5 --json
```

The friendly D&D and CoC embedding-cache paths map to Core's
`DND5E_EMBEDDING_CACHE_DIR` and `COC7_EMBEDDING_CACHE_DIR`. Narrative's template
entry is reserved only: the current Narrative runtime does not use the Core
embedder and must not be treated as cache-enabled.

The installer syncs only the Agent base and selected MCP packages; D&D/CoC add
the `gateway` extra only for HTTP profiles. It does not install every Agent
channel extra. Select Bot channels after installation, for example with
`uv sync --extra discord`, `--extra qq`, or `--extra telegram`. Domain MCP
document/OCR requirements still follow each package's current contract.

After configuration passes, start the selected services and Agent:

```powershell
uv run nanobot sagasmith start
```

The committed public catalog contains only redistributable SRD Packs. A complete private library must be built locally from books the user owns through the current draft → Agent review → finalize flow. The installer does not copy commercial sources, generate private Packs, or choose campaign activation; import and activation remain Lobby content-control operations.

D&D and CoC Workbenches use ports 8766 and 8768. Non-loopback access requires an explicit bearer token and origin allowlist. Keep machine paths and secrets out of Git and reference provider keys through environment variables.

## Hosted workspace lifecycle

The trusted supervisor must pass `--workspace`, a stable unique `--workspace-id`, and the config
path to `sagasmith-agent-worker`. Defaults are a 86,400-second TTL, 1 GiB per workspace, and 128
registered workspaces under one root. Tighten them with `--workspace-ttl-seconds`,
`--workspace-max-bytes`, and `--workspace-max-count`.

The worker writes a `sagasmith.hosted-workspace/v1` marker and hashes the canonical path plus the
Host-managed ID into a stable opaque owner. A root-scoped cross-process lock makes active-workspace
admission, marker updates, and cleanup mutually exclusive. A new or reactivated workspace fails
closed when `--workspace-max-count` active markers already exist, while a retry or process restart
with the same owner and canonical path reuses its existing slot. A request with `terminal=true`
marks the workspace terminated before it becomes eligible for TTL/LRU cleanup. Cleanup deletes only
a child whose marker schema and recorded path match and whose status is `terminated`; unknown
directories, active workspaces, symlinks, and damaged or mismatched markers are preserved. Never
reuse one ID for another workspace or derive it from player/model text.

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
- Finalized unified Packs are domain content, not Host session memory or permission carriers.

## Memory layers

| Layer | Owner | Purpose |
|---|---|---|
| Session history | Agent | recent chat continuity |
| Dream/compaction | Agent | compressed long-conversation context |
| Campaign snapshots/branches | Domain runtime | authoritative restorable world timelines |
| Campaign memory | Domain MCP/Core | branch-aware facts across sessions |
| Actor knowledge | Domain MCP/Core | independent PC/NPC knowledge and visibility |

In domain-authoritative campaign context, workspace/Dream memory is excluded
from the model prompt and campaign messages are classified `campaign_private`
inside their session. Agent summaries never replace authoritative campaign
state and must not leak GM-only facts into player-visible sessions.

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

### Focused verification

README, Hosted/MCP configuration, or release-lock changes should run at least:

```bash
uv run ruff check nanobot tests
uv run pytest -q tests/apps/test_hosted_worker.py tests/tools/test_mcp_v2_contract.py \
  tests/tools/test_mcp_tasks.py tests/test_sagasmith_local_stack.py
uv run pytest -q tests/host_conformance
```

CI runs real-domain `release-lock` and `latest-main` lanes. Local tests must not use production
campaign data, real user text, or paid models. Run `python -m nanobot.apps.hosted_audit` only in a
clean Hosted image containing `.[hosted]`; it fails by design in a Local/dev environment that has
Channel extras installed.

## Deploy, upgrade, and roll back

1. Read [`sagasmith-stack-lock.json`](sagasmith-stack-lock.json), verify
   `sagasmith.release-lock/v3` and the expected `release_status`, and deploy Core plus the three
   domains from those immutable commits. Do not assemble a production stack from archived repos
   or floating `main` branches.
2. Run `nanobot sagasmith install --verify-only` and `nanobot sagasmith doctor --json`. Roll domain
   MCPs first, Agent Worker second, and Web last. Retain the previous images and lock until the new
   transport/identity/media/Tasks smoke passes.
3. A generic MCP can temporarily use `protocolMode: "legacy"` for a protocol incident. For the
   coordinated stack, prefer rolling the whole image/lock set back. Legacy is a compatibility
   adapter, not an implicit-session authority model, and archived repos remain forbidden.
4. If the Hosted request contract is incompatible, roll Agent back before changing the Web pin.
   Do not change a live `workspace-id` owner or replace marker/TTL/LRU handling with directory
   clearing.

Hosted Worker exposes `/health` and `GET /metrics/mcp`. The latter returns
`sagasmith.host-mcp-metrics/v1` counters using only transport, protocol era, phase, outcome, and
fixed catalogue-size buckets. Trusted `traceparent`, `tracestate`, and `baggage` propagate to MCP,
but users, campaigns, runs, tool names, and arguments never become metric labels. Optional
Langfuse model tracing complements rather than replaces these low-cardinality runtime metrics.

Review [`SECURITY.md`](SECURITY.md),
[`docs/sagasmith-host-adapters.md`](docs/sagasmith-host-adapters.md), and
[`docs/deployment.md`](docs/deployment.md) before production rollout. Never commit
`SAGASMITH_WORKER_SERVICE_TOKEN`, MCP signing secrets, provider keys, trusted-context files, or
filled Local Kit templates.

Docs: [Quick Start](docs/quick-start.md) · [Configuration](docs/configuration.md) · [Architecture](docs/architecture.md) · [MCP](docs/guides/configure-mcp-tools.md) · [Security](SECURITY.md)

## Status and license

Active Alpha. SagaSmith-specific work is licensed under Apache-2.0. NanoBot upstream and other third-party components retain their respective licenses, attribution, and notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
