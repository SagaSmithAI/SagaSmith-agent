# Isolated NPC portrayal

SagaSmith Agent includes `portray_npc` for D&D hosts that need one NPC to reason
from its own bounded knowledge instead of inheriting the parent Agent's entire
conversation, workspace, and tool surface.

The tool accepts only the signed bundle returned by
`continuity_context(purpose="npc_turn")`. Each call:

- uses the current immutable provider/model runtime;
- sends one fresh system message and one JSON bundle message;
- passes `tools=None` and exposes no Skills or workspace;
- is awaited synchronously and never posts to the background subagent bus;
- creates no child session or persisted transcript;
- validates actor, target, basis-ref, and `npc-turn-proposal.v1` structure;
- permits one fresh repair generation for invalid JSON/contract output; and
- can run an optional fresh zero-tool guardian audit.

The v2 bundle contains an MCP-owned structured `conversation`, not a copy of
the Agent channel transcript. It is campaign/branch/scene/scope-bound, has an
event cursor and explicit participants, and contains only audience-observable
speech/action/portrayal fields. The bundle's fixed
`sagasmith.delegation.v1` object must prohibit inherited history, tools, and
worker persistence.

`isolated_evaluate(jobs=[...])` may evaluate up to 16 independent signed
bundles concurrently. Parallelism stops at proposal generation: the parent must
validate and commit proposals serially, then refresh every remaining bundle
after a state-changing write.

This is intentionally separate from `spawn`. General subagents can read files,
search, execute commands, retain task context, and finish asynchronously; those
properties are useful for work but unsafe for a receipt-bound NPC turn.
`portray_npc` and `isolated_evaluate` are available only in the main (`core`)
tool scope, so an NPC call
cannot recursively spawn another portrayal call.

The result is a proposal, never authoritative campaign state. The parent Agent
must resolve requested mechanics through D&D MCP, reread a bundle after any
change, select accepted deltas, and commit via the MCP's signed NPC continuity
contract. The MCP remains responsible for receipt freshness, permissions,
ledger ownership, event participants, and atomic state writes.

Providers need only ordinary text chat and JSON output. Native structured-output
or image support is not required; OCR/image review happens upstream in the D&D
runtime, and the NPC receives reviewed text evidence. Fenced JSON is accepted,
but fuzzy JSON repair is not used because guessing a field can change intent or
knowledge provenance.
