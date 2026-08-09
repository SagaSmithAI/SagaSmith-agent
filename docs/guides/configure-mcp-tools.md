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
          "game_phase",
          "skill_query"
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
does not duplicate a phase/profile filter. Keep only the core protocol tools in
`enabledTools`. Start with `skill_query(action="plan")`, read every document in
`required_now`, call `exposure_open`, then search, inspect, and load an MCP
capability group. Read each returned `skill_plan` or `skill_plan_delta` before
using the affected group or operation. Nanobot currently retains its initial
static tool schemas, so invoke every loaded domain tool through
`exposure_call`. A host that supports MCP `tools/list_changed` can instead use
the dynamically refreshed native tool.
`combat_start` and `combat_end` change the authoritative campaign phase and make
incompatible loaded groups unavailable. `enabledTools` is therefore the outer
allowlist for the core protocol, not a list of every D&D action.
The D&D server's compact domain catalogue contains 89 public tools, with
Lobby/Play/Combat ceilings of 60/58/49. The Agent allowlist remains exactly the
13 core tools shown above; do not add retired domain aliases to `enabledTools`.
`skill_query` is intentionally core so a zero-knowledge Agent can obtain the
phase/tool-group plan and read each bounded Skill fragment even though a narrow
`enabledTools` list suppresses MCP resources and prompts in NanoBot. The server
tracks fragment checksums per trusted session: unchanged reads are reported as
`already_satisfied`, while an updated fragment appears in `invalidated` and
must be read again. If the plan is unavailable, repair the Skills installation
instead of silently continuing a live campaign.

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

Optional rules remain MCP-owned too. In `lobby`, the Agent loads `lobby.rules`
and authors a rulebook only through `rulebook_draft`: `start` performs the
Core+D&D mechanical first pass, `get` and `evidence` expose the resulting draft
and exact source evidence, and repeated `edit` calls save the Agent's corrections
and source-bound decisions. `finalize` requires explicit Agent confirmation and
atomically freezes the reviewed draft as a unified v2 Pack. The same pattern uses
`module_draft` for rulebook-sized adventures and other module sources.

`content_pack` manages only immutable finalized Packs: list/get/import/export,
explicit activation/deactivation, and removal. It does not build mutable content
or expose legacy install/compile side doors. Missing or conflicting source
evidence remains an external review boundary; ordinary semantic uncertainty is
the Agent's review work and is saved with the draft and Pack. Never generate
Python, mutate the database, call retired import facades, or silently activate a
Pack. During play, use `campaign_rules(action="explain")` and
`campaign_rules(action="receipts")` for the active fingerprint and immutable
settlement evidence; authoring and rule configuration changes are unavailable
outside Lobby.

For the fixed 13-tool Agent path, an `exposure_call` response with live
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
The same contract applies to module-specific narrative consequences:
`record-event` and `record-outcome` accept a settled Agent ruling either beside
the exact premise text or by itself for a source-independent DM event. The
committed event retains that decision and reason; callers do not need a new Core
mechanic for every one-off module situation.

For reusable access to module-authored narrative context, the Agent creates a
DM-only `kind="context_anchor"` through `memory_change(action="upsert")`. The
anchor contains only opaque entity links and exact managed source bindings; it
cannot contain a predicate, trigger, condition, action, result, paraphrase, or
Agent instruction. Before adjudicating, call `continuity_context` with
`related_refs` for the current NPC, scene/location, active quest, and key item.
Its pinned `module_evidence` is exact source context, not an executable plan.
The Agent combines it with current state, decides the situation, and invokes
ordinary public MCP tools. Save only the outcome that actually occurred, and
never expose DM evidence or transfer it into ActorKnowledge until an actor
reasonably learns it.

Standard D&D rules are different: mechanic ids registered in the campaign's
active rule lock select version-locked engine implementations, so the Agent must
not reinterpret or replace them with prose rulings. A core-looking string alone
does not create an executable mechanic, and accounting or transaction mechanics
do not settle a card's authored outcome. A locked standard card with neither an
exact registered mechanic nor a persisted source-bound content clause returns
`semantic_solution.status="engine_implementation_required"` and must stop before
payment. A bundled spell, item, or creature-specific ability may instead carry
a reviewed exact-source Agent-ruling clause; that clause applies only the card's
content while action economy, accounting, attacks, damage, and transactions
remain engine-owned. Imported or homebrew boundaries are likewise fixed before
play: draft review and finalization store either a constrained schema-v2 plan or
a direct exact-source Agent-ruling requirement on every mechanical entry. Draft
finalization and Pack import recompute the semantic audit and reject missing,
stale, or deferred entries. All current public actor-card write paths prefill
unresolved custom prose with an exact-source direct Agent ruling before
persistence. `content_solution(action="compile")` is available only in Lobby for
explicit source-bound authoring. Play and Combat never call it. A
`semantic_solution.status="content_authoring_required"` result identifies corrupt
data that bypassed the invariant; it consumes no action, spell slot, charge, or
revision. Return to Lobby and author a corrected Pack version. An existing plan
may be settled through
`combat_choice(action="execute_plan")`; an existing direct ruling is decided from
current facts in its bounded DM window. Never invent a `dnd5e.core.*` id, attach
arbitrary code, or use a paid event to change the card's execution contract.

Use a one-occurrence Agent ruling only when the current situation is genuinely
unique or cannot be represented by the constrained plan operations. Such a
ruling may settle that occurrence but must not masquerade as a reusable standard
rule or silently transfer into another card.
A safe engine prerequisite may return `pending_ruling` with `committed=false`,
`missing`, and a `retry_contract`; this is a control return, not a fictional
failed action. The Agent supplies ordinary scene/rule facts and retries at the
current revision. Missing ranges, unresolved hydration, and unsupported source
contracts remain source-review boundaries and cannot be downgraded to generic
Agent adjudication.
Lobby draft review uses the same typed ownership before any live action.
`rulebook_draft(get/evidence)` and `module_draft(get/evidence)` expose mechanical
findings, exact evidence identities, and unresolved review work. Exact-text and
semantic review default to the Agent; missing or conflicting evidence preserves
the external source-review owner. Readiness is intentionally narrow: it verifies
the identities and minimum structural facts needed for the requested operation,
not whether every possible future adjudication has already been automated.

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

Do not promote book-specific parsing repairs into Core or the D&D plugin. Core
owns document invariants; D&D owns only cross-book grammar and vocabulary. When
the first pass splits a layout incorrectly, binds the wrong owner, merges two
features, or otherwise misreads one publication, the Agent inspects the exact
draft evidence and saves the correction through `rulebook_draft(edit)` or
`module_draft(edit)`. That source-bound decision travels in the unfinished draft
and finalized Pack, so it can be audited and replayed without mutating the
original source or teaching the engine a one-book heuristic. The Skills review
loop, not a growing set of MCP recovery tools, tells the Agent how to find,
correct, re-check, and finally confirm these cases.

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

Unified `.sagasmith-pack` archives may be read only from those same rule/module
roots, or by managed artifact name after the MCP exported them. Pass an
allowlisted attachment path to `content_pack(action="import")`; the Agent must
not deserialize it and write domain state itself. Imported actor cards receive
fresh actor ids and never inherit Host/session memory or ActorKnowledge.

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
