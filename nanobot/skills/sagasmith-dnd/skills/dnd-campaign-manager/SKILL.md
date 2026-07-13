---
name: dnd-campaign-manager
description: "Create and maintain D&D campaigns, characters, modules, saves, and long-term memory through sagasmith-dnd."
---

# D&D Campaign Manager

All commands end in `--json`.

Read `../dnd-dm/references/DM_RULES.md` before mutating a campaign. For character
creation, module lifecycle, save/restore, undo, recap, memory, player assignments,
or advancement, load the matching section of that reference and the linked
specialized reference. The CLI is authoritative; never emulate a successful write.

## Start

```powershell
sagasmith-dnd campaign start --name "<name>" --edition 2014 --locale zh --json
sagasmith-dnd campaign start --name "<name>" --edition 2024 --locale en --json
```

Store the returned campaign ID. Import a module only after the campaign exists:

```powershell
sagasmith-dnd module inspect --path "<module.pdf>" --json
sagasmith-dnd module ingest --campaign <id> --path "<module.pdf>" --json
```

Existing output from `SagaSmith-module-gen-skills` can be passed directly to
`module ingest`.

## Characters

```powershell
# Any type may be a public template or a direct campaign instance.
sagasmith-dnd character create --name "<name>" --type pc|npc|monster --sheet '@<sheet.json>' --notes '@<notes.json>' --json
sagasmith-dnd character library list --type pc|npc|monster --json
sagasmith-dnd character instantiate --id <template-id> --campaign <id> --player "<player>" --json
sagasmith-dnd character create --campaign <id> --name "<name>" --type pc|npc|monster --sheet '@<sheet.json>' --notes '@<notes.json>' --json

# Character creation: atomically create the public template plus campaign instance.
sagasmith-dnd character build --campaign <id> --name "<name>" --type pc --player "<player>" --sheet '@<pc-sheet.json>' --notes '@<pc-notes.json>' --json
sagasmith-dnd character ability roll --edition 2014 --json
sagasmith-dnd character build --campaign <id> --name "<name>" --type pc --player "<player>" --ability-method point_buy --assignments '<six-ability-json>' --sheet '@<pc-sheet.json>' --notes '@<pc-notes.json>' --json
sagasmith-dnd character list --campaign <id> --json
sagasmith-dnd character show --id <character-id> --json
```

Do not persist a draft until the user confirms it.

All live PCs, NPCs, and monsters use the same complete `sheet v2` and `notes v2`
documents. This skill requires `profile.summary` for every type; the runtime
enforces it for NPCs and monsters. Every in-play item also needs a name and short
description. For normal play, do not use `character update` to patch one field;
it replaces an entire supplied document and is only for a reviewed full-card
revision. A campaign actor copied from any public template is independent: mutations
and saves affect the instance, never the template. `character build` is the standard
PC car-creation operation and creates both in one transaction.

For the complete v2 contract, read `references/database-contract.md` and
`../../references/character-schema-v2.md`. During play, mutate only the affected
state through granular commands:

```powershell
sagasmith-dnd character inventory add --id <id> --payload '<item-json>' --json
sagasmith-dnd character inventory update --id <id> --item <item-id> --payload '<item-json>' --json
sagasmith-dnd character inventory remove --id <id> --item <item-id> --amount <n> --json
sagasmith-dnd character inventory use-ammunition --id <id> --item <weapon-item-id> --amount <n> --json
sagasmith-dnd character inventory transfer --id <source> --target <target> --item <item-id> --json
sagasmith-dnd character wallet credit --id <id> --denomination gp --amount 10 --json
sagasmith-dnd character wallet debit --id <id> --denomination gp --amount 10 --json
sagasmith-dnd character equipment equip --id <id> --item <item-id> --slot-name main_hand --json
sagasmith-dnd character equipment unequip --id <id> --item <item-id> --json
sagasmith-dnd character spell prepare --id <id> --spell <spell-id> --json
sagasmith-dnd character spell unprepare --id <id> --spell <spell-id> --json
sagasmith-dnd character effect add --id <id> --payload '<effect-json>' --json
sagasmith-dnd character effect remove --id <id> --effect <effect-id> --json
sagasmith-dnd character resource set --id <id> --resource <resource-key> --amount <n> --json
sagasmith-dnd character memory add --id <npc-id> --payload '<memory-json>' --json
sagasmith-dnd character memory resolve --id <npc-id> --memory-id <memory-id> --json
sagasmith-dnd party inventory deposit --campaign <id> --id <character-id> --item <item-id> --json
sagasmith-dnd party inventory withdraw --campaign <id> --id <character-id> --item <item-id> --json
sagasmith-dnd party wallet deposit --campaign <id> --id <character-id> --denomination gp --amount 10 --json
sagasmith-dnd party wallet withdraw --campaign <id> --id <character-id> --denomination gp --amount 10 --json
```

For new cards, use the structured identity, background grants, weapon/ammunition,
container capacity, encumbrance, expanded senses, spell definition, concentration,
and adventure-state fields described in the v2 contract. After item changes,
inspect `character show` and use `derived.inventory.weapon_attacks` and
`derived.inventory.encumbrance` as the authoritative combat-card and load view.

## Saves

```powershell
sagasmith-dnd save create --campaign <id> --label "<label>" --json
sagasmith-dnd save list --campaign <id> --json
sagasmith-dnd save verify --campaign <id> --slot <n> --json
sagasmith-dnd save lineage --campaign <id> --json
sagasmith-dnd save restore --campaign <id> --slot <n> --json
```

Restore automatically preserves the current state and creates a new branch. Never
describe restore as overwriting history. A v2 snapshot restores the campaign,
every actor document, party stash, scene progress, chronology events, active memory
content, and its undo/redo cursor; rules and module source text remain external.

## Continuity

```powershell
sagasmith-dnd event list --campaign <id> --limit 30 --json
sagasmith-dnd memory search --campaign <id> --query "<question>" --limit 8 --json
sagasmith-dnd state history --campaign <id> --limit 30 --json
```

Use memory for durable facts and events for chronology. Use `character memory`
for facts that belong to one NPC or monster, and `character show` after any actor
or party mutation to refresh the authoritative card and `derived` values.

Use `state undo` and `state redo` for audited mutations; these do not delete
snapshots. Keep player-to-character assignments in campaign state so they remain
portable across agent platforms.
