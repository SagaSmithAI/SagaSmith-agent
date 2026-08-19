# SagaSmith external Host Auth Adapters

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

`actor_principal` is the authorization subject. `conversation_principal` is the routing and
audience boundary. Users in one group therefore share a conversation without sharing roles.
The bridge overwrites any model-supplied principal argument, signs `sagasmith.auth-context/v1`,
tracks the current authorization epoch, forwards tools/resources/prompts and their change
notifications, and returns the MCP receipt unchanged.

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
Narrative MCP processes. It covers principal forgery replacement, actor/conversation separation,
epoch advancement, tools/resources/prompts, and all seven Host profiles including Hosted Worker.
The domain suites independently enforce nonce replay rejection, stale authorization epochs,
revocation, campaign isolation, receipts, and dynamic tool-list changes.
