# D&D Character Schema v2

Runtime mode stores every PC, NPC, and monster as a `Character` record with the
same validated documents. `character_type` is `pc`, `npc`, or `monster`; it does
not change the required sheet shape.

## Authority

- `sheet` is the authoritative mechanical state.
- `notes` is authoritative narrative state: description, important memories,
  relationships, and goals.
- `derived` is returned by `character show`; it is calculated from the sheet and
  must never be edited directly.
- `campaign.state.party.inventory` is the authoritative shared wallet and stash.
- Every new sheet and notes document has `schema_version: 2`.

## Actor Cards

Every game entity that can take part in play is a complete `Character` record.
Do not keep a second, abbreviated combat card in campaign state or a prose note.

| Type | Required identity and narrative state | Mechanical expectations |
|---|---|---|
| PC | `player_name` when player-controlled; `notes.profile.summary` is required by this skill as the player-facing setting description | Full progression, abilities, skills, combat, traits, resources, spells, content, effects, and personal inventory. |
| NPC | `notes.profile.summary` is required; record lasting conversation outcomes in `notes.memories` | The same full sheet. Populate every value that can affect a check, save, combat, spell, resource, item, or effect. |
| Monster | `notes.profile.summary` is required and describes appearance/behavior | The same full sheet, including CR-relevant combat values, defenses, senses, movement, actions in `content.activities`, limited uses, spells, equipment, effects, and loot. |

The public character library may hold reusable PC, NPC, and monster templates.
Any type may instead be created directly as a campaign instance. For player
character creation, prefer `character build`: it atomically creates the public
template and an independent instance in the selected campaign. All live actors in
the same encounter must be read with `character show` and must use the campaign's
edition and rules profile.
Defaults are only placeholders while information is unknown. Once a rule source or
published stat block supplies a value, write the structured value rather than
leaving an inferred default.

```powershell
# Public library templates and direct campaign instances both support pc/npc/monster.
sagasmith-dnd character create --name "<name>" --type pc|npc|monster --sheet '@<sheet.json>' --notes '@<notes.json>' --json
sagasmith-dnd character library list --type pc|npc|monster --json
sagasmith-dnd character instantiate --id <library-character-id> --campaign <campaign-id> --player "<player>" --json
sagasmith-dnd character create --campaign <campaign-id> --name "<name>" --type pc|npc|monster --sheet '@<sheet.json>' --notes '@<notes.json>' --json

# Preferred character-creation flow: one transaction creates both records.
sagasmith-dnd character build --campaign <campaign-id> --name "<name>" --type pc --player "<player>" --sheet '@<pc-sheet.json>' --notes '@<pc-notes.json>' --json
```

Instances retain `template_id` for provenance but are independent copies. Gameplay
mutations and snapshots apply to the campaign instance only; they never alter or
restore the public library template.

## Sheet

`sheet` contains these required top-level blocks:

```json
{
  "schema_version": 2,
  "edition": "2014",
  "identity": {},
  "progression": {},
  "ability_generation": {},
  "abilities": {},
  "skills": {},
  "combat": {},
  "traits": {},
  "resources": {},
  "spellcasting": {},
  "content": {},
  "conditions": [],
  "effects": [],
  "adventure_state": {},
  "inventory": {}
}
```

`progression` records level, XP, classes, subclass, hit die, background, and
species, including `background_grants` for the background feature, starting item
IDs, language/tool grants, and selection choices. `identity` records gender, age,
height, weight, faith/deity, visible features, and an optional `portrait_uri`.
`combat` records current/max/temp HP, AC, initiative, all movement modes, hit
dice, `hp_progression` gains by level (`fixed|rolled|manual`), death saves,
exhaustion, inspiration, and an explicit wounded flag.
`traits.senses` always includes darkvision, blindsight, tremorsense, truesight,
and a passive-perception modifier. `resources` is a named pool with `value`,
`max`, `recovers_on`, and `source_key`.

## Skill Table

`sheet.skills` always contains the complete table below. Each entry is
`{ "proficiency": "none|half|proficient|expertise", "bonus": <integer> }`.
Set the proficiency from class, background, species, feats, and expertise; use
`bonus` only for a persistent additional modifier. Do not omit untrained skills.

| Ability | Skills |
|---|---|
| Strength | Athletics |
| Dexterity | Acrobatics, Sleight of Hand, Stealth |
| Intelligence | Arcana, History, Investigation, Nature, Religion |
| Wisdom | Animal Handling, Insight, Medicine, Perception, Survival |
| Charisma | Deception, Intimidation, Performance, Persuasion |

