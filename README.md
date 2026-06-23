# 🐉 SagaSmith Agent

[English](README.md) | [中文](README-cn.md)

**Autonomous AI Dungeon Master runtime** — built on [NanoBot](https://github.com/HKUDS/nanobot), with full D&D 5e DM capabilities.

> *"The rulebooks are scripture, the module is the map, the dice are the judge."*  
> — Minthara Baenre, SagaSmith default DM

SagaSmith Agent is a complete, runnable AI DM system. Connect QQ (NapCat), Telegram, or WebSocket — players send messages in chat, the DM responds. Backed by a SQLite/PostgreSQL campaign database, ChromaDB vector store (optional), BGE-M3 rule search engine, d20 combat engine, and a Lawful Evil drow DM persona.

---

## Ecosystem

| Repo | Role |
|------|------|
| 🎲 **SagaSmith-agent** (this repo) | Full AI DM runtime |
| 📦 [SagaSmith-skills](https://github.com/dajiaohuang/SagaSmith-skills) | Cross-platform skill pack (NanoBot / OpenClaw / Hermes) |
| ✍️ [SagaSmith-module-gen-skills](https://github.com/dajiaohuang/SagaSmith-module-gen-skills) | Standalone module generator (pure Markdown skill) |

---

## Capabilities

| System | Description |
|--------|-------------|
| 🎲 **Rule Engine** | BGE-M3 Dense Vector over 8,000+ SRD chunks across 3 editions. 3-layer hybrid (exact + FTS + semantic). ChromaDB HNSW when configured, numpy/pgvector fallback. Auto-ingest on first use. |
| ⚔️ **Combat Engine** | True d20 rolls, initiative/hit/damage/save/crit, turn tracking, XP |
| 🏛️ **Campaign DB** | SQLAlchemy ORM + Alembic migrations, full CRUD, Snapshot save/load/verify/undo. Per-campaign rule set + extension binding. ChromaDB optional vector store. |
| 📖 **Module Mgmt** | PDF/HTML/DOCX import, structure-aware chunking, scene index, Dense retrieval |
| 🎭 **Minthara Persona** | Lawful Evil DM, 2024-rules absolutist, cold wit, never leaks hidden info |
| 💬 **Multi-Channel** | QQ (NapCat OneBot v11), Telegram, WebSocket, WebUI |

### Built-in Skills

| Skill | Role |
|-------|------|
| 🎲 **dnd-dm** | Core DM persona (always-on), rule adjudication, combat engine, SRD retrieval (adapted from [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill)) |
| 📋 **dnd-campaign-manager** | Campaign lifecycle, Snapshot save/load/verify/undo |
| ✍️ **dnd-module-gen** | Module generation (5 types × 25 paradigms), multi-step + sandbox |

### Supported Platforms

19 chat platforms built in. **Group-chat platforms default to mention-only** to avoid noise:

| Platform | Type | Default Policy | Setup |
|----------|------|---------------|-------|
| 🐱 **Napcat (QQ)** | Group | **Mention-only** | `nanobot onboard` |
| 📬 **Telegram** | Group | **Mention-only** | `nanobot onboard` |
| 🎮 **Discord** | Group | **Mention-only** | `nanobot onboard` |
| 💬 **Slack** | Group | **Mention-only** | `nanobot onboard` |
| 🐦 **Feishu/Lark** | Group | **Mention-only** | `nanobot onboard` |
| 📱 **WhatsApp** | Group | **Mention-only** | `nanobot onboard` |
| 🔗 **Matrix** | Group | **Mention-only** | `nanobot onboard` |
| 📶 **Signal** | Group | **Mention-only** | `nanobot onboard` |
| 💼 **Mochat** | Group | **Mention-only** | `nanobot onboard` |
| 📋 **MSTeams** | Group | **Mention-only** | `nanobot onboard` |
| 🐧 **QQ** | Group | @-only | `nanobot onboard` |
| 📌 **DingTalk** | Group | All messages | `nanobot onboard` |
| 🟢 **WeCom** | Group | All messages | `nanobot onboard` |
| 💚 **WeChat** | Group | All messages | `nanobot onboard` |
| 📧 **Email** | DM | N/A | `nanobot onboard` |
| 🔌 **WebSocket** | General | N/A | `nanobot onboard` |

```powershell
# First-run: auto-discovers all platforms, writes default config
nanobot onboard

# Or use the interactive wizard
nanobot onboard --wizard
```

To allow a group to receive all messages, set in `~/.nanobot/config.json`:
```json
{
  "channels": {
    "napcat": { "group_policy": "open" },
    "telegram": { "group_policy": "open" }
  }
}
```

---

## Usage Examples

A complete walkthrough from zero to running a D&D session. Each phase shows both conversational and CLI interaction modes.

### 1. Rulebook Import

Three rule sets are bundled and **auto-ingested** on first rules access (lazy, no manual CLI needed):

| Rule Set ID | Edition | Locale | Chunks | Source |
|---|---|---|---|---|
| `dnd5e-2024-srd-5.2.1` | 2024 | EN | 2,684 | Bundled SRD 5.2.1 |
| `dnd5e-2014-srd-5.1-en` | 2014 | EN | 3,524 | Bundled SRD 5.1 |
| `dnd5e-2014-srd-5.1-zh-v2` | 2014 | ZH-CN | ~2,000 | Bundled Chinese translation |

With ChromaDB configured (`CHROMA_DB_PATH` or `CHROMA_DB_URL`), vectors are stored via HNSW indexing. Without ChromaDB, chunks are stored without embeddings (lexical search still works).

```powershell
# Manual CLI (maintenance fallback)
uv run python -m nanobot.dnd.db.cli rules ingest-srd
uv run python -m nanobot.dnd.db.cli rules status
uv run python -m nanobot.dnd.db.cli vector status   # ChromaDB state
```

### 2. Module Import

Supports Markdown, PDF, DOCX, PPTX, XLSX. PDF uses a dedicated structural parser (bookmark recovery + CJK reflow).

```
User: Import the Baldur's Gate module

Agent:
📄 Importing...
   ✅ Bookmarks recovered: 42 chapter/appendix anchors
   ✅ TOC filtered: 8 entries removed
   ✅ Chapters: 5 · Scenes: 62 · Chunks: 847

📦 Done: Baldur's Gate: Descent into Avernus (BGDIA)
   Ch.1 A Tale of Two Cities · Ch.2 Dead Three Dungeon
   Ch.3 Vanthampur Villa · Ch.4 Candlekeep · Ch.5 The Cathedral
   Embeddings: 847 (BGE-M3) · Status: ready
```

Or via CLI:

```powershell
uv run python -m nanobot.dnd.db.cli module import --campaign <id> --path "D:\modules\bgdia\" --name "BGDIA"
uv run python -m nanobot.dnd.db.cli module index --campaign <id>
```

### 3. Campaign Start

Campaigns can choose which rule set and extensions to use:

```
User: Start a new campaign

Agent:
🎲 Campaign Setup

1️⃣ Rule sets available:
   · dnd5e-2024-srd-5.2.1 — D&D 5e 2024 / SRD 5.2.1 (EN, recommended)
   · dnd5e-2014-srd-5.1-en — D&D 5e 2014 / SRD 5.1 (EN)
   · dnd5e-2014-srd-5.1-zh-v2 — D&D 5e 2014 / SRD 5.1 (中文)
   Which edition?

2️⃣ Module source — found in database:
   · BGDIA (5 chapters / 62 scenes)
   Use existing or import new?

User: 2024, BGDIA, 2 players

Agent:
⚔️ One-shot start
   ✅ Campaign created: campaign_7a3f
   ✅ Rules bound: D&D 5e 2024 / SRD 5.2.1 [en]
   ✅ Extensions: core rulebook only
   ✅ Module bound: BGDIA · Ch.1 unlocked
   ✅ Initial snapshot: slot 1 "Initial State"

   🎉 Campaign ready!
```

The `dnd_campaign start` action accepts `rule_set_id`, `publication_ids`, and `locale` — omit them to auto-select the most recent active rule set with core publications only.

### 4. Character Creation

```
User: Build me a half-elf ranger

Agent:
📜 Character Creation — Half-Elf Ranger

1️⃣ Race: Half-Elf (+2 CHA, two others +1, Darkvision 60ft)
2️⃣ Class: Ranger (d10, light/medium armor, Favored Enemy, Natural Explorer)
3️⃣ Abilities (27 point-buy): DEX 16 · CON 14 · WIS 14

Confirm to proceed...

User: Confirm

Agent:
✅ Varis Moonshadow — Half-Elf Ranger 1 · Chaotic Good
   HP 12 · AC 14 · Longbow (1d8) · Shortsword (1d6)
   Proficient: Perception +4 · Stealth +5 · Nature +2 · Survival +4
   Saved to database

📦 Current state [slot 1:]
   Campaign: BGDIA · Ch.1 · Waterdeep · Party: Varis (1)
```

### 5. Module Generation (in-campaign side quest)

```
User: Generate a side quest — ratfolk nest in the sewers

Agent:
✅ Generated side quest: The Sewer Shadows
   Type: One-shot (Five-Room Dungeon)
   CR: suitable for level 2

   Structure:
   S1 — Sewer entrance (Guard: Ratfolk Sentries ×2, CR 1/2)
   S2 — Slippery tunnels (Skill challenge: DC 12 Acrobatics over sewage)
   S3 — Forgotten shrine (Twist: ratfolk are escaped slaves, not enemies)
   S4 — Hatchery boss fight (Ratfolk King CR 2 + Swarm ×3)
   S5 — Rescued scholar (Reward: sewer map + 100GP + 1 magic item)

   ✅ Written to modules/sewer_shadows.md
   ✅ Imported: 5 scenes · 47 chunks

📜 Side quest ready to trigger naturally in the current chapter.
```

### 6. DM Gameplay

```
Agent:
【Time: 1492 DR, dusk】
【Location: Waterdeep · The Elfsong Tavern】

The smell of ale and roast meat hangs in the air. The innkeeper,
Alanna, nods at you from behind the bar. A hooded figure sits
by the window, watching the door.

What do you do?

User: Approach the hooded figure

Agent:
🎲 Beneath the hood — a scarred drow woman, hand resting on her sword.

"Another hopeful fool," she says coldly. "Sit."

Zara Do'Urden, captain of the Flaming Fist. She spreads out a map
marked with recent cult activity.

"Three murders. Temple District. Witnesses say black robes, Bhaal masks.
Go investigate."

【Clue: Death Three cult activity map】
【Quest: Investigate Temple District murders】

User: Ask the innkeeper if she saw anything

Agent:
🎲 Alanna stiffens at the words "Temple District."

She lowers her voice: "Last night, the back alley. A figure...
its shadow was wrong. When torchlight hit it, the shadow on the wall
had no fingers."

She refuses to say more — clearly terrified.

【Clue: Back alley sighting (fingerless shadow)】

⚔️ Your options:
🅰️ Investigate the back alley tonight
🅱️ Rest at the inn, visit witnesses in the morning
🅲️ Check Flaming Fist headquarters first

User: 🅱️

Agent:
🌙 Long rest complete. HP/spell slots restored.

📦 Campaign status — Ch.1 · Waterdeep · Day 2, morning
   Varis Moonshadow (1) · Roland Lightbringer (1)
   Clues: cult map, fingerless shadow
   📁 Auto-saved [slot 2: "After Elfsong rest"]
```

### 7. Rules Q&A

Ask D&D rules questions directly. The agent searches SRD via BGE-M3 Dense Vector retrieval:

```
User: What's a Frost Giant's AC? Special traits?

Agent:
🔍 Searching SRD 5.2.1 Monster Manual (2025)...
   Frost Giant · CR 8 · HP 138(12d12+60) · AC 15 (patchwork armor)
   Traits: Cold Immunity · Multiattack (2×Greataxe, +12, 3d12+6)
   Source: Monster Manual (2025) p.187-188


User: How many spell slots does a level 5 Paladin have?

Agent:
🔍 Searching Player's Handbook (2024)...
   📖 Paladin Level 5
   1st-level: 4 · 2nd-level: 2
   Spells known: CHA mod + ½ Paladin level (rounded down)
   Source: Player's Handbook (2024) Ch.3


User: How does Darkvision work in combat?

Agent:
🔍 Searching...
   🌑 Perception checks in darkness have disadvantage.
   Cannot discern color — grayscale only.
   Attacking a target within darkvision range does NOT
   suffer disadvantage from lighting (though allies might).
   Source: Player's Handbook (2024) Ch.1 · Rules Glossary


User: Fireball damage and save?

Agent:
🔍 Searching...
   🔥 Fireball · 3rd-level Evocation
   Range 150ft · 20ft radius · 8d6 fire damage
   DEX save (DC = caster's spell save DC) for half
   Source: Player's Handbook (2024) Ch.11
```

### 8. Campaign Management

```
User: List campaigns

Agent:
📋 Active campaigns
   campaign_7a3f  Baldur's Gate: Avernus  Ch.1  Lv2  Saves: 2
   campaign_b21   Icewind Dale            Ch.3  Lv5  Saves: 7

User: Switch to Icewind Dale

Agent:
✅ Switched to campaign_b21
   📍 Ch.3 Scene 4 · Bryn Shander · Last: yesterday

User: Save

Agent:
✅ Snapshot created
   📁 slot 8: "Before Frost Giant's Gate"
   📍 Ch.3 Scene 2 · Frost Cave entrance · 🔑 SHA-256: a3f2c...

User: List Icewind Dale saves

Agent:
📁 Snapshots (campaign_b21)
   slot 1 "Initial State"               creation
   slot 2 "Arrival at Bryn Shander"     Ch.1
   slot 3 "After Winter Wolf ambush"    Ch.2
  ┊
   slot 8 "Before Frost Giant's Gate"   Ch.3 ← current

User: Load slot 5

Agent:
⚠️ Auto-saving current state first (slot 9)
⏪ Restored slot 5 "Before Frost Giant's Gate Open"
   ✅ World · Party · Combat · Plot · Events — all restored
   📍 Ch.3 Scene 2 · Frost Giant's Gate
```

---

## Quick Start

```powershell
# 1. Install (uv-managed editable install)
uv sync

# 2. Initialize workspace + auto-discover platforms
uv run nanobot onboard --wizard

# 3. SRD auto-ingested on first rules access — no manual CLI needed
#    Optional: pre-ingest with uv run python -m nanobot.dnd.db.cli rules ingest-srd

# 4. (Optional) Enable ChromaDB for fast vector search
$env:CHROMA_DB_PATH = "$env:APPDATA\nanobot\dnd\chroma_db"

# 5. Launch gateway + QQ
.\scripts\start-all.bat
```

WebUI at `http://127.0.0.1:18765`.

---

## Channel Setup

### QQ (NapCat)

QQ connects via NapCat (OneBot v11 Forward WebSocket). First run auto-installs:

```powershell
# One-click launch (uv-managed)
.\scripts\start-all.bat
```

**Config** `~/.nanobot/config.json`:

```json
{
  "channels": {
    "napcat": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "allowFrom": ["<QQ number>"],
      "groupPolicy": "mention",
      "groupPolicyOverrides": {"<group ID>": "mention"}
    }
  }
}
```

### Telegram

```json
{
  "channels": {
    "telegram": { "enabled": true, "token": "<bot-token>" }
  }
}
```

### Format Policy

| Channel | Format |
|---------|--------|
| QQ (NapCat) | Plain text + emoji + `【】` emphasis, **no** markdown bold/italic |
| Telegram | Short paragraphs, sparse `**bold**` |
| WebUI / CLI | Full Markdown / plain text |

---

## Module Import

Supports Markdown, PDF, DOCX, PPTX, XLSX. PDF uses a dedicated structural parser (page-aware + bookmark recovery + CJK reflow + TOC filtering):

```powershell
uv run python -m nanobot.dnd.db.cli module import --campaign <id> --path "<module dir>" --name "Module Name"
```

Chunking: 1,200-char max, ≈100-char overlap, no cross-heading boundaries, page ranges preserved.

---

## Rule Search

8,000+ SRD chunks across 3 rule sets (2024 EN · 2014 EN · 2014 ZH), indexed with `BAAI/bge-m3` (1,024-dim) semantic vectors. ChromaDB HNSW when configured; numpy/pgvector fallback.

```powershell
uv run python -m nanobot.dnd.db.cli rules status
uv run python -m nanobot.dnd.db.cli rules search --campaign <id> --query "grapple escape" --top-k 5
uv run python -m nanobot.dnd.db.cli vector status   # ChromaDB collections
```

GPU acceleration: `$env:DND_EMBEDDING_DEVICE="cuda"`.

---

## Campaign Management

Database is the single source of truth. Snapshots cover campaign metadata, world, party, characters, combat, plot summary, and event log — module source documents and vectors are never duplicated. Each campaign pins a rule set and enabled publications (extensions), queried via `dnd_campaign show`.

```powershell
# Create with specific rule set
uv run python -m nanobot.dnd.db.cli campaign create --name "Baldur's Gate" --module "BGDIA" \
  --rule-set dnd5e-2024-srd-5.2.1

# Or with 2014 rules
uv run python -m nanobot.dnd.db.cli campaign create --name "Classic FR" \
  --rule-set dnd5e-2014-srd-5.1-en

uv run python -m nanobot.dnd.db.cli save create --campaign <id> --label "Initial State" --workspace "<path>"
uv run python -m nanobot.dnd.db.cli save list --campaign <id>
uv run python -m nanobot.dnd.db.cli save load --campaign <id> --slot <n> --workspace "<path>"
```

---

## Architecture

```
QQ / Telegram / Discord / Slack / Feishu / WhatsApp / Matrix ...
        │
        ▼
NanoBot Runtime  (Provider · Agent Loop · Session · Memory · 19 Channels)
        │
        ▼
D&D Adapter       (dnd_rules search · dnd-engine calc · Campaign DB)
        │
        ├── SQLite / PostgreSQL  (Rule index · Campaign state · Audit · Snapshots)
        └── ChromaDB (optional) (HNSW vector index · dnd_rules + dnd_modules collections)
```

### Rule Sets

```
nanobot/skills/dnd-dm/srd/
├── references/              D&D 5e 2024 SRD 5.2.1 (EN) — 20 files, 2,684 chunks
├── references-2014-en/      D&D 5e 2014 SRD 5.1 (EN) — 1,019 files, 3,524 chunks
└── references-2014-zh/      D&D 5e 2014 SRD 5.1 (ZH) — 300+ files, ~2,000 chunks
```

All three are auto-ingested on first `dnd_rules` access — no manual CLI needed.

### Project Structure

```
SagaSmith-agent/
├── nanobot/                   # Agent runtime
│   ├── agent/                 #   Agent Loop · Context · Memory · Runner
│   ├── channels/              #   napcat · telegram · websocket
│   ├── dnd/                   #   D&D adapter (rules · db · engine · modules)
│   ├── skills/                #   dnd-dm · dnd-campaign-manager · napcat-qq
│   └── templates/             #   System prompt templates (identity · SOUL · platform)
├── tools/napcat/              # NapCat + portable QQ runtime
├── scripts/                   # Setup & launch scripts
│   ├── install.sh / install.ps1  #   One-click install
│   └── start-all.bat          #   One-click launch (uv-managed)
└── tests/                     # Tests
```

---

## Context Management

| Mechanism | Description |
|-----------|-------------|
| Session JSONL | Real-time conversation log |
| Auto-Compact | Triggers at 30% token budget |
| Dream (every 2h) | Long-term memory summary → MEMORY.md |

---

## Credits

- [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill) — D&D DM skill pioneer, inspiration and design reference for SagaSmith
- [NanoBot](https://github.com/HKUDS/nanobot) — Lightweight agent framework
- D&D 5e SRD 5.2.1 © Wizards of the Coast ([CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/))
- [SagiriWWW/DND.SRD.zh-CN](https://github.com/SagiriWWW/DND.SRD.zh-CN) — D&D 5e SRD 5.1 Chinese translation ([CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/))
- D&D 5e SRD 5.1 (2014 English) — bundled Markdown edition

---

## License

MIT
