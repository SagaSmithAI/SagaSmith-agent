# Database contract

## Authority

The database is authoritative. Legacy files such as `saves/存档*.json`,
`live_party.json`, `world_state.json`, and `combat_state.json` are not part of
this workflow.

## Snapshot coverage

A complete snapshot contains campaign metadata and configuration plus:

- world state;
- party and characters;
- combats;
- plot summaries and campaign events;
- mutable module progress such as scene state;
- campaign-scoped player/character channel records if present.

Module source documents, chapter metadata, and scene indexes are immutable imported content.
They remain in the database across restores and are referenced by scene-state IDs rather than
copied into every snapshot.

Snapshot rows, tool audit history, state revisions, dice audit history, and global
rule/compendium data are intentionally not nested into a snapshot. Restoring a
snapshot retains those historical records.

## Vector storage

Dense embedding vectors (BGE-M3, 1024-dim) for rule and module chunks are stored
**outside snapshot boundaries**:

- **ChromaDB** (when `CHROMA_DB_URL` or `CHROMA_DB_PATH` is set): vectors live in
  the `dnd_rules` and `dnd_modules` ChromaDB collections with HNSW indexing.
  Snapshots never read or write ChromaDB — vectors are regenerated from SQL chunk
  content on re-ingest or via `vector reindex`.
- **Fallback** (no ChromaDB configured): vectors are stored in the
  `rule_chunks.embedding_json` and `module_chunks.embedding_json` JSON columns.
  These columns are part of the static chunk rows and are never captured or
  restored by snapshots.

In both paths, restoring a snapshot does not delete, replace, or invalidate
vector data.  The `vector migrate` CLI command copies existing SQL embeddings
into ChromaDB for users upgrading from the fallback path.

## Isolation and identity

All snapshot lookup uses `(campaign_id, slot)`. Restore additionally validates
that `snapshot_json.campaign_id` equals the requested campaign. Importing or
cloning into a new campaign is a separate future operation; ordinary load must
never rewrite campaign identity.

## USER.md projection

`Campaign.config.user_md_player_roles` is the database copy of the managed
campaign block in `USER.md`. `--workspace` synchronizes it:

- save: USER.md block -> campaign config -> snapshot;
- load: snapshot -> campaign config -> USER.md block;
- undo: restored campaign config -> USER.md block.

The rest of `USER.md` is outside campaign state and must remain unchanged.

## Errors

CLI output is JSON. A nonzero exit with an `error` object means no successful
operation should be reported. Snapshot restore is transactional; database state
rolls back when restoration fails.
