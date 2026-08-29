# SagaSmith external Host Auth Adapters

## MCP 2026-07-28 compatibility

Agent uses Python SDK v2 and `protocolMode: "auto"`: it probes `server/discover`
and falls back to legacy `initialize` only when the peer does not implement the modern method.
`protocolMode: "legacy"` is an explicit rollback switch. The modern path never sends or trusts
`Mcp-Session-Id`; identity, campaign, turn, revision, expiry and allowed operations are supplied
again on every tool call. `clientInfo` is diagnostics, never an authorization identity.

The bundled `sagasmith.release-lock/v3` is stricter than a generic Agent configuration: its
compatibility metadata requires MCP 2026-07-28 and auth-context v2. Legacy remains a tested
rollback adapter, but it is not an authority model for a coordinated locked deployment.
Its `release_status` is `compatibility-lock-not-published`; creating or merging the lock does not
publish a package, image, tag, or GitHub release.

| Boundary | Legacy | 2026-07-28 |
|---|---|---|
| stdio / Streamable HTTP | initialize, compatibility notifications | discover or explicit version |
| authority | `sagasmith.auth-context/v1` adapter | `sagasmith.auth-context/v2` per-request delegation |
| catalogue | `tools/list_changed` may refresh a legacy session | deterministic sorted list, private cache scope |
| cross-call state | legacy server compatibility only | explicit campaign/revision/opaque server handle |

The v2 envelope is compatible with SagaSmith Core commit
`0ac316655687757203a9df1b2eb81669ec1d2d78`. It binds the target MCP service, caller workload,
requester, resource owner, acting Host/character, audience, concrete operation allowlist,
`room_turn_id`, `base_revision`, and a hard expiry. Browser or upstream bearer tokens are never
forwarded to an MCP; the Agent signs a separate target-specific delegation.

The coordinated modern lock contains only the current active repositories:

| Component | Immutable commit |
|---|---|
| SagaSmith Core | `0ac316655687757203a9df1b2eb81669ec1d2d78` |
| D&D | `dd31d809cf406d9af99eb6c3fea176c33e16fee2` |
| CoC | `492be2073a2b9bb3191111c470514e63c088d59f` |
| Narrative | `c15fb2400407986b457d5406b76f9c3dae5b5358` |

The lock parser rejects unknown component keys and an invalid component/profile layout. Archived
standalone MCP, Skill, UI, and module-generator repositories cannot be release inputs or fallbacks.

Standard MCP transport is not an authorization boundary. Every external Host must turn trusted
Host facts into one immutable `TrustedHostContext`, then launch one `sagasmith-auth-bridge` process
for that requester/conversation binding. The model never receives the signing secret or controls
the identity files.

## Unified identity contract

The adapters in `nanobot.sagasmith_hosts` map each Host into:

```json
{
  "host": "openclaw",
  "channel": "discord",
  "actor_principal": "discord:user:alice",
  "conversation_principal": "discord:group:table-1",
  "session_id": "discord:table-1",
  "tenant_id": "guild-1"
}
```

For legacy v1, `actor_principal` is the authorization subject. In v2 it remains a requester
compatibility alias and must never be populated with the Agent identity. `requester_principal`
identifies the player who requested the turn, `resource_owner_principal` identifies the campaign
owner, and `acting_host_principal` identifies the Agent workload executing the authoritative
operation. `conversation_principal` is the routing and
audience boundary. Users in one group therefore share a conversation without sharing roles.
The bridge overwrites any model-supplied principal argument. For a modern downstream it signs a
fresh v2 delegation for the configured `targetService`, exact called tool, trusted requester and
short-lived bridge turn; the explicit legacy mode signs v1. Hosted Worker uses the Web-supplied v2
turn fields above. Both forward standard MCP tools/resources/prompts and return standard content
blocks and receipts unchanged.

Available mappings are:

