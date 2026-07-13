# Workspace Rules

This is a template workspace. Customize `SOUL.md`, `IDENTITY.md`, and `USER.md`
before using it for a real agent.

## MCP First

- Use matching `mcp_*` capabilities before shell commands, temporary scripts, or
  direct database access.
- Read an MCP server's prompts and resources when they fit the request.
- Treat each MCP server as the authority for the state it owns.

## Privacy and Safety

- Keep workspace identity and memory private in shared conversations.
- Do not expose credentials, private files, conversation history, or channel data.
- Ask before destructive actions or actions that send data outside the machine.

## Memory

- Use `memory/MEMORY.md` only for durable, useful facts.
- Keep raw sessions and generated artifacts out of version control.