## Ability Generation

`ability_generation` records the pre-species/pre-advancement assignment that
created the card. The default `method: "unrecorded"` is only for existing cards;
new 2014 car creation must use one of these code-validated methods:

| Method | Runtime enforcement |
|---|---|
| `standard_array` | Uses exactly `15, 14, 13, 12, 10, 8` once each. |
| `point_buy` | Uses scores 8-15 and spends exactly 27 points using the 2014 cost table. |
| `roll_4d6_drop_lowest` | Requires six recorded 4d6 pools, each with its lowest die dropped, and assigns every resulting score once. |

```powershell
# Roll first, reveal all six pools, then let the player assign them.
sagasmith-dnd character ability roll --edition 2014 --json

# Apply a method to an existing template or campaign instance.
sagasmith-dnd character ability apply --id <id> --method standard_array --assignments '<six-ability-json>' --json
sagasmith-dnd character ability apply --id <id> --method point_buy --assignments '<six-ability-json>' --json
sagasmith-dnd character ability apply --id <id> --method roll_4d6_drop_lowest --rolls '<roll-output-array>' --assignments '<six-ability-json>' --json

# Preferred PC car creation: validate the generation method before the template and instance are created.
sagasmith-dnd character build --campaign <id> --name "<name>" --type pc --player "<player>" --ability-method point_buy --assignments '<six-ability-json>' --sheet '@<sheet.json>' --notes '@<notes.json>' --json
```

The recorded assignments are the creation baseline. Later species adjustments,
ASI/feat choices, magic, and other legal changes may make the current
`abilities.*.score` different; do not rewrite the creation record to hide them.

## Spells And Limited Features

`content.spells` records a spell's source, level, grant, and access flags:
`known`, `prepared`, `always_prepared`, `in_spellbook`, `ritual_available`, and
`at_will`. Its structured `definition` records school, casting time, range,
duration, concentration, V/S/M components, material cost/consumption, and concise
rule effect. `point_cost` supports spell-point variants. The authoritative daily choice is
`spellcasting.preparation.selected_spell_ids`.

- Clerics, druids, paladins, and prepared casters use `mode: "prepared"`.
- Wizards use spellbook membership separately from daily preparation.
- Known casters use `mode: "known"`; do not fabricate a prepared list.
- Always-prepared spells are returned as prepared without consuming a selection.
- `spellcasting.casting_economy` is `slots` or `spell_points`; spell-point
  casting requires the structured `spellcasting.spell_points` resource.
- `content.features`, `content.feats`, and `content.activities` use the same
  source/description/uses/choices shape plus `resource_key`, `activation`, and
  level `scaling` for limited class, racial, item, and feat capabilities. Record
  Action Surge, Rage, Channel Divinity, and comparable features here, not in prose.

## Inventory And Wallet

`inventory.wallet` is `{ "cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0 }`.
Balances are non-negative integers. Gems, trade bars, keys, and unusual currency
are items, not wallet balances.

Each item has a stable `id`, `name`, `kind`, `quantity`, `weight_oz`, `price_cp`,
short `description`, `source_key`, container/equipment state, identification,
attunement, condition, uses, charges, and type-specific `mechanics`.

For every item that exists in play, this skill requires a nonempty `name` and
short `description`; use `source_key` whenever it comes from a rule source. A
plain key, gem, trade bar, quest object, or monster drop is still an item with
an ID, quantity, ownership, and description, not prose hidden in an event.

Equipment slots are `armor`, `shield`, `main_hand`, `off_hand`, `head`, `neck`,
`cloak`, `gloves`, `boots`, `ring_1`, `ring_2`, `shoulders`, `back`, `chest`,
`wrists`, `waist`, and `legs`. The slot map and each item's
`equipped` / `equipped_slot` fields must agree. Use `character equipment equip`
or `unequip`; never set those fields through an inventory patch.

Armor and shields have strict mechanics:

```json
{
  "armor": {
    "base_ac": 14,
    "dexterity_mode": "max",
    "dexterity_max": 2,
    "magic_bonus": 0
  },
  "shield": { "ac_bonus": 2, "magic_bonus": 0 },
  "magic_item": { "ac_bonus": 1 }
}
```

Armor uses `dexterity_mode: "none"`, `"full"`, or `"max"`; `dexterity_max` is
required only for `"max"`. Armor may only occupy `armor`, shields only `shield`,
and rings must be `magic_item` records in a ring slot.