| Host | Trusted input |
|---|---|
| SagaSmith Agent | normalized `InboundMessage` actor/conversation fields |
| Nanobot | Channel `sender_id`, `chat_id`, and trusted metadata |
| OpenClaw | gateway requester context, including sender and conversation binding |
| Hermes | gateway `SessionSource` sender/chat fields |
| Codex | fixed local profile and project identity |
| Claude Code | fixed local profile and project identity |
| Hosted Worker | Service-authenticated user/Agent identity and conversation id |

Codex and Claude Code are single-user local bindings. For Nanobot, OpenClaw, and Hermes, a static
bot-wide context is invalid: the gateway integration must call its `adapt_*` function for every
trusted requester/conversation and create a requester-scoped bridge. If a Host cannot expose the
real sender and conversation to an extension, it cannot safely host a multi-user SagaSmith table.

## Launch contract

Write the downstream MCP definition, trusted context, and a random secret of at least 32 bytes to
owner-readable files. `BridgeLaunch.for_host()` returns the native server shape for `codex`,
`claude-code`, `nanobot`, `openclaw`, or `hermes`; every shape ultimately launches:

```text
sagasmith-auth-bridge
  --config <downstream-mcp.json>
  --context <trusted-context.json>
  --secret-file <auth-context-secret>
```

The downstream definition may use stdio:

```json
{
  "type": "stdio",
  "command": "python",
  "args": ["-m", "sagasmith_narrative_mcp.server"],
  "env": {"SAGASMITH_AUTH_CONTEXT_SECRET": "${SAGASMITH_AUTH_CONTEXT_SECRET}"}
}
```

or Streamable HTTP:

```json
{
  "type": "streamable-http",
  "url": "http://127.0.0.1:8767/mcp"
}
```

The bridge and MCP must use the same secret. Do not place identity, role, campaign authority, or
the secret in a prompt, transcript, model tool argument, repository file, or shared bot profile.

## Conformance gate

`tests/host_conformance` runs the common adapter contract and real bridge against D&D, CoC, and
Narrative MCP processes. Both `release-lock` and `latest-main` CI lanes force all 42 real
Host/domain/transport combinations through MCP 2026-07-28. Separate dual-era tests retain legacy
coverage. The matrix covers principal forgery replacement, actor/conversation separation, epoch
advancement, tools/resources/prompts, and all seven Host profiles including Hosted Worker. The
domain suites independently enforce replay rejection, expiry, stale revision, authorization
isolation, deterministic catalogues, receipts, and legacy compatibility.

## Hosted Worker deployment

Set a unique `SAGASMITH_WORKER_SERVICE_TOKEN` (at least 32 bytes) for the Web-to-Agent audience.
The completion request must carry `trusted_context` separately from the single player message.
It includes `system_id`, so the worker connects only the matching `systemIds` MCP configuration.
`allowed_operations` contains exact MCP tool IDs such as `campaign_query` or `resolution`, never
Web policy groups such as `campaign.read`. Web derives that exact allowlist from system, phase,
caller permissions and task. Agent rejects unknown IDs before invoking the model and also enforces
the same list when tools are called. The per-turn live registry sees legacy catalog updates while
keeping transient response tools out of the underlying session registry.
The response contains `mcp_results` with standard text/image/audio/resource/embedded-resource
blocks and `host_media` envelopes for Web artifact ingestion. Activity callbacks reuse one HTTP
client; their token is scoped to Web and is never passed downstream.

`GET /metrics/mcp` exposes bounded counters by phase, outcome, transport and protocol era, plus
fixed buckets for stable-catalog candidate and per-turn selected counts. It never uses tool names,
users, campaigns, runs or arguments as labels, so Hosted deployments can scrape it without creating
unbounded cardinality or leaking authority context.

`--workspace-ttl-seconds`, `--workspace-max-bytes`, and `--workspace-max-count` bound registered
worker workspaces. Cleanup removes only terminated directories with a matching SagaSmith marker;
unknown directories, symlinks and active workspaces are not deletion candidates. Roll back the
Agent image before changing the request contract; use `protocolMode: "legacy"` only for an MCP
protocol rollback, not as a long-term authorization boundary.
