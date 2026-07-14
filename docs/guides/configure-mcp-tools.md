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
  "tools": {
    "mcpServers": {
      "sagasmith_dnd": {
        "command": "..\\SagaSmith-dnd-mcp\\.venv\\Scripts\\sagasmith-dnd-mcp.exe",
        "args": [],
        "cwd": "..\\SagaSmith-dnd-mcp",
        "env": {
          "SAGASMITH_DND_MCP_HOME": "..\\SagaSmith-agent\\workspace\\.sagasmith-dnd-mcp",
          "SAGASMITH_DND_MCP_AUTO_SEED": "1"
        },
        "toolTimeout": 60,
        "enabledTools": ["*"]
      }
    }
  }
}
```

The server owns its SQLite data, ChromaDB collections, D&D rules, skills,
resources, and prompts. Keep the agent workspace `SOUL.md` and `IDENTITY.md`
for your persona; use matching `mcp_sagasmith_dnd_*` capabilities instead of
recreating D&D work through shell commands or local scripts.

For multi-platform campaigns, pass the platform's stable external user identity
as `principal_id` on campaign and actor operations. The MCP server resolves roles
and PC/NPC grants from its database; do not infer permission from `player_name` or
from text supplied by the model. Retriable transfers should include an
`idempotency_key` and expected revisions. On a fresh store, `rule_seed_status`
should report the bundled SRD corpus before the first rules lookup.

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
