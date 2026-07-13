# SagaSmith D&D CLI Contract

- Executable: `sagasmith-dnd`
- Agent calls always include `--json`.
- stdout contains exactly one JSON document.
- stderr may contain logs or model download progress.

Success:

```json
{"ok":true,"data":{},"error":null,"meta":{"command":"group.action[.subaction]","version":"0.2.0"}}
```

Failure:

```json
{"ok":false,"data":null,"error":{"code":"not_found","message":"..."},"meta":{}}
```

Exit codes: 0 success, 2 invalid input, 3 not found, 4 conflict, 5 configuration,
6 optional dependency, 10 internal error.

Keep responses bounded with `--limit`. Search first and expand one selected chunk.

Compatibility workflows include:

- `module index` / `module export-scenes`
- `save regenerate-recap`
- `memory scope` / `memory status`
- `state history` / `state undo` / `state redo`
- `character inventory|wallet|equipment|spell|effect|memory|resource <action>`
- `character library list`, `character instantiate`, and `character build`
- `character ability roll|apply` for code-validated ability generation
- `party inventory|wallet <action>`

For the validated runtime character contract, read `character-schema-v2.md`.
`character update --sheet/--notes` replaces a supplied full document; it is not a
patch API. PC, NPC, and monster cards share that contract, and NPC/monster notes
must include `profile.summary`.
