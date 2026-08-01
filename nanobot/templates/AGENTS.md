# AGENTS.md - Workspace Contract

This workspace is the agent's durable working area. Treat its contents as
private unless the user explicitly asks to share them.

Identity and voice come from `IDENTITY.md` and `SOUL.md`. Domain-specific rules
come from the relevant Skill and MCP server, never from this generic template.

## Session startup

Use runtime-provided startup context. Do not manually reread bootstrap or memory
files unless the user requests it or the supplied context is incomplete.

When a domain MCP returns `host_context_binding`, treat it as authoritative. A
change of campaign, authenticated principal, role, audience, branch, or context
epoch is a hard replay boundary. Do not reintroduce older messages, summaries,
workspace memory, cached retrieval, or stale receipts after that boundary.

## Domain authority

- Prefer connected `mcp_*` capabilities for state owned by their domain.
- Use the narrowest matching public tool and preserve revisions, idempotency,
  provenance, permissions, and random-stream receipts.
- Do not replace an available MCP workflow with direct databases, private APIs,
  local command-line tools, or temporary scripts.
- Treat model output as a proposal until the owning service validates and commits it.

## Memory ownership

- `IDENTITY.md` and `SOUL.md` define the agent, not application state.
- `USER.md` contains durable user preferences.
- `memory/MEMORY.md` contains workspace context.
- Domain state belongs to its MCP or application store.
- Never copy private or domain-scoped state into a broader workspace file.
- With `memory_policy=domain_authoritative`, query the domain owner instead of
  searching workspace memory.

## Safety

- Do not expose private information.
- Confirm destructive or externally visible actions unless already authorized.
- Prefer recoverable changes and verify important writes.
- In group chats, respond only when addressed or when the contribution is useful.

## First run

If `BOOTSTRAP.md` exists, use it to initialize the workspace, then remove it once
its setup is complete.
