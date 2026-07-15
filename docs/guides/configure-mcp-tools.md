# How to Configure MCP Tools in nanobot

This guide adds an MCP server to nanobot so the agent can use external tools
through the Model Context Protocol.

## What you will build

- a working nanobot agent
- one MCP server entry in `~/.nanobot/config.json`
- a restricted set of MCP tools exposed to the model

## When to use this

Use MCP when the capability you need already exists as an MCP server, or when
you want external tools to be managed outside nanobot core.

## Install

```bash
python -m pip install nanobot-ai
nanobot onboard --wizard
nanobot agent -m "Hello!"
```

Install the MCP server runtime separately. Many examples use `npx`, `uvx`, or a
remote HTTP endpoint.

## Minimal working example

Add this to `~/.nanobot/config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file"]
      }
    }
  }
}
```

Restart nanobot and ask a question that requires the MCP tool.

## SagaSmith D&D MCP

SagaSmith Agent keeps D&D state and skills outside the agent process. Clone
[`SagaSmith-dnd-mcp`](https://github.com/SagaSmithAI/SagaSmith-dnd-mcp) beside
this repository, create its virtual environment, and add this Windows config
when starting nanobot from the SagaSmith-agent repository root:

```json
{
  "agents": {
    "defaults": {
      "externalSkillsDirs": [
        "..\\SagaSmith-dnd-skills\\full\\skills",
        "..\\SagaSmith-coc-skills"
      ]
    }
  },
  "tools": {
    "mcpServers": {
      "sagasmith_dnd": {
        "command": "..\\SagaSmith-dnd-mcp\\.venv\\Scripts\\sagasmith-dnd-mcp.exe",
        "args": [],
        "cwd": "..\\SagaSmith-dnd-mcp",
        "env": {
          "SAGASMITH_DND_MCP_HOME": "..\\SagaSmith-agent\\workspace\\.sagasmith-dnd-mcp",
          "SAGASMITH_DND_SKILLS_DIR": "..\\SagaSmith-dnd-skills",
          "SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS": "..\\reference\\DnD-Books",
          "SAGASMITH_DND_MCP_AUTO_SEED": "1"
        },
        "toolTimeout": 60,
        "injectPrincipal": true,
        "enabledTools": [
          "exposure_open",
          "exposure_status",
          "exposure_search",
          "exposure_inspect",
          "exposure_load",
          "exposure_unload",
          "exposure_call",
          "server_capabilities",
          "server_tool_profiles",
          "storage_status",
          "campaign_query",
          "game_phase"
        ]
      },
      "sagasmith_coc": {
        "command": "..\\SagaSmith-coc-mcp\\.venv\\Scripts\\sagasmith-coc-mcp.exe",
        "args": [],
        "cwd": "..\\SagaSmith-coc-mcp",
        "env": {
          "SAGASMITH_COC_MCP_HOME": "..\\SagaSmith-agent\\workspace\\.sagasmith-coc-mcp",
          "SAGASMITH_COC_SKILLS_DIR": "..\\SagaSmith-coc-skills",
          "SAGASMITH_MODULEGEN_SKILLS_DIR": "..\\SagaSmith-module-gen-skills"
        },
        "toolTimeout": 60,
        "injectPrincipal": true,
        "enabledTools": [
          "exposure_open",
          "exposure_status",
          "exposure_search",
          "exposure_inspect",
          "exposure_load",
          "exposure_unload",
          "exposure_call",
          "server_capabilities",
          "storage_status",
          "campaign_query",
          "game_phase"
        ]
      }
    }
  }
}
```

Each domain server owns its SQLite data and skill access. D&D additionally owns
its ChromaDB collections and imported rule packs. Keep the agent workspace
`SOUL.md` and `IDENTITY.md` for your persona; use matching
`mcp_sagasmith_dnd_*` or `mcp_sagasmith_coc_*` capabilities instead of
recreating game state through shell commands or local scripts.

The D&D MCP owns the per-session `lobby`, `play`, and `combat` exposure; Nanobot
does not duplicate a phase/profile filter. Keep only the core exposure tools in
`enabledTools`, call `exposure_open`, then search, inspect, and load an MCP
capability group. Nanobot currently retains its initial static tool schemas, so
invoke every loaded domain tool through `exposure_call`. A host that supports
MCP `tools/list_changed` can instead use the dynamically refreshed native tool.
`combat_start` and `combat_end` change the authoritative campaign phase and make
incompatible loaded groups unavailable. `enabledTools` is therefore the outer
allowlist for the core protocol, not a list of every D&D action.

The CoC MCP follows the same protocol and server-owned session boundary. Its
Lobby groups cover campaign setup, investigators, scenario import, continuity,
and actor knowledge; Play adds investigation/SAN/chase resolution; Combat adds
attack resolution while keeping SAN/HP mutations explicit. Per-actor grants
prevent one player from reading another investigator's private beliefs, and
player module queries return only `visibility=player` scenes.

Before a campaign exists, an unbound exposure may load only `lobby.bootstrap`
to list systems or create the campaign. Reopen it with the selected
`campaign_id` before loading campaign administration, character, rules, module,
play, or combat groups. With `injectPrincipal: true`, Nanobot binds the trusted
chat principal to `exposure_open`; the MCP then injects that same principal into
the nested target arguments used by `exposure_call`.

Optional rules remain MCP-owned too. While in `lobby`, the agent loads
`lobby.rules`, stages a
locally supplied rulebook from `SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS`, calls
`rule_document_inspect` and `rule_document_import`, then searches and expands a
reviewed chunk. User-imported executable rules must go through
`rule_pack_draft_from_source`, which binds citations to the imported document
checksum and page range. The agent then inspects validation and requests
installation. It must never
generate Python, mutate the database directly, or silently enable a pack. The
DM explicitly enables an installed version with `campaign_rule_pack_set`.
During play, use `campaign_rules_explain` to audit the current branch fingerprint
and citations, and `campaign_rule_receipts` for immutable historical settlement
evidence; rule configuration changes are unavailable during combat.

`externalSkillsDirs` belongs to `agents.defaults` because nanobot loads skills
in the parent agent process. MCP server `env` values are visible only to the
child server process and cannot configure the agent's skill loader.

For multi-platform campaigns, set `injectPrincipal` to `true`. Nanobot then derives
the stable `principal_id` from its trusted inbound `channel:sender_id` request
context and removes only the caller-auth field from the model-visible tool schema.
Grant tools keep their target `principal_id` visible and receive the authenticated
caller through `by_principal_id`; never let a model choose the caller identity.
The MCP server resolves roles and PC/NPC grants from its database; do not infer
permission from `player_name` or from text supplied by the model. Every retriable
state mutation should include a fresh `idempotency_key` and the relevant expected
revision. On a fresh store, run `SagaSmith-dnd-mcp\\scripts\\smoke_seed.py` once,
then confirm `rule_seed_status` reports the bundled SRD corpus before the first
rules lookup.

## Production notes

- Prefer `enabledTools` over exposing every tool by default.
- Use `toolTimeout` for slow MCP operations.
- Use HTTP MCP only for endpoints you trust.
- Keep MCP server commands stable and versioned in deployment docs or scripts.

## Security notes

- Stdio MCP starts a local process; review the command before enabling it.
- HTTP/SSE MCP uses nanobot's SSRF guard.
- Allow private HTTP MCP hosts only with narrow `tools.ssrfWhitelist` CIDRs.
- Do not place secrets in command arguments when environment variables or
  headers can be used.

## Troubleshooting

- Run the MCP command outside nanobot first.
- Start `nanobot gateway --verbose` and inspect tool registration logs.
- If an HTTP MCP URL is blocked, check whether it points to loopback or a
  private address that needs explicit allowlisting.

## Related nanobot docs

- [MCP tools for AI agents](./mcp-tools-for-ai-agents.md)
- [Configuration: MCP](../configuration.md#mcp-model-context-protocol)
- [Security](../configuration.md#security)
