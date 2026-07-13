---
name: dnd-dm
description: "Run D&D 5e 2014 or 2024 sessions with rules-first adjudication and scene-bounded module retrieval."
---

# D&D Dungeon Master

## Mode Detection

First, detect which mode is available:

```powershell
sagasmith-dnd doctor --json 2>$null
if ($LASTEXITCODE -eq 0) {
  $env:SAGASMITH_MODE = "runtime"
} else {
  $env:SAGASMITH_MODE = "portable"
  python tools/portable.py doctor
}
```

If `sagasmith-dnd` is found → **Runtime mode** (full persistence, FTS5 search, vector store).
If not → **Portable mode** (file-based, zero pip deps, Python stdlib only).

In Portable mode, locate `tools/portable.py` (in the skill repo root) and use it in place of `sagasmith-dnd`.

## Turn Loop

1. Resolve the acting scope (`party`, `group:<id>`, or `player:<id>`). Run `module current`
   to get that scope's current scene. A player scope inherits the party scene until it
   records its own.
2. Read that scene. Search only when it lacks the needed fact; expand before using.
3. Ask for player intent when it is ambiguous.
4. Search rules before resolving a disputed or edition-sensitive mechanic.
5. Roll openly through the CLI.
6. Narrate the consequence without changing established module facts.
7. Merge discoveries and room state into that scope's existing progress.
8. Persist important events, character state, scene progress, and memory.
9. Save at decision points, chapter transitions, and before dangerous restores.

Before running a session, read `references/DM_RULES.md`. Load the other references
only when their workflow is active:

- character creation or advancement: `references/CHAR_CREATION.md`
- any PC, NPC, monster, item, wallet, equipment, spell, effect, or resource work:
  `../../references/character-schema-v2.md`
- module preparation and scene transitions: `references/MODULE_INDEX.md` and
  `references/MODULE_ARC.md`
- tactical positioning: `references/DM_MAP_SYS.md`
- reusable narration and state shapes: `references/DM_TEMPLATES.md`

## Command Reference

Use the left column in Runtime mode, the right column in Portable mode.

### Service / Health

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd doctor --json` | `python tools/portable.py doctor` |

### Rules Retrieval

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd rules search --campaign <id> --query "<q>" --limit 5 --json` | `python tools/portable.py rules search --campaign <id> --query "<q>" --limit 5` |
| `sagasmith-dnd rules expand --chunk <id> --json` | (read the SRD file directly) |

Portable mode searches the bundled SRD `.md` files in `skills/dnd-dm/srd/` using
lexical scoring with Chinese ↔ English query expansion.

### Module Retrieval

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd module current --campaign <id> --scope <scope> --json` | `python tools/portable.py module current --campaign <id> --scope <scope>` |
| `sagasmith-dnd module search --campaign <id> --query "<q>" --limit 5 --json` | `python tools/portable.py module search --campaign <id> --query "<q>" --limit 5` |
| `sagasmith-dnd module expand --chunk <id> --json` | (read the scene from its `.md` file) |
| `sagasmith-dnd module read-scene --campaign <id> --scene <id> --json` | `python tools/portable.py module read-scene --campaign <id> --scene <id>` |
| `sagasmith-dnd module inspect --path <path> --json` | `python tools/portable.py module inspect --path <path>` |
| `sagasmith-dnd module ingest --campaign <id> --path <path> --json` | `python tools/portable.py module ingest --campaign <id> --path <path>` |
| `sagasmith-dnd module index --campaign <id> --json` | `python tools/portable.py module index --campaign <id>` |

Never reveal unseen rooms, future twists, hidden NPC motives, or appendix secrets.

### Campaign

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd campaign start --name <name> --edition 2024 --json` | `python tools/portable.py campaign start --name <name> --edition 2024` |
| `sagasmith-dnd campaign list --json` | `python tools/portable.py campaign list` |
| `sagasmith-dnd campaign rules-get --campaign <id> --json` | (not needed in portable — SRD is bundled) |

