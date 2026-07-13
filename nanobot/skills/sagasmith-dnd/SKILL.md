---
name: sagasmith-dnd-suite
description: "Cross-platform D&D 5e 2014/2024 game-master and campaign workflow using the SagaSmith JSON CLI."
version: 2.0.0
---

# SagaSmith D&D Suite

This repository is an Agent Skill, not a Python runtime. It operates
`sagasmith-dnd` through shell commands that always include `--json`.

## Startup

1. Run `sagasmith-dnd doctor --json`.
2. If it succeeds, run `sagasmith-dnd database upgrade --json` once before play,
   then use Runtime mode and load the relevant child Skill.
3. If the command is missing, use Portable mode: static SRD lookup and best-effort
   local-file guidance only.
4. Never claim that Portable mode provides Runtime transactions, validated v2 actor
   cards, granular state mutations, or SQL Snapshot semantics.

## Included Skills

- `skills/dnd-dm`: play, adjudication, rule/module retrieval, and narration.
- `skills/dnd-campaign-manager`: campaign, character, save, and memory lifecycle.

Module generation is maintained separately in `SagaSmith-module-gen-skills`.

## Invariants

- Keep the active `campaign_id`, edition, and locale explicit.
- Never mix 2014 and 2024 rules unless the user explicitly requests comparison.
- Search first, then expand only the selected rule or module chunk.
- Treat CLI stdout as one JSON envelope; logs belong to stderr.
- On `ok:false`, branch on `error.code`; do not infer success.
- Runtime character state uses `sheet v2` / `notes v2`; load
  `references/character-schema-v2.md` before creating or mutating a PC, NPC, or
  monster. All three are full `Character` records, not abbreviated stat blocks.
- Use granular `character` / `party` commands for inventory, wallet, equipment,
  prepared spells, effects, resources, and NPC memories. `character update` is
  reserved for a reviewed replacement of the complete `sheet` or `notes` document.
- `character build` is the preferred player-character creation workflow: it creates
  a public template and a separate initial campaign instance atomically.
- Do not load entire rulebooks or modules into context.

See `references/cli-contract.md` and `references/workflows.md`.
