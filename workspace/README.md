# Agent Workspace Template

This directory is a clean starting point for one SagaSmith Agent instance.

- Put the agent's behavior rules in `AGENTS.md`.
- Define the public-facing persona in `SOUL.md` and `IDENTITY.md`.
- Record user-specific preferences in `USER.md`.
- Add only intentional periodic work to `HEARTBEAT.md`.
- `memory/MEMORY.md` begins empty and is populated at runtime.

Runtime sessions, local databases, MCP state, uploads, generated artifacts, and
channel data are ignored by Git. Do not commit credentials or personal data.

This template is for a user-owned Local Agent workspace. It is not a campaign database, an MCP
authorization store, or a Hosted Worker lifecycle marker. In an authoritative SagaSmith campaign,
the domain MCP owns campaign/revision/actor knowledge and the Agent excludes workspace/Dream
memory from the campaign prompt.

Hosted supervisors create a separate child workspace, pass a stable unique `--workspace-id`, and
let `sagasmith-agent-worker` maintain its `sagasmith.hosted-workspace/v1` marker. Do not copy this
template over an existing Hosted workspace, invent marker files, or delete unknown workspace
directories during cleanup.
