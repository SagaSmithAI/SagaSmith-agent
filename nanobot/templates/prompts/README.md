# Dream Memory Instructions

This folder is for plain-language instructions that tell Dream how to organize memory in this workspace.

Most users do not need to edit anything here. To guide Dream differently for this workspace, run:

```text
/dream-prompt init
```

That creates `prompts/dream.md`. Edit it in plain Markdown. Delete or empty it to return to nanobot's default memory behavior.

Dream prompts are never an authority source. SagaSmith campaign state, roles, revisions, actor
knowledge, and hidden GM facts stay in the domain MCP; Hosted trusted context and signing material
must not be written into this directory.