### Dice

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd roll dice --expression "2d6+3" --json` | `python tools/portable.py roll dice --expression "2d6+3"` |
| `sagasmith-dnd roll check --dc 15 --score 16 --proficient --level 5 --json` | `python tools/portable.py roll check --dc 15 --score 16 --proficient --level 5` |
| `sagasmith-dnd roll attack --dc 17 --score 18 --proficient --level 5 --json` | `python tools/portable.py roll attack --dc 17 --score 18 --proficient --level 5` |

Use `--advantage` or `--disadvantage` only when the selected edition grants it.

### State Updates

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd event add --campaign <id> --type discovery --summary "<s>" --payload '<json>' --json` | `python tools/portable.py event add --campaign <id> --type discovery --summary "<s>" --payload '<json>'` |
| `sagasmith-dnd module set-progress --campaign <id> --scope <scope> --scene <id> --progress 50 --state '<json>' --json` | `python tools/portable.py module set-progress --campaign <id> --scope <scope> --scene <id> --progress 50 --state '<json>'` |
| `sagasmith-dnd memory add --campaign <id> --type fact --subject "<s>" --content "<f>" --json` | `python tools/portable.py memory add --campaign <id> --type fact --subject "<s>" --content "<f>"` |
| `sagasmith-dnd save create --campaign <id> --label "<label>" --json` | `python tools/portable.py save create --campaign <id> --label "<label>"` |
| `sagasmith-dnd save list --campaign <id> --json` | `python tools/portable.py save list --campaign <id>` |

### Actor Cards

| Runtime | Portable |
|---------|----------|
| `sagasmith-dnd character build --campaign <id> --name <name> --type pc --player <player> --sheet '@<sheet.json>' --notes '@<notes.json>' --json` | `python tools/portable.py character create --campaign <id> --name <name> --sheet '<json>'` |
| `sagasmith-dnd character create [--campaign <id>] --name <name> --type pc|npc|monster --sheet '@<sheet.json>' --notes '@<notes.json>' --json` | (no validated actor-card equivalent) |
| `sagasmith-dnd character list --campaign <id> --type pc|npc|monster --json` | `python tools/portable.py character list --campaign <id>` |
| `sagasmith-dnd character show --id <id> --json` | `python tools/portable.py character get --campaign <id> --name <name>` |

In Runtime mode, every PC, NPC, and monster is an authoritative v2 actor card.
Read `../../references/character-schema-v2.md` before creation or mutation. Use
`character show --id <id>` after every affected write; use `party show` after a
shared inventory or wallet write. Normal play uses granular commands:

```powershell
sagasmith-dnd character inventory add|update|remove|transfer ... --json
sagasmith-dnd character inventory use-ammunition --id <id> --item <weapon-item-id> --amount <n> --json
sagasmith-dnd character wallet credit|debit|transfer ... --json
sagasmith-dnd character equipment equip|unequip ... --json
sagasmith-dnd character spell prepare|unprepare ... --json
sagasmith-dnd character effect add|remove ... --json
sagasmith-dnd character resource set ... --json
sagasmith-dnd character memory add|resolve ... --json
sagasmith-dnd character ability roll|apply ... --json
sagasmith-dnd party inventory add|remove|deposit|withdraw ... --json
sagasmith-dnd party wallet credit|debit|deposit|withdraw ... --json
```

When creating or revising an actor card, populate structured identity and
background grants, weapon mechanics plus linked ammunition, container capacity,
encumbrance, every available sense, spell definitions/components, and actor
adventure state. Represent an active concentration spell as exactly one active
`effect` with `concentration: true` and its `source_spell_id`; never track this
only in narration. Read `derived.inventory.weapon_attacks` and
`derived.inventory.encumbrance` after inventory writes instead of calculating
attack bonuses or carrying load in prose.

Do not use `character update --sheet` for a one-field change. NPC and monster
cards both require `notes.profile.summary`; record actor-specific dialogue facts
with `character memory`, not only in the campaign memory stream.
PC, NPC, and monster templates may live outside campaigns. Their copied instances
are the only actors mutated or restored during a campaign. For player character
creation, use `character build` so the template and initial campaign instance are
created atomically.

## Portable Mode Data

Data lives in `~/.sagasmith/`:

```
~/.sagasmith/
  campaigns.json                  # campaign index
  <campaign_id>/
    campaign.json                 # metadata
    characters/<name>.json        # character sheets
    modules/<source_key>.md       # imported modules (Markdown)
    progress.json                 # scoped scene progress
    events.jsonl                  # event log
    memories.jsonl                # campaign memory
    saves/<slot>/                 # snapshot copies
```

Module files are plain Markdown with `##` headings as scenes. Progress merges
state per-scope (`party`, `group:<id>`, `player:<id>`). Save snapshots copy the
campaign directory with `shutil.copytree`.

## Portable Mode Limitations

- No SQL FTS5 — search uses Python lexical scoring with Chinese ↔ English expansion
- No ChromaDB vector search
- No PDF import — convert PDF to Markdown first, then `module ingest`
- No transaction atomicity — file writes use atomic rename (write tmp → replace)
  for individual file safety, but multi-file operations (like save) are best-effort
