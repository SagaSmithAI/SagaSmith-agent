# Runtime Workflows

## Session start

1. `doctor`
2. `campaign list`
3. `campaign show`
4. `campaign rules-get`
5. `module list`
6. Retrieve the current scene, recent events, recent memory, and `party show`.
7. Use `character show` for each PC and every NPC/monster that is currently relevant.

## New campaign

1. `campaign start`
2. Optional `module inspect` and `module ingest`
3. Use `character build` for each confirmed PC, creating its public template and
   initial campaign instance atomically. PC/NPC/monster templates or direct
   instances all use complete `sheet v2` and `notes v2` documents.
4. Record the opening event
5. Keep the initial snapshot returned by `start`

## Rule question

1. `rules search --campaign`
2. Select by title, edition and publication
3. `rules expand`
4. Answer with source metadata

## Restore

1. `save verify`
2. Explain the target slot and branch behavior
3. `save restore`
4. Refresh campaign, every relevant actor with `character show`, `party show`,
   scene, events, and memory. Do not retain pre-restore chat state.

## Session close

1. Reconcile PC/NPC/monster HP, resources, conditions, effects, equipment,
   inventory, prepared spells, scene progress, party stash, and campaign state
2. Append the session events, durable campaign memories, and actor-specific NPC
   memories
3. `save create`
4. Inspect the generated delta recap; use `save regenerate-recap --slot` if needed

## Module generator handoff

1. Ingest the existing generated Markdown without rewriting it
2. `module index --campaign`
3. `module export-scenes --campaign --output`

## Audit recovery

- `state history` inspects mutations.
- `state undo` and `state redo` change audited state without deleting snapshots.
  After restore, the restored cursor deliberately cannot redo the abandoned future.
- `memory scope` reports the memories effective on the current branch.
