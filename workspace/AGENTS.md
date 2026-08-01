# AGENTS.md - Workspace Contract

This workspace is the agent's durable working area. Treat its contents as
private unless the user explicitly asks to share them.

Identity and voice come from `IDENTITY.md` and `SOUL.md`. Domain-specific rules
come from the relevant Skill and MCP server, never from this generic workspace.

Use runtime-provided startup context. When a domain MCP returns
`host_context_binding`, treat it as authoritative. Any change of campaign,
principal, role, audience, branch, or context epoch is a hard replay boundary;
do not reintroduce older messages, summaries, workspace memory, cached retrieval,
or stale receipts.

Prefer connected `mcp_*` capabilities for domain-owned state. Preserve
revisions, idempotency, provenance, permissions, and random-stream receipts. Do
not bypass an MCP with direct database access, private APIs, local CLIs, or
temporary scripts. Model output remains a proposal until the owning service
validates and commits it.

Keep user memory, workspace memory, and domain state separate. Never copy private
or scoped domain facts into broader files. Confirm destructive or externally
visible actions unless already authorized, and verify important writes.
