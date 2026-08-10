# Configure MCP tools in nanobot

Use MCP when a capability server owns state or domain logic outside nanobot. Nanobot discovers
the server's current tool list and exposes those tools as native model tools.

## Minimal example

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

Use a fixed `enabledTools` list for a server with a fixed catalogue. Use `['*']` when a trusted
server intentionally changes its native tool list with MCP `tools/list_changed`; nanobot refreshes
the registry whenever it receives that notification.

## SagaSmith D&D

SagaSmith D&D uses one session-aware `exposure` protocol and a mutable native tool list. Configure
the trusted D&D server with `enabledTools: ["*"]`; the server initially publishes only its six core
tools and publishes selected domain tools after `exposure(set)`.

```json
{
  "agents": {
    "defaults": {
      "externalSkillsDirs": ["..\\SagaSmith-dnd-skills\\full\\skills"]
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
          "SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS": "..\\reference\\DnD-Books\\5e\\Books",
          "SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS": "..\\reference\\DnD-Books\\5e\\Campaign",
          "SAGASMITH_DND_MCP_RULE_OCR": "1",
          "SAGASMITH_DND_MCP_MODULE_OCR": "1",
          "SAGASMITH_DND_MCP_AUTO_SEED": "1"
        },
        "toolTimeout": 900,
        "injectPrincipal": true,
        "enabledTools": ["*"]
      }
    }
  }
}
```

The six always-available tools are `exposure`, `server_capabilities`, `storage_status`,
`campaign_query`, `game_phase`, and `skill_query`. Do not configure retired exposure facades or a
second host-side phase/profile filter.

Use the protocol in this order:

1. Call `exposure(action="open", campaign_id=...)` for the current MCP session and principal.
2. Call `exposure(action="search", query=...)` to discover a bounded domain capability.
3. Call `exposure(action="set", add_tool_ids=[...], remove_tool_ids=[...])`.
4. Let nanobot process `tools/list_changed`, then call the newly listed domain tool directly.
5. Call `exposure(action="get")` to inspect the current session binding and loaded tool ids.

The MCP connection is the exposure session. Its campaign, principal, phase and loaded tools remain
server-owned. A phase change can remove incompatible tools from the next native tool list. If an
operator must isolate unrelated principals, run separate trusted MCP connections/processes; do not
let model-authored fields choose transport identity.

## Pack authoring boundary

Import and edit only in Lobby. `rulebook_draft(start)` or `module_draft(start)` performs the
Core+D&D mechanical first pass. The Agent then repeatedly reads exact evidence, finds mistakes,
applies source-bound edits and rechecks the draft. Ordinary semantic uncertainty and book-specific
layout decisions are review work, not new Core/D&D parsing heuristics.

Only structural corruption, missing/conflicting source identity, explicit failed tests or a failed
compile block finalization. Warnings and unresolved judgments stay visible for Agent review. The
final `finalize` call requires explicit confirmation plus durable revision/idempotency data and
freezes the reviewed draft as an immutable Pack. Fine-grained draft edits do not require a new
revision/idempotency ceremony.

Keep one-book decisions in draft/Pack metadata. Move a pattern into the D&D Skill only after it is
reusable across books as an Agent inspection or correction procedure. Do not grow MCP recovery
facades or encode publication-specific guesses in Core/D&D.

## Play, NPC and combat judgment

MCP owns authoritative state, actor authorization, transactions and engine mechanics. The Agent
owns narrative and situational judgment:

- resolve who can hear and understand an utterance and submit audience facts;
- infer targetability, range, cover, visibility, line of effect, friendly fire and movement
  legality when combat runs in Agent spatial mode;
- use engine/grid results when combat runs in Grid mode;
- keep one private, zero-tool worker context per `conversation + NPC` and submit proposal v4.

NPC proposal v4 requires only transport identity, a response decision and text for each utterance
segment. Truth posture, basis references, targets, language and delivery are optional. When those
fields are supplied, MCP validates their actor-scoped references; it does not invent narrative
meaning. Mechanical effects must be requested and settled through public engine tools.

## CoC and other servers

Do not assume that another SagaSmith server implements the D&D exposure contract. Read its
`server_capabilities` and configure the tools it actually advertises. A fixed-catalogue server may
still use a narrow `enabledTools` list.

## Production notes

- Keep `injectPrincipal: true` for multi-platform campaigns.
- Put rule and module imports under separate allowlisted roots.
- Set a long timeout for OCR-heavy first imports; cached later runs should be fast.
- Review stdio commands and remote endpoints before enabling them.
- Use narrow SSRF allowlists for trusted private HTTP MCP hosts.

## Related docs

- [MCP tools for AI agents](./mcp-tools-for-ai-agents.md)
- [Configuration: MCP](../configuration.md#mcp-model-context-protocol)
- [Security](../configuration.md#security)
