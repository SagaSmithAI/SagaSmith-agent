# SagaSmith Local Agent Kit templates

These credential-free templates cover the two supported local MCP transports.
Copy them outside the repository, replace local paths and identities, then keep
the resulting files owner-readable.

The templates target the coordinated MCP 2026-07-28 release lock. Use `protocolMode: "legacy"`
only as a temporary compatibility rollback; archived split repositories are not inputs.

- `bot-config.template.json` is a SagaSmith Agent channel starting point for
  Discord, QQ, or Telegram. Enable only installed channel extras and replace
  the allowlists before starting a bot.
- `mcp-stdio.template.json` is the downstream definition for clients that own
  one process per domain.
- `mcp-http.template.json` connects clients to loopback-only, long-lived domain
  processes.
- `domain-runtime.template.json` lists the process environment for long-lived
  domain servers. D&D and CoC map their friendly cache paths to the prefixes
  consumed by `sagasmith-core`; Narrative reserves a path but does not yet use
  an embedder or persistent embedding cache.
- `trusted-context.template.json` is a single-user local identity example for
  Codex, Claude Code, or another generic Agent.
- `host-launch.template.json` gives the native Codex, Claude Code, OpenClaw,
  and generic stdio shapes for the same auth bridge. Copy one host entry; do
  not load the whole example object as a client configuration.

Codex, Claude Code, OpenClaw, and other Hosts should launch
`sagasmith-auth-bridge` with one downstream definition, one trusted context,
and a separately generated secret file. Multi-user Bot Hosts must create a
requester/conversation-scoped bridge from trusted channel metadata; a static
bot-wide OpenClaw or generic context is unsafe. See
`docs/sagasmith-host-adapters.md`.

No template contains a token, API key, signing secret, role grant, or campaign
authority. Do not commit filled copies.
