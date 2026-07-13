# Agent Workspace Template

This directory is a clean starting point for one SagaSmith Agent instance.

- Put the agent's behavior rules in `AGENTS.md`.
- Define the public-facing persona in `SOUL.md` and `IDENTITY.md`.
- Record user-specific preferences in `USER.md`.
- Add only intentional periodic work to `HEARTBEAT.md`.
- `memory/MEMORY.md` begins empty and is populated at runtime.

Runtime sessions, local databases, MCP state, uploads, generated artifacts, and
channel data are ignored by Git. Do not commit credentials or personal data.
