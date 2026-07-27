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
          "SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS": "..\\reference\\DnD-Books\\5e\\Books",
          "SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS": "..\\reference\\DnD-Books\\5e\\Campaign",
          "SAGASMITH_DND_MCP_RULE_OCR": "1",
          "SAGASMITH_DND_MCP_RULE_OCR_SCALE": "2.0",
          "SAGASMITH_DND_MCP_MODULE_OCR": "1",
          "SAGASMITH_DND_MCP_MODULE_OCR_SCALE": "2.0",
          "SAGASMITH_DND_MCP_AUTO_SEED": "1"
        },
        "toolTimeout": 900,
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
The D&D server's compact domain catalogue contains 82 public tools, with
Lobby/Play/Combat ceilings of 61/46/44. The Agent allowlist remains exactly the
12 core tools shown above; do not add retired domain aliases to `enabledTools`.

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
`lobby.rules` and uses the public `rule_import` facade in this order:
`discover` → `stage` → `inspect` → `ingest` → `extract_candidates` → `review` →
`compile` → `install` → `activate`. The Agent acting as DM reviews inspection
warnings from exact extracted text or rendered page evidence and sets
`payload.acknowledge_warnings=true` only after that review. For a PDF warning or
ambiguous candidate, call `rule_import(action="render_page")` with the staged
job id and exact page before acknowledging it. Missing or conflicting source
evidence, including a page that needs visual review when the active model cannot
inspect images, remains an external review boundary. Search the imported evidence
with the exact `source_ids` filter before expanding a chunk. The server binds
accepted candidates to the imported checksum and page range; imported prose
remains `catalog_only` unless reviewed mechanics pass compilation and the
campaign owner explicitly approves activation of the installed version. Never
generate Python, mutate the database, use
retired raw import tools, or silently enable a pack. During play, use
`campaign_rules(action="explain")` and `campaign_rules(action="receipts")` for
the active fingerprint and immutable settlement evidence; rule configuration
changes are unavailable during combat.

For the fixed 12-tool Agent path, an `exposure_call` response with live
`status="pending_ruling"` carries `ruling_resolution` and refers to
`server_capabilities.ruling_policy`. Its default resolver is `agent`; an
explicitly classified external-input exception names `external_input` instead.
The Agent performs its assigned reasoning and then uses public tools to settle
the resulting state. It waits for external input only for a player-owned choice,
owner approval, permission escalation, or missing/conflicting source review.
Native domain calls carry the same `default_resolver`, `ruling_kind`, and
`policy_ref`, and compact facades copy that classification to their top level.
Classification covers the full nested `pending`, `ruling_requirement`, and
`ruling_requirements` set. A real external exception anywhere in that set wins;
otherwise an unclassified DM ruling belongs to the Agent. Contradictory resolver
and kind fields must be rejected or normalized rather than selected by envelope
position.
Ordinary source-independent DM estimates follow the same ownership. For
example, when a module establishes a journey but omits its exact duration, the
Agent chooses the elapsed interval from current scene and world context and
commits a strict `agent_dm_adjudication` through the public playthrough path.
It must not attach unrelated source prose or ask for external input merely
because the duration was not printed.
A safe engine prerequisite may return `pending_ruling` with `committed=false`,
`missing`, and a `retry_contract`; this is a control return, not a fictional
failed action. The Agent supplies ordinary scene/rule facts and retries at the
current revision. Missing ranges, unresolved hydration, and unsupported source
contracts remain source-review boundaries and cannot be downgraded to generic
Agent adjudication.
Lobby review states use the same typed ownership before any live action.
`rule_import(action="extract_candidates")` returns
`job.review_resolution`, `job.review_requirements`, and per-candidate
`ruling_requirement`; `module_query(view="candidates")` does the same for a
`review_ready` statblock candidate. Exact-text review defaults to the Agent. If
any nested requirement is `missing_or_conflicting_source_review`, preserve that
external owner and repair/review the evidence instead of accepting the candidate.
Reviewed rule-statblock parser warnings also carry
`validation.ruling_requirements` and `validation.default_dm_resolver`.
Scene readiness applies the same contract per item through
`ruling_requirements`. Ordinary card, scene, spell, and module adjudications
name the Agent; missing ranged/thrown ranges and incomplete source hydration
name the source-review boundary and block combat rather than inviting invented
mechanics.
Declarative rule-pack `ruling.require` entries also default to Agent reasoning;
`choice.require` remains a player-owned external input. Full-party,
playthrough, and encounter regression drivers preserve stopped rulings as
machine-readable output with top-level `status`, `default_resolver`, and
`ruling_requirements`, so the Agent should resume from those fields rather than
from an exception string.
The same contract covers character validation and derivation. A pre-commit
spell or activity ruling with no payment must return to its named resolver
before the Agent rolls healing, applies an effect, starts combat, or assumes a
slot or charge was consumed. A paid generic-effect ruling is resumed without
paying the resource again.

For an already indexed 2014 statblock whose columns were split into sibling text
chunks, the D&D workflow first retries `character_create_from(mode="statblock")`
with source-established `chunk_ids` and the exact printed heading in
`payload.source_statblock_name`. The MCP server reconstructs and source-scopes
that card from text alone, so the parent Agent does not need image capability.
Only missing or conflicting required facts proceed to the local
`rule_import(action="recover_statblock")` OCR path.

The first inspection of a scanned or corrupt-text book may perform selective OCR.
Keep `toolTimeout` at 900 seconds for real rulebook corpora; normalized and raw page
extraction caches make exact subsequent runs fast. Stdio servers receive only their
configured `env`, so paths and OCR settings must be present in this MCP block rather
than only in the shell that starts Nanobot.

`SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS` and
`SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS` are separate allowlists. The first admits
optional rule publications; the second admits campaign PDFs, appendix packets,
maps, pregenerated-character documents, and other lobby imports. On Windows,
separate multiple roots with `;`. Before staging a suspected character document,
the Agent uses `character_query(view="document")`; it must not force that file
through module import.

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