`derived.armor_class` is calculated in this order: explicit `combat.ac.override`;
otherwise armor or `combat.ac.base`; then shield, equipped magic-item AC bonuses,
and supported active effects. `derived.armor_class_breakdown` explains every
applied source. A supported effect change uses
`{ "path": "derived.armor_class", "mode": "add|override", "value": <integer> }`.
Other effect changes remain in `derived.unresolved_rules` for DM adjudication.

Containers are ordinary `kind: "container"` items. Items reference their parent
with `container_id`; containers cannot form cycles. Container mechanics record
`capacity_oz`, `weightless_contents`, and `extra_dimensional`, and the runtime
rejects direct contents that exceed capacity. Inventory encumbrance records
`mode: "standard|variant"` and currency-weight handling. `derived.inventory`
returns carried weight, 5e load thresholds/state, and weapon attack cards.

Weapon mechanics are structured, not free prose: category, melee/ranged attack
type, selected attack ability, damage formula/type, versatile formula, properties,
normal/long and thrown ranges, proficiency, magic bonus, and an optional linked
ammunition item ID. `derived.inventory.weapon_attacks` is the authoritative card
view for attack bonus and damage expression.

## Effects And Narrative

Effects record source, optional `source_spell_id`, active state, `concentration`,
a declared duration period/remaining count, structured changes, and a short
description. The runtime permits at most one active concentration effect. It lists
effect changes it cannot derive automatically in `derived.unresolved_rules`; the
DM must read the rules before narrating their result.

`adventure_state` records actor-scoped reputation, contributions, blessings,
wards, legendary boons, and durable status tags. Do not hide these campaign-facing
states in `dm_notes`.

`notes.profile.summary` is the one-paragraph public description. NPCs and monsters
both require it; a monster summary is its concise appearance/behavior description. Important
dialogue facts use `notes.memories`, with `kind`, `summary`, `importance` 1-5,
participants, optional source event, visibility, and status. Do not store every
line of dialogue as a memory.
`notes.profile.backstory` holds the longer character history; it complements, but
does not replace, the compact public `summary` and `appearance`.

## Mutation Commands

```powershell
sagasmith-dnd character show --id <id> --json
sagasmith-dnd character inventory add --id <id> --payload '<item-json>' --json
sagasmith-dnd character inventory update --id <id> --item <item-id> --payload '<item-json>' --json
sagasmith-dnd character inventory remove --id <id> --item <item-id> --amount <n> --json
sagasmith-dnd character inventory use-ammunition --id <id> --item <weapon-item-id> --amount <n> --json
sagasmith-dnd character inventory transfer --id <source> --target <target> --item <item-id> --amount <n> --json
sagasmith-dnd character wallet credit --id <id> --denomination gp --amount 10 --json
sagasmith-dnd character wallet debit --id <id> --denomination gp --amount 5 --json
sagasmith-dnd character wallet transfer --id <source> --target <target> --denomination gp --amount 5 --json
sagasmith-dnd character equipment equip --id <id> --item <item-id> --slot-name main_hand --json
sagasmith-dnd character equipment unequip --id <id> --item <item-id> --json
sagasmith-dnd character spell prepare --id <id> --spell <spell-id> --json
sagasmith-dnd character spell unprepare --id <id> --spell <spell-id> --json
sagasmith-dnd character effect add --id <id> --payload '<effect-json>' --json
sagasmith-dnd character effect remove --id <id> --effect <effect-id> --json
sagasmith-dnd character resource set --id <id> --resource <resource-key> --amount <n> --json
sagasmith-dnd character memory add --id <id> --payload '<memory-json>' --json
sagasmith-dnd character memory resolve --id <id> --memory-id <memory-id> --json
sagasmith-dnd party inventory deposit --campaign <id> --id <character-id> --item <item-id> --json
sagasmith-dnd party inventory withdraw --campaign <id> --id <character-id> --item <item-id> --json
sagasmith-dnd party wallet deposit --campaign <id> --id <character-id> --denomination gp --amount 5 --json
sagasmith-dnd party wallet withdraw --campaign <id> --id <character-id> --denomination gp --amount 5 --json
```

Use `character update --sheet/--notes` only for a reviewed complete draft or a
deliberate full-sheet change. Never hand-edit one inventory entry, wallet balance,
prepared spell, effect, or memory through a raw sheet replacement during play.
