# 🐉 SagaSmith Agent

[中文](README.md) | [English](README-en.md)

<p align="center"><img src="images/Sagasmith.png" alt="SagaSmith" width="200"></p>

**自主 AI 地下城主运行时** — 基于 [NanoBot](https://github.com/HKUDS/nanobot) 构建，具备完整 D&D 5e DM 能力。

> *"规则书为经文，模组为地图，骰子为审判官。"*  
> — 明萨拉·班瑞，SagaSmith 默认 DM

SagaSmith Agent 是一个完整的、可运行的 AI DM 系统。连接 QQ (NapCat)、Telegram 或 WebSocket——玩家在聊天中发送消息，DM 负责响应。后端由 SQLite/PostgreSQL 战役数据库、ChromaDB 向量库（可选）、BGE-M3 规则检索引擎、d20 战斗引擎，以及守序邪恶的卓尔 DM 人格驱动。

---

## 生态

| 仓库 | 定位 |
|------|------|
| 🎲 **SagaSmith-agent**（本仓库） | 完整 AI DM 运行时 |
| 📦 [SagaSmith-skills](https://github.com/dajiaohuang/SagaSmith-skills) | Skill 插件包 |
| ✍️ [SagaSmith-module-gen-skills](https://github.com/dajiaohuang/SagaSmith-module-gen-skills) | 独立模组生成器 |

---

## 为什么是 SagaSmith

大多数 D&D AI 工具只做一件事：掷骰、查规则、或者写一段描述。SagaSmith 是**完整的 DM**：

| 系统 | 描述 |
|------|------|
| 🎲 **规则引擎** | BGE-M3 Dense Vector 检索，8,000+ SRD 规则块，3 层混合搜索（精确 + FTS + 语义）。ChromaDB HNSW 加速，numpy/pgvector 降级方案。惰性自动摄入。 |
| ⚔️ **战斗引擎** | 真实 d20 掷骰、先攻/命中/伤害/豁免/暴击、回合追踪、XP 计算 |
| 🏛️ **战役管理** | 青铜龙的时间线修正器——SQLite/PostgreSQL 驱动，DAG 存档树（任意分支读档）、Snapshot 存档/读档/校验、战役记忆（per-branch revision 模型）、ChromaDB 向量语义搜索、事件日志、模组进度追踪 |
| 📖 **模组管理** | PDF/HTML/DOCX 导入、结构感知分块、场景索引、Dense 检索 |
| 🧠 **战役记忆** | 分支感知长期记忆——事实身份稳定，每个存档分支有独立 revision。自然语言查询，沿 DAG 祖先路径精准计算有效记忆，兄弟分支互不串线。 |
| 🎭 **明萨拉人格** | 守序邪恶 DM，2024 规则绝对主义，冷幽默，绝不泄露隐藏信息 |
| 💬 **多平台接入** | 16 个聊天平台（QQ/Telegram/Discord/Slack/飞书/WhatsApp/Matrix/Signal...）|

---

## 使用示例

加载 SagaSmith 到任意兼容 Agent 后，以下 8 个阶段展示完整生命周期。

### 1. 规则书导入

```
用户: 导入 SRD 规则书

Agent:
📚 解析 SRD 5.2.1 (20 文件)...
   规则块: 2,847 · BGE-M3 嵌入: 2,847 (1024 维)
   规则书: 玩家手册(2024) · 城主指南(2024) · 怪物图鉴(2025)
   ✅ 索引就绪
```

### 2. 模组导入

PDF/DOCX/PPTX/XLSX → MarkItDown 转换，PDF 走专有结构解析：

```
用户: [上传 Baldurs_Gate_Avernus.pdf]

Agent:
📄 解析中 — 书签恢复: 42 锚点 · CJK 重排 · 目录过滤
📦 博德之门：坠入阿弗纳斯 (BGDIA)
   5 章 · 62 场景 · 847 块 · 847 向量 · ✅ 就绪
```

### 3. 开团流程

```
用户: 开个新团，用 BGDIA

Agent:
🎲 开团向导
   ✅ 战役创建: campaign_7a3f
   ✅ 规则绑定: 2024 Core
   ✅ 模组绑定: BGDIA Ch.1 解锁
   ✅ 初始存档: slot 1 "初始状态"

   🎉 战役就绪！请创建角色或说"随机生成"。
```

### 4. 角色创建

```
用户: 帮我建一个半精灵游侠

Agent:
📜 角色创建 — 半精灵游侠
   1️⃣ 种族: 半精灵 (+2 CHA, 黑暗视觉)
   2️⃣ 职业: 游侠 (d10, 宿敌, 自然探索者)
   3️⃣ 属性: DEX 16 · CON 14 · WIS 14 (27 point-buy)

用户: 确认

Agent:
✅ 瓦里斯·月影 — 半精灵游侠 1 级 · 混乱善良
   HP 12 · AC 14 · 长弓 (1d8) · 已录入数据库
```

### 5. 模组创作

```
用户: 给当前战役生成一个下水道支线

Agent:
✅ 生成支线模组：下水道的阴影 (One-shot · Five-Room Dungeon)
   S1 入口守卫 · S2 技能挑战(DC 12) · S3 反转 · S4 Boss · S5 奖励
   📝 modules/sewer_shadows.md · ✅ 已导入: 5 场景 47 块
```

### 6. 带团过程

```
Agent:
【时间：1492 DR，黄昏】【地点：深水城 · 精灵之歌酒馆】

你们做什么？

用户: 找兜帽人搭话

Agent:
🎲 卓尔女性——札拉·多乌尔登，焰拳队长。
"三起谋杀案，神殿区。去查。"

【线索: 死亡三神活动地图】【任务: 调查谋杀案】

用户: 问问老板娘

Agent:
🎲 阿兰娜："后巷人影...火把照过去，影子没有手指。"
【线索: 无指阴影】

⚔️ 🅰️ 连夜后巷  🅱️ 休息明早去  🅲️ 总部档案

用户: 🅱️

Agent:
🌙 长休完成。📁 自动存档 [slot 2: "精灵之歌休息后"]
```

### 7. 规则提问

```
用户: 冰巨人的 AC？

Agent: 🔍 Frost Giant · CR 8 · AC 15 · 寒冰抗性 · 多重攻击(2×巨斧 +12, 3d12+6)
       来源: 怪物图鉴(2025) p.187


用户: 圣武士 5 级几个法术位？

Agent: 🔍 一环 4 · 二环 2。来源: 玩家手册(2024) Ch.3


用户: 火球术伤害？

Agent: 🔥 Fireball · 三环 · 150ft · 20ft半径 · 8d6 火焰 · DEX 豁免减半
       来源: 玩家手册(2024) Ch.11
```

### 8. 战役管理

```
用户: 查看战役

Agent:
📋 campaign_7a3f  BGDIA  Ch.1  2级  存档 2
   campaign_b21   冰风谷  Ch.3  5级  存档 7

用户: 切到冰风谷

Agent: ✅ 已切换 campaign_b21 · 📍 Ch.3 Scene 4

用户: 存档

Agent: ✅ slot 8 "进入冰巨人之门前" · 🔑 a3f2c...

用户: 读档 slot 5

Agent: ⚠️ 自动保存当前 → ⏪ 恢复 slot 5 "冰巨人之门开启前"
   ✅ 世界/队伍/战斗/剧情/事件 全部恢复
```

## 存档、Recap 与记忆

SagaSmith 将三类容易混淆的数据分开管理：

| 数据 | 作用域 | 存储位置 | 用途 |
|------|--------|----------|------|
| Agent session 记忆 | 当前聊天/session | NanoBot session history 与压缩摘要 | 保持近期对话连续性 |
| Snapshot | 单个战役、单个存档点 | `campaign_saves.snapshot_json` | 保存可恢复的权威战役状态 |
| Campaign memory | 单个战役、当前存档分支 | `campaign_memories` + `campaign_memory_revisions` | 保存跨 session 使用、可随分支演化的长期叙事事实 |

创建存档时，Agent 只需发送一个简短工具调用：

```text
dnd_save action=create campaign_id=<id> label="进入地城前"
```

工具会捕获当前世界、队伍、PC、战斗、剧情、事件、场景和频道绑定，生成相对父存档的 recap，将 recap 写入 snapshot，并从 `memory_candidates` 与 `future_impact` 派生战役长期记忆。高优先级事实写为 `permanent`，中优先级写为 `candidate`，低优先级只保留在 recap 中。

每个存档保存 `parent_save_id`，整条时间线形成 DAG；读档会把 active head 移到目标存档，之后的新存档从该节点建立新分支。`campaign_memories` 只保存稳定的事实身份，事实在不同存档上的文本、优先级和状态保存在 `campaign_memory_revisions`。查询某个存档时，系统只沿其“根节点 → 当前节点”的祖先路径取最近 revision，因此兄弟分支的记忆不会串线。

查看 DAG 或自然语言查询记忆：

```text
dnd_save action=lineage campaign_id=<id>
dnd_memory action=scope campaign_id=<id>
dnd_memory action=search campaign_id=<id> query="米拉现在与队伍是什么关系？"
dnd_memory action=search campaign_id=<id> slot=3 query="这个存档里谁知道密门的位置？"
```

启用 ChromaDB 时，向量库保存 memory revision 的语义索引；DAG 祖先路径与有效 revision ID 仍由关系数据库精确计算，再限制 Chroma 的候选集合。ChromaDB 不决定分支，也不是权威存档。

读档使用：

```text
dnd_save action=restore campaign_id=<id> slot=3
```

`restore` 默认启用 `auto_save=true`，代码会先创建一个 `auto-before-restore` 存档，再恢复目标 slot。这个读档前备份是代码强制行为；长休、升级、章节结束等节点的自动存档目前由 skill 指示 Agent 主动调用 `dnd_save action=create`，不是数据库事件或定时器硬触发。

存档和读档只修改数据库中的战役状态、snapshot、recap 与 campaign memory，不会改写工作区文件或 `USER.md`。只有显式使用 `action=export` 时才会向指定路径写出 JSON 文件。

本版 memory schema 是破坏性更新：迁移会重建旧版 `campaign_memories`，不会导入旧版可变 memory 数据。

---

## 支持平台

SagaSmith 通过 channels 接入各大聊天平台。**私聊**直接响应；**群聊**默认需要 @机器人 才能触发（可配置为开放模式）。

| 平台 | Channel | 私聊 | 群聊策略 | 备注 |
|------|---------|:----:|----------|------|
| Telegram | [telegram.py](nanobot/channels/telegram.py) | ✅ | mention（可配置 open） | 支持流式输出、inline keyboards |
| Discord | [discord.py](nanobot/channels/discord.py) | ✅ | mention | Webhook 推送 |
| Slack | [slack.py](nanobot/channels/slack.py) | ✅ | mention | |
| 飞书 | [feishu.py](nanobot/channels/feishu.py) | ✅ | mention | 支持 emoji reaction |
| QQ (Napcat) | [napcat.py](nanobot/channels/napcat.py) | ✅ | mention / open | OneBot v11 协议，WebSocket |
| QQ (Bot) | [qq.py](nanobot/channels/qq.py) | ✅ | mention | botpy SDK |
| 企业微信 | [wecom.py](nanobot/channels/wecom.py) | ✅ | mention | |
| 个人微信 | [weixin.py](nanobot/channels/weixin.py) | ✅ | — | HTTP 长轮询 |
| WhatsApp | [whatsapp.py](nanobot/channels/whatsapp.py) | ✅ | mention（可配置 open） | Bridge WebSocket |
| Signal | [signal.py](nanobot/channels/signal.py) | ✅ | allowlist + mention | 支持 DM 和群组 |
| Matrix | [matrix.py](nanobot/channels/matrix.py) | ✅ | mention | |
| 钉钉 | [dingtalk.py](nanobot/channels/dingtalk.py) | ✅ | — | |
| MoChat | [mochat.py](nanobot/channels/mochat.py) | ✅ | — | |
| MS Teams | [msteams.py](nanobot/channels/msteams.py) | ✅ | — | |
| Email | [email.py](nanobot/channels/email.py) | ✅ | — | IMAP/SMTP |
| WebSocket | [websocket.py](nanobot/channels/websocket.py) | ✅ | — | 每连接独立 session，支持 Token 认证 |
| WebUI | — | ✅ | — | 内置 Web 界面，WebSocket 直连 |

**群聊说明：**
- `mention`：需要 @机器人 或回复机器人消息才响应
- `open`：所有消息都响应（可能产生噪音）
- 私聊无限制，未授权用户会收到配对码

---

## 快速开始

```powershell
# 1. 安装（uv 管理）
uv sync

# 2. 初始化工作区 + 自动发现平台
uv run nanobot onboard --wizard

# 3. SRD 在首次规则访问时自动摄入——无需手动 CLI
#    可选：预摄入
uv run python -m nanobot.dnd.db.cli rules ingest-srd

# 4. （可选）启用 ChromaDB 加速向量搜索
$env:CHROMA_DB_PATH = "$env:APPDATA\nanobot\dnd\chroma_db"

# 5. 启动网关 + QQ
.\scripts\start-all.bat
```

WebUI 地址：`http://127.0.0.1:18765`。

---

## 规则集

内置 3 套规则集，首次访问规则时自动摄入（惰性，无需手动 CLI）：

| 规则集 ID | 版本 | 语言 | 规则块数 | 来源 |
|---|---|---|---|---|
| `dnd5e-2024-srd-5.2.1` | 2024 | EN | 2,684 | 内置 SRD 5.2.1 |
| `dnd5e-2014-srd-5.1-en` | 2014 | EN | 3,524 | 内置 SRD 5.1 |
| `dnd5e-2014-srd-5.1-zh-v2` | 2014 | ZH-CN | ~2,000 | 内置中文翻译 |

启用 ChromaDB 时（`CHROMA_DB_PATH` 或 `CHROMA_DB_URL`），向量通过 HNSW 索引存储。

---

## 技能拆解

| 技能 | SKILL.md | 职责 |
|------|----------|------|
| 🎲 **dnd-dm** | [SKILL.md](skills/dnd-dm/SKILL.md) | 核心 DM 人格（always-on），规则裁判，战斗引擎，SRD 检索（参考自 [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill)） |
| 📋 **dnd-campaign-manager** | [SKILL.md](skills/dnd-campaign-manager/SKILL.md) | 战役生命周期，Snapshot 存档/读档，模组导入，USER.md 同步 |
| ✍️ **dnd-module-gen** | [SKILL.md](skills/dnd-module-gen/SKILL.md) | 模组生成：one-shot → short → medium → long → sandbox，25 种范式 |

### 模组生成范式一览

| 类型 | 推荐范式 | 产出规模 |
|------|----------|----------|
| One-shot | Five-Room Dungeon, Heist, Mystery | 1 章，3-6h |
| Short | Three-Act, Kishōtenketsu, Race Against Time | 3 章，3-8 次 |
| Medium | Hero's Journey, Plot Point, Faction Turn | 5 章，2-4 月 |
| Long | Double Triangle, Conspyramid, Megadungeon | 8 章，6+ 月 |
| Sandbox | Hexcrawl, Node-Based, Blorb | 4-6 区域，开放 |

---

## DM 人格：明萨拉·班瑞

以《博德之门 3》经典角色明萨拉·班瑞为原型的守序邪恶 DM：

- **规则绝对主义** — 严格按 2024 版规则书裁决，骰子结果不可商量
- **冷刺幽默** — 指出战术失误后，补一句带刺的可行建议
- **信息边界** — 绝不泄露 DC、怪物隐藏数值、未发现房间、后续剧情
- **玩家自主** — 不替玩家做任何决定，不因戏剧效果改骰

默认适配《博德之门：坠入阿弗纳斯》模组，可通过模组导入适配任意冒险。

---

## 架构

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

## 上下文管理

| 机制 | 描述 |
|------|------|
| Session JSONL | 实时对话日志 |
| Auto-Compact | Token 预算达到 30% 时触发压缩 |
| Dream (每 2 小时) | 长期记忆摘要 → MEMORY.md |

---

## 项目结构

```
SagaSmith-agent/
├── nanobot/                   # Agent 运行时
│   ├── agent/                 #   Agent Loop · Context · Memory · Runner
│   ├── channels/              #   19 个平台接入（QQ/Telegram/Discord/...）
│   ├── dnd/                   #   D&D 适配器（rules · db · engine · modules）
│   ├── skills/                #   dnd-dm · dnd-campaign-manager · napcat-qq
│   └── templates/             #   系统提示模板（identity · SOUL · platform）
├── scripts/                   # 启动脚本
│   ├── start-all.bat          #   一键启动（uv 管理）
│   └── install.ps1            #   安装脚本
├── tests/                     # 测试
└── pyproject.toml             # uv 项目配置
```

---

## 外部依赖

| 依赖 | 用途 |
|------|------|
| Python 3.11+ | domain 运行时 |
| SQLAlchemy | 数据库 ORM |
| FlagEmbedding | BGE-M3 Dense Vector 检索 |
| markitdown | PDF / DOCX 模组导入 |

---

## 致谢

- [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill) — D&D DM skill 先驱，SagaSmith 的灵感与设计参考
- [NanoBot](https://github.com/HKUDS/nanobot) — 轻量级 AI agent 框架
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — SKILL.md 生态标准推动者
- D&D 5e SRD 5.2.1 © Wizards of the Coast，以 [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) 授权使用
- [SagiriWWW/DND.SRD.zh-CN](https://github.com/SagiriWWW/DND.SRD.zh-CN) — D&D 5e SRD 5.1 中文翻译

---

## 许可证

- 代码：MIT
- SRD 5.2.1 数据文件：[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
