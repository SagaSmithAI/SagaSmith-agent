# 🐉 SagaSmith Agent

[中文](README.md) | [English](README-en.md)

<p align="center"><img src="images/Sagasmith.png" alt="SagaSmith" width="200"></p>

**Autonomous AI Dungeon Master runtime** — built on [NanoBot](https://github.com/HKUDS/nanobot), with full D&D 5e DM capabilities.

> *"The rulebooks are scripture, the module is the map, the dice are the judge."*  
> — Minthara Baenre, SagaSmith default DM

SagaSmith Agent is a complete, runnable AI DM system. Connect QQ (NapCat), Telegram, or WebSocket — players send messages in chat, the DM responds. Backed by a SQLite/PostgreSQL campaign database, ChromaDB vector store (optional), BGE-M3 rule search engine, d20 combat engine, and a Lawful Evil drow DM persona.

---

## Ecosystem

| Repo | Role |
|------|------|
| 🎲 **SagaSmith-agent** (this repo) | Complete AI DM runtime |
| 📦 [SagaSmith-skills](https://github.com/dajiaohuang/SagaSmith-skills) | Skill plugin pack |
| ✍️ [SagaSmith-module-gen-skills](https://github.com/dajiaohuang/SagaSmith-module-gen-skills) | Standalone module generator |

---

## Why SagaSmith

Most D&D AI tools do one thing: roll dice, look up a rule, or write a paragraph. SagaSmith is the **full DM**:

| System | Description |
|--------|-------------|
| 🎲 **Rule Engine** | BGE-M3 Dense Vector over 8,000+ SRD chunks across 3 editions. 3-layer hybrid (exact + FTS + semantic). ChromaDB HNSW when configured, numpy/pgvector fallback. Auto-ingest on first use. |
| ⚔️ **Combat Engine** | True d20 rolls, initiative/hit/damage/save/crit, turn tracking, XP |
| 🏛️ **Campaign Management** | Bronze Dragon's Timeline Corrector — SQLite/PostgreSQL-backed, DAG save tree (branch-aware loading), Snapshot save/load/verify, campaign memory (per-branch revision model), ChromaDB vector semantic search, event log, module progress tracking |
| 📖 **Module Mgmt** | PDF/HTML/DOCX import, structure-aware chunking, scene index, Dense retrieval |
| 🧠 **Campaign Memory** | Branch-aware long-term memory — stable fact identities, independent revisions per save branch. Natural language query, exact effective memory calculation along DAG ancestor path, no cross-branch leakage. |
| 🎭 **Minthara Persona** | Lawful Evil DM, 2024-rules absolutist, cold wit, never leaks hidden info |
| 💬 **Multi-Channel** | 16 chat platforms (QQ/Telegram/Discord/Slack/Feishu/WhatsApp/Matrix/Signal...) |

---

## Usage Examples

Load SagaSmith into any compatible agent. The 8 phases below cover the full lifecycle.

### 1. Rulebook Import

```
User: Import SRD rulebooks

Agent:
📚 Parsing SRD 5.2.1 (20 files)...
   Chunks: 2,847 · BGE-M3 embeddings: 2,847 (1024-dim)
   Books: PHB (2024) · DMG (2024) · Monster Manual (2025)
   ✅ Index ready
```

### 2. Module Import

PDF/DOCX/PPTX/XLSX → MarkItDown. PDF uses dedicated structural parser:

```
User: [Upload Baldurs_Gate_Avernus.pdf]

Agent:
📄 Parsing — bookmarks: 42 anchors · CJK reflow · TOC filtered
📦 Baldur's Gate: Descent into Avernus (BGDIA)
   5 chapters · 62 scenes · 847 chunks · 847 vectors · ✅ ready
```

### 3. Campaign Start

```
User: Start a new campaign with BGDIA

Agent:
🎲 Campaign Setup
   ✅ Created: campaign_7a3f
   ✅ Rules bound: 2024 Core
   ✅ Module bound: BGDIA Ch.1 unlocked
   ✅ Initial snapshot: slot 1 "Initial State"

   🎉 Campaign ready! Create characters or say "random roll."
```

### 4. Character Creation

```
User: Build me a half-elf ranger

Agent:
📜 Character Creation — Half-Elf Ranger
   1️⃣ Race: Half-Elf (+2 CHA, Darkvision)
   2️⃣ Class: Ranger (d10, Favored Enemy, Natural Explorer)
   3️⃣ Abilities: DEX 16 · CON 14 · WIS 14 (27 point-buy)

User: Confirm

Agent:
✅ Varis Moonshadow — Half-Elf Ranger 1 · Chaotic Good
   HP 12 · AC 14 · Longbow (1d8) · Saved to database
```

### 5. Module Generation

```
User: Generate a sewer side quest for this campaign

Agent:
✅ Generated: The Sewer Shadows (One-shot · Five-Room Dungeon)
   S1 Entrance Guard · S2 Skill Challenge (DC 12) · S3 Twist · S4 Boss · S5 Reward
   📝 modules/sewer_shadows.md · ✅ Imported: 5 scenes, 47 chunks
```

### 6. DM Gameplay

```
Agent:
【Time: 1492 DR, dusk】【Location: Waterdeep · The Elfsong Tavern】

What do you do?

User: Approach the hooded figure

Agent:
🎲 A drow woman — Zara Do'Urden, Flaming Fist captain.
"Three murders. Temple District. Go investigate."

【Clue: Death Three activity map】【Quest: Investigate murders】

User: Ask the innkeeper

Agent:
🎲 Alanna: "Back alley... a figure whose shadow had no fingers."
【Clue: Fingerless shadow】

⚔️ 🅰️ Alley tonight  🅱️ Rest, morning  🅲️ HQ archives

User: 🅱️

Agent:
🌙 Long rest complete. 📁 Auto-saved [slot 2: "After Elfsong rest"]
```

### 7. Rules Q&A

```
User: Frost Giant AC?

Agent: 🔍 Frost Giant · CR 8 · AC 15 · Cold Immune · Multiattack (2×Greataxe +12, 3d12+6)
       Source: Monster Manual (2025) p.187


User: Paladin 5 spell slots?

Agent: 🔍 1st: 4 · 2nd: 2. Spells known: CHA mod + ½ level.
       Source: Player's Handbook (2024) Ch.3


User: Fireball?

Agent: 🔥 Fireball · 3rd-level · 150ft · 20ft radius · 8d6 fire · DEX save half
       Source: Player's Handbook (2024) Ch.11
```

### 8. Campaign Management

```
User: List campaigns

Agent:
📋 campaign_7a3f  BGDIA         Ch.1  Lv2  Saves: 2
   campaign_b21   Icewind Dale  Ch.3  Lv5  Saves: 7

User: Switch to Icewind Dale

Agent: ✅ Switched · 📍 Ch.3 Scene 4

User: Save

Agent: ✅ slot 8 "Before Frost Giant's Gate" · 🔑 a3f2c...

User: Load slot 5

Agent: ⚠️ Auto-saving current → ⏪ Restored slot 5
   ✅ World / Party / Combat / Plot / Events — all restored
```

## Saves, Recaps, and Memory

SagaSmith keeps three commonly confused kinds of data separate:

| Data | Scope | Storage | Purpose |
|------|-------|---------|---------|
| Agent session memory | Current chat/session | NanoBot session history and compacted summaries | Maintains recent conversational continuity |
| Snapshot | One campaign and save point | `campaign_saves.snapshot_json` | Stores authoritative, restorable campaign state |
| Campaign memory | One campaign and active save branch | `campaign_memories` + `campaign_memory_revisions` | Stores long-lived facts that can evolve independently on each branch |

Creating a save requires only a short tool call:

```text
dnd_save action=create campaign_id=<id> label="Before entering the dungeon"
```

The tool captures the current world, party, PCs, combat, plot, events, scenes, and channel bindings. It then generates a recap relative to the parent save, stores that recap in the snapshot, and derives long-term campaign memories from `memory_candidates` and `future_impact`. High-priority facts become `permanent`, medium-priority facts become `candidate`, and low-priority facts remain only in the recap.

Every save records a `parent_save_id`, forming a DAG. Restoring a save moves the active head to that node; the next save creates a new child branch. `campaign_memories` stores stable fact identities, while `campaign_memory_revisions` stores each save's text, priority, and status. A query walks only the root-to-target ancestor path and selects the nearest revision, so memories from sibling branches cannot leak into the result.

Inspect the DAG or ask campaign-memory questions in natural language:

```text
dnd_save action=lineage campaign_id=<id>
dnd_memory action=scope campaign_id=<id>
dnd_memory action=search campaign_id=<id> query="What is Mira's current relationship with the party?"
dnd_memory action=search campaign_id=<id> slot=3 query="Who knew where the secret door was at this save?"
```

When ChromaDB is enabled, it holds the semantic index for memory revisions. The relational database still computes the exact DAG ancestor path and effective revision IDs before constraining the Chroma candidate set. ChromaDB neither chooses the branch nor acts as the authoritative save store.

Restoring a save uses:

```text
dnd_save action=restore campaign_id=<id> slot=3
```

`restore` defaults to `auto_save=true`: the code first creates an `auto-before-restore` snapshot, then restores the requested slot. This pre-restore backup is enforced by code. Saves after a long rest, level-up, or chapter transition are currently skill-driven instructions for the Agent to call `dnd_save action=create`; they are not hard-wired database events or timers.

Save and restore operations modify campaign state, snapshots, recaps, and campaign memory in the database. They do not rewrite workspace files or `USER.md`. Only an explicit `action=export` writes a JSON file to the requested path.

This memory schema is intentionally breaking: migration rebuilds the legacy `campaign_memories` table and does not import legacy mutable memory rows.

---

## Supported Platforms

SagaSmith connects to major chat platforms via channels. **DM** responds directly; **Group** defaults to requiring @mention (configurable to open mode).

| Platform | Channel | DM | Group Policy | Notes |
|----------|---------|:--:|--------------|-------|
| Telegram | [telegram.py](nanobot/channels/telegram.py) | ✅ | mention (configurable to open) | Streaming, inline keyboards |
| Discord | [discord.py](nanobot/channels/discord.py) | ✅ | mention | Webhook push |
| Slack | [slack.py](nanobot/channels/slack.py) | ✅ | mention | |
| Feishu/Lark | [feishu.py](nanobot/channels/feishu.py) | ✅ | mention | Emoji reaction support |
| QQ (Napcat) | [napcat.py](nanobot/channels/napcat.py) | ✅ | mention / open | OneBot v11, WebSocket |
| QQ (Bot) | [qq.py](nanobot/channels/qq.py) | ✅ | mention | botpy SDK |
| WeCom | [wecom.py](nanobot/channels/wecom.py) | ✅ | mention | |
| WeChat (Personal) | [weixin.py](nanobot/channels/weixin.py) | ✅ | — | HTTP long-poll |
| WhatsApp | [whatsapp.py](nanobot/channels/whatsapp.py) | ✅ | mention (configurable to open) | Bridge WebSocket |
| Signal | [signal.py](nanobot/channels/signal.py) | ✅ | allowlist + mention | DM and group support |
| Matrix | [matrix.py](nanobot/channels/matrix.py) | ✅ | mention | |
| DingTalk | [dingtalk.py](nanobot/channels/dingtalk.py) | ✅ | — | |
| MoChat | [mochat.py](nanobot/channels/mochat.py) | ✅ | — | |
| MS Teams | [msteams.py](nanobot/channels/msteams.py) | ✅ | — | |
| Email | [email.py](nanobot/channels/email.py) | ✅ | — | IMAP/SMTP |
| WebSocket | [websocket.py](nanobot/channels/websocket.py) | ✅ | — | Per-connection session, Token auth |
| WebUI | — | ✅ | — | Built-in web interface, WebSocket |

**Group Policy Notes:**
- `mention`: Requires @bot or reply to bot message
- `open`: Responds to all messages (may cause noise)
- DM has no restrictions; unauthorized users receive a pairing code

---

## Quick Start

```powershell
# 1. Install (uv-managed)
uv sync

# 2. Initialize workspace + auto-discover platforms
uv run nanobot onboard --wizard

# 3. SRD auto-ingested on first rules access — no manual CLI needed
#    Optional: pre-ingest
uv run python -m nanobot.dnd.db.cli rules ingest-srd

# 4. (Optional) Enable ChromaDB for fast vector search
$env:CHROMA_DB_PATH = "$env:APPDATA\nanobot\dnd\chroma_db"

# 5. Launch gateway + QQ
.\scripts\start-all.bat
```

WebUI at `http://127.0.0.1:18765`.

---

## Rule Sets

Three rule sets bundled and auto-ingested on first rules access (lazy, no manual CLI):

| Rule Set ID | Edition | Locale | Chunks | Source |
|---|---|---|---|---|
| `dnd5e-2024-srd-5.2.1` | 2024 | EN | 2,684 | Bundled SRD 5.2.1 |
| `dnd5e-2014-srd-5.1-en` | 2014 | EN | 3,524 | Bundled SRD 5.1 |
| `dnd5e-2014-srd-5.1-zh-v2` | 2014 | ZH-CN | ~2,000 | Bundled Chinese translation |

With ChromaDB configured (`CHROMA_DB_PATH` or `CHROMA_DB_URL`), vectors are stored via HNSW indexing.

---

## Skill Breakdown

| Skill | SKILL.md | Role |
|-------|----------|------|
| 🎲 **dnd-dm** | [SKILL.md](skills/dnd-dm/SKILL.md) | Core DM persona (always-on), rule adjudication, combat engine, SRD retrieval (adapted from [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill)) |
| 📋 **dnd-campaign-manager** | [SKILL.md](skills/dnd-campaign-manager/SKILL.md) | Campaign lifecycle, Snapshot save/load, module import, USER.md sync |
| ✍️ **dnd-module-gen** | [SKILL.md](skills/dnd-module-gen/SKILL.md) | Module generation: one-shot → short → medium → long → sandbox, 25 paradigms |

### Module Generation Paradigms

| Type | Default Paradigms | Output |
|------|-------------------|--------|
| One-shot | Five-Room Dungeon, Heist, Mystery | 1 chapter, 3-6h |
| Short | Three-Act, Kishōtenketsu, Race Against Time | 3 chapters, 3-8 sessions |
| Medium | Hero's Journey, Plot Point, Faction Turn | 5 chapters, 2-4 months |
| Long | Double Triangle, Conspyramid, Megadungeon | 8 chapters, 6+ months |
| Sandbox | Hexcrawl, Node-Based, Blorb | 4-6 regions, open-ended |

---

## DM Persona: Minthara Baenre

A Lawful Evil DM based on the iconic Baldur's Gate 3 character:

- **Rules Absolutism** — Strict 2024 rulebook adjudication. Dice are final.
- **Cold Wit** — Points out tactical errors, then offers a barbed but viable alternative.
- **Info Boundary** — Never reveals DCs, hidden monster stats, undiscovered rooms, or future plot.
- **Player Agency** — Never decides for the players, never fudges dice for drama.

Default campaign: *Baldur's Gate: Descent into Avernus*. Adaptable to any adventure via module import.

---

## Architecture

```
QQ / Telegram / Discord / Slack / Feishu / WhatsApp / Matrix ...
        │
        ▼
NanoBot Runtime  (Provider · Agent Loop · Session · Memory · 19 Channels)
        │
        ▼
D&D Adapter       (dnd_rules search · dnd-engine calc · Campaign DB · Memory Search)
        │
        ├── SQLite / PostgreSQL  (Rule index · Campaign state · Snapshot DAG · Memory revisions)
        └── ChromaDB (optional)   (HNSW vector index · dnd_rules + dnd_memories collections)
```

---

## Context Management

| Mechanism | Description |
|-----------|-------------|
| Session JSONL | Real-time conversation log |
| Auto-Compact | Triggers at 30% token budget |
| Dream (every 2h) | Long-term memory summary → MEMORY.md |

---

## Project Structure

```
SagaSmith-agent/
├── nanobot/                   # Agent runtime
│   ├── agent/                 #   Agent Loop · Context · Memory · Runner
│   ├── channels/              #   19 platforms (QQ/Telegram/Discord/...)
│   ├── dnd/                   #   D&D adapter (rules · db · engine · modules)
│   ├── skills/                #   dnd-dm · dnd-campaign-manager · napcat-qq
│   └── templates/             #   System prompt templates (identity · SOUL · platform)
├── scripts/                   # Launch scripts
│   ├── start-all.bat          #   One-click launch (uv-managed)
│   └── install.ps1            #   Setup script
├── tests/                     # Tests
└── pyproject.toml             # uv project config
```

---

## Dependencies

| Dependency | Purpose |
|------------|---------|
| Python 3.11+ | Domain runtime |
| SQLAlchemy | Database ORM |
| FlagEmbedding | BGE-M3 Dense Vector retrieval |
| markitdown | PDF / DOCX module import |

---

## Credits

- [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill) — D&D DM skill pioneer, inspiration and design reference for SagaSmith
- [NanoBot](https://github.com/HKUDS/nanobot) — Lightweight agent framework
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — SKILL.md ecosystem standard driver
- D&D 5e SRD 5.2.1 © Wizards of the Coast, used under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
- [SagiriWWW/DND.SRD.zh-CN](https://github.com/SagiriWWW/DND.SRD.zh-CN) — D&D 5e SRD 5.1 Chinese translation ([CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/))

---

## License

- Code: MIT
- SRD 5.2.1 data files: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
