# 存档 Recap 与触发记忆机制绑定方案

## 背景与目标

当前 SagaSmith 的存档已经承担了战役权威状态的职责：快照记录队伍、章节、地点、任务、节点、场景缓存等信息；记忆机制则负责在会话之外保留对后续叙事有用的长期上下文。为了让“存档”不仅保存数值状态，也保存“这一段剧情为什么重要”，本方案将存档、剧情 recap、触发记忆三者绑定为同一个原子流程。

目标是在每次成功存档时：

1. 自动生成一段相对上一个存档的剧情 recap。
2. 将 recap 写入本次 snapshot，随存档一起持久化。
3. 将“已存档信息 + recap”一起返回给用户。
4. 根据 recap 中识别出的关键变化，触发记忆候选写入或 Dream/长期记忆整理。
5. 在读档、查看存档列表、恢复上下文时优先使用 snapshot 内的 recap，而不是依赖完整聊天历史。

## 用户体验

玩家触发“存档”或系统自动存档后，回复不再只是“已保存”，而是包含两层信息：

```text
💾 已存档
- 存档槽位：slot 12
- 战役：坠入阿弗纳斯
- 章节：第 2 章
- 当前位置：艾尔托瑞尔废墟 · 断桥前
- 游戏内时间：1494 DR，黄昏
- 队伍状态：4 人，均存活；卡菈克 HP 22/41；盖尔 2 环法术位已耗尽

📜 本次推进 Recap
自上次存档以来，队伍穿过恶魔巡逻线并发现断桥下的灵魂锁链。新登场的提夫林斥候 Nera 揭示了难民营被 Zariel 信徒渗透的线索。玩家选择释放被锁链束缚的灵魂，触发“地狱债印”回声，使后续与魔鬼谈判的风险上升，但也让失踪牧师的线索转向了铁塔废墟。
```

如果是第一次存档，则 recap 使用“开局至今摘要”，并标记 `baseline: true`。

## Recap 内容要求

Recap 必须回答“相比上个存档推进了什么”，而不是复述当前状态。建议长度：中文 150-400 字，极简列表可降到 80-150 字。

必须覆盖以下维度，若无则明确省略或置空：

- **剧情推进**：完成了哪些场景、节点、任务步骤、章节目标。
- **新角色**：新登场 NPC、怪物、盟友、敌人，以及当前关系倾向。
- **新场景**：新地点、隐藏区域、重要物件、可回访区域。
- **触发事件**：陷阱、回声、伏笔、派系反应、战斗、检定结果、时间压力。
- **后续影响**：改变路线、锁定/解锁选项、引入代价、资源消耗、敌我态势变化。
- **玩家选择**：真正影响分支或关系的玩家决定，避免把 DM 叙述误当成玩家选择。
- **记忆候选**：值得进入长期记忆的稳定事实或叙事承诺。

Recap 禁止包含：

- 未揭示的未来剧情、隐藏 DC、怪物隐藏数值、未发现房间。
- 只存在于 DM 计划但玩家尚未触发的内容。
- 与本次存档无关的完整角色卡、规则长文或聊天流水账。

## Snapshot 数据结构

当前存档结构：`campaign_saves` 表的 `snapshot_json` 列存储完整快照 JSON（格式为 `dnd-campaign-snapshot`，schema version 3）。本方案在 `snapshot_json` 顶层新增 `recap` 字段，旧存档没有该字段时读档流程正常工作——`snapshot_json` 是 JSON 列，不校验内部 key，向后兼容无需 migration。

### 数据库物理结构

存档操作发生在单个 SQLite 数据库 `~/.sagasmith/dnd/dnd_dm.db` 中：

```
campaign_saves 表（每次存档新增一行，不覆盖旧行）
├── id: "save_abc123def456"
├── campaign_id: "campaign_abc123"
├── slot: 12                          ← 自增，每次 max(slot)+1
├── label: "进入地城前"
├── chapter: "2"                      ← 从 world_state 提取
├── location: "艾尔托瑞尔废墟·断桥前"
├── snapshot_json: { ... }            ← JSON blob，以下全部内容
├── snapshot_format: "dnd-campaign-snapshot"
├── snapshot_hash: "sha256..."
├── schema_version: 3
└── state_version: 12
```

### snapshot_json 完整结构（新增 `recap` 在顶层）

```json
{
  "format": "dnd-campaign-snapshot",
  "schema_version": 3,
  "campaign_id": "campaign_abc123",
  "captured_at": "2026-06-26T12:34:56Z",

  "campaign": {
    "name": "坠入阿弗纳斯",
    "system_version": "D&D 5e 2024",
    "module_name": "Descent into Avernus",
    "engine_source": "sagasmith-dnd-engine",
    "status": "active",
    "config": { "user_md_player_roles": "- 张三：卡菈克" }
  },

  "state": {
    "world_states": [{ "id": "world_xxx", "state_json": { "current_chapter": 2, "current_scene": "断桥前" }, "state_version": 12 }],
    "parties": [{ "id": "party_xxx", "name": "冒险者小队", "location": "艾尔托瑞尔废墟·断桥前", "shared_gold": 250 }],
    "characters": [{ "id": "char_001", "character_type": "pc", "name": "卡菈克", "hp": 22, "max_hp": 41, "level": 3, "sheet_json": {...} }],
    "combats": [{ "id": "combat_001", "name": "恶魔巡逻队遭遇战", "is_active": false, "result": "胜利" }],
    "plot_summaries": [{ "id": "summary_xxx", "scope": "campaign", "summary": "队伍进入艾尔托瑞尔废墟...", "open_threads": [...] }],
    "campaign_events": [{ "id": "event_045", "event_type": "npc_introduced", "content": "Nera 在断桥下出现", "importance": 4 }],
    "scene_states": [{ "id": "scene_state_001", "scene_id": "scene_broken_bridge", "current_room": "断桥下方", "explored_percent": 75, "state_json": {...} }],
    "channel_bindings": [{ "id": "binding_001", "channel": "telegram", "character_id": "char_001", "display_name": "张三" }]
  },

  "recap": {
    "version": 1,
    "baseline": false,
    "from_save_id": "save_abc123def456",
    "to_save_id": "save_xyz789ghi012",
    "generated_at": "2026-06-26T12:34:56Z",
    "language": "zh-CN",
    "summary": "自上次存档以来……",
    "plot_progress": ["穿过恶魔巡逻线", "发现断桥下的灵魂锁链"],
    "new_characters": [
      {
        "name": "Nera",
        "role": "提夫林斥候",
        "relationship": "谨慎盟友",
        "first_seen_at": "艾尔托瑞尔废墟"
      }
    ],
    "new_locations": ["断桥下方", "铁塔废墟线索"],
    "triggered_events": [
      {
        "type": "echo",
        "name": "地狱债印",
        "result": "魔鬼谈判风险上升"
      }
    ],
    "future_impact": ["难民营渗透线索开启", "失踪牧师线索转向铁塔废墟"],
    "player_choices": ["释放被锁链束缚的灵魂"],
    "memory_candidates": [
      {
        "kind": "plot_commitment",
        "text": "Nera 已向队伍透露难民营可能被 Zariel 信徒渗透。",
        "priority": "high"
      }
    ],
    "source": {
      "mode": "delta_from_previous_snapshot",
      "previous_save_id": "save_abc123def456",
      "events_range": {
        "from_event_id": "event_045",
        "to_event_id": "event_079"
      }
    }
  }
}
```

> **注意**：`recap` 使用 snake_case 字段名，与现有 `snapshot_json` 的命名风格一致（`campaign_id`、`schema_version`、`state_version`）。

### 存档不捕获的内容（刻意保留）

快照只保存可变游戏状态，以下内容在 restore 时不被删除/重写：

- **模组文档**（`module_chapters`、`module_chunks`、`scene_indexes`）：导入后不变
- **NPC**（`character_type != "pc"`）：存在全局 NPC 库，不随战役存档
- **规则数据**（`rule_sets`、`rule_publications` 等）：全局共享
- **审计日志**（`tool_audits`、`state_revisions`、`dice_rolls`）：只追加不覆盖
- **向量嵌入**（ChromaDB）：独立存储

### recap 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `summary` | 是 | 给玩家看的自然语言 recap（中文 150-400 字）。 |
| `plot_progress` | 否 | 已完成或实质推进的剧情节点。 |
| `new_characters` | 否 | 新角色或首次变重要的旧角色。 |
| `new_locations` | 否 | 新场景、可回访地点、重要空间。 |
| `triggered_events` | 否 | 回声、陷阱、派系反应、战斗结果、节点触发。 |
| `future_impact` | 否 | 会影响后续走向的内容。 |
| `player_choices` | 否 | 有分支意义的玩家选择。 |
| `memory_candidates` | 否 | 需要进入长期记忆的候选项（写入 `campaign_memories` 表）。 |
| `source` | 是 | 生成依据，用于审计和调试。 |

## 流程设计

### 1. 存档触发

触发来源保持现状，包括：

- 玩家主动说“保存/存档”。
- 升级完成。
- 长休结束。
- 章节结束或进入下一章前。
- 重大决策前或关键战斗前。
- 系统自动检查点。

### 2. 读取上一个存档

存档前先定位同一 campaign 的上一个有效 snapshot（数据库查询，不是文件系统）：

1. 查 `campaign_saves` 表，按 `campaign_id` 过滤。
2. 排除当前正在写入的行（事务未提交）。
3. 按 `slot` 降序取最新一个（`slot` 自增，反应时间顺序）。
4. 若该 campaign 无任何存档，进入 baseline recap 模式。

### 3. 收集 delta 输入

输入来源按优先级排列：

1. `campaign_events` 表中两次存档之间的事件（按 `created_at` 区间查询，事件类型覆盖 `npc_introduced`、`combat_result`、`player_choice`、`scene_transition`、`quest_update` 等）。
2. 两次 `snapshot_json` 的字段级 diff：
   - `world_states.state_json`（章节、场景、任务进度、派系关系）
   - `characters`（HP、等级、法术位、状态变化）
   - `parties`（位置、金币变化）
   - `plot_summaries.summary` + `open_threads`
   - `scene_states`（探索百分比、房间变化）
3. 当前 session 中用户消息和工具调用结果（从 runner context 中提取）。
4. 若以上不足，使用最近 DM 上下文生成保守摘要，标记 `source.mode = "context_fallback"`。

### 4. 生成 recap

推荐两层生成：

1. **结构化提取**：从事件日志和 snapshot diff 中提取 `plotProgress/newCharacters/newLocations/triggeredEvents/futureImpact/playerChoices/memoryCandidates`。
2. **自然语言压缩**：把结构化结果压缩成玩家可读的 `summary`。

生成提示词应强调：

- 只总结已发生、玩家已知、可审计的内容。
- 对比上一个存档，避免重复当前全量状态。
- 输出 JSON，失败时降级为纯文本 `summary`。
- 不暴露 DM 秘密、隐藏 DC、未来剧情。

### 5. 写入 snapshot

当前 `create` 方法已实现完整的 capture → serialize → insert 流程。改动点仅在 capture 之后、insert 之前插入 recap 生成：

1. `capture_from_session()` — 序列化 8 类可变状态 + campaign 元数据（现有逻辑不动）。
2. **生成 recap**（新增）— 调用 LLM 生成 `recap` JSON。
3. 将 `recap` 挂到 snapshot payload 顶层。
4. 校验 snapshot + recap（checksum 重新计算）。
5. 构造 `CampaignSave` 行，`INSERT` 到 `campaign_saves` 表（现有逻辑，`snapshot_json` 列接受任意 JSON key）。
6. 追加 `campaign_events` 事件：`snapshot_created` + `snapshot_recap_generated`。

如果 recap 生成失败：

- `INSERT` 仍然执行，`recap.summary` 使用降级文本。
- 写入 `recap.error` 和 `recap.source.mode = “failed”`。
- 返回用户时明确说明 recap 暂不可用。

### 6. 返回给用户

存档成功后，把存档元信息和 recap 一起返回：

- 存档槽位、标签、时间、章节、地点（从 `CampaignSave` 行 + `SnapshotInfo` 获取）。
- 队伍关键状态摘要（从 `snapshot_json.state.parties` + `characters` 提取）。
- Recap `summary`。
- 如果存在高优先级 `future_impact`，展示 1-3 条”后续影响”。
- 如果触发长期记忆，追加”🧠 已记录关键记忆/已加入记忆候选”。

### 7. 触发记忆机制

Recap 写入 snapshot 后，触发记忆判定器：

```text
memory_candidates + future_impact + player_choices → memory trigger classifier
```

记忆存储目标为**新建的 `campaign_memories` 数据库表**（campaign 级隔离），不写入 USER.md。

建议触发规则：

- `priority = high` 的 `memoryCandidates` 立即进入长期记忆候选队列。
- 新角色关系变化、永久性后果、路线分支、阵营敌对、魔法契约、死亡、重大物品归属必须触发。
- 单次普通战斗、短期资源消耗、纯氛围描写默认不触发，除非影响后续。
- 同一事实已有记忆时合并更新，不重复写入。

触发后的动作分三档，记忆统一存储在**数据库**中（新建 `CampaignMemory` 表），不写入 USER.md：

| 档位 | 条件 | 动作 |
| --- | --- | --- |
| P0 即时写入 | 角色死亡、契约、章节结算、不可逆分支 | 立即写入 `campaign_memories` 表，标记 `permanent`。 |
| P1 候选入队 | 新 NPC、线索、重要承诺、未结任务 | 写入 `campaign_memories` 表，标记 `candidate`，等待 Dream 整理后升级为 `stable` 或降级为 `ephemeral`。 |
| P2 仅随存档保存 | 普通场景推进、临时战斗结果 | 只保留在 snapshot recap 中，不单独写入记忆表。 |

USER.md 保持其设计边界：仅存储用户身份/偏好（语言、风格、玩家-角色名映射），不存放战役叙事内容。

## 读档与列表展示

### 读档

读档时优先加载：

1. snapshot 权威状态。
2. snapshot `recap.summary` 作为“上次剧情回顾”。
3. snapshot `recap.futureImpact` 和 `memoryCandidates` 作为 DM 连续性约束。
4. 必要时再查事件日志或完整历史。

### 存档列表

存档列表可以增加一行短 recap：

```text
slot 12 · 第2章 · 艾尔托瑞尔废墟 · 2026-06-26
  Recap: 发现灵魂锁链，结识 Nera，难民营渗透线索开启。
```

为避免列表过长，默认展示 `summary` 的 60-100 字截断；详情页展示完整 recap。

## API / 工具接口

### 写存档（扩展现有 `dnd_save`）

现有工具 `dnd_save action=create` 直接调用 `CampaignSnapshotService.create()`。本方案在 `create` 内部增加 recap 生成和记忆触发，**不改变对外工具签名**：

```
# 现有调用方式不变
dnd_save action=create campaign_id=<id> label="进入地城前"

# 内部新增流程：capture → generate_recap → insert → trigger_memory
```

返回 `SnapshotInfo` 扩展为包含 recap 和记忆动作：

```python
@dataclass(frozen=True)
class SnapshotInfo:
    id: str              # save_xxx (CampaignSave.id)
    campaign_id: str
    slot: int
    label: str
    chapter: str
    location: str
    snapshot_hash: str
    created_at: datetime
    # 以下为新增字段
    recap: dict | None         # snapshot_json["recap"]，旧存档为 None
    memory_actions: list[dict] # 本次触发的记忆动作列表
    warnings: list[str]        # 降级/警告信息
```

### `campaign_memories` 表（新建）

战役叙事记忆独立于 USER.md，存在数据库中：

```sql
CREATE TABLE campaign_memories (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,           -- plot_commitment / npc_relation / location_fact / quest_state
    text        TEXT NOT NULL,           -- 原子事实
    priority    TEXT NOT NULL DEFAULT 'medium',  -- high / medium / low
    status      TEXT NOT NULL DEFAULT 'candidate', -- candidate / stable / permanent / ephemeral / superseded / contested
    entity_type TEXT,                    -- npc / location / quest / faction / item
    entity_id   TEXT,                    -- 实体标识符
    fact_type   TEXT,                    -- 去重用的组合键
    supersedes  TEXT,                    -- 替代的旧 memory id
    source_save_id TEXT,                 -- 来源存档 id (CampaignSave.id)
    score       INTEGER,                 -- Dream 相关性评分 (1-5)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(campaign_id, entity_type, entity_id, fact_type)
);
```

**查询接口**：

```python
# 写记忆
def upsert_memory(campaign_id: str, kind: str, text: str, priority: str, ...) -> str

# 读当前有效记忆（读档时加载）
def get_active_memories(campaign_id: str) -> list[dict]

# Dream 修剪
def prune_memories(campaign_id: str, min_score: int = 3) -> int
```

### 重新生成 recap

为旧存档和失败场景提供补救：

```
dnd_save action=regenerate_recap campaign_id=<id> slot=12
```

内部逻辑：读取该 slot 的 `snapshot_json`，找到上一个 slot 作为对比对象，调用 recap 生成器，更新 `snapshot_json.recap`。

### 记忆触发（独立可重放）

```python
trigger_memory_from_recap(campaign_id: str, save_id: str, recap: dict) -> list[dict]
```

失败不影响存档，可稍后单独调用重放。

## 校验规则

存档完成后新增 recap 校验：

- `recap.summary` 必须非空，除非 `recap.error` 存在。
- `recap.from_save_id` 必须等于实际比较对象的 `CampaignSave.id`；首次存档允许为空且 `baseline = true`。
- `recap.future_impact` 不得包含未揭示未来剧情。
- `recap.memory_candidates` 每条应有 `kind/text/priority`。
- `recap.summary` 长度超过 400 字时自动截断或重新压缩。
- 旧 snapshot 无 `recap` key 时不报错（向后兼容）。
- 新增 `recap` key 不影响现有 `snapshot_hash` 校验——校验逻辑不变，checksum 覆盖完整 `snapshot_json` 包括 `recap`。

## 向后兼容与迁移

1. 旧存档保持可读。
2. 旧存档列表展示时，如果缺少 recap，则显示“无 recap，可重新生成”。
3. 首次读旧存档并再次保存时，为新 snapshot 生成 recap；对比对象可以是旧存档的状态 diff。
4. 可提供批处理脚本，为历史 snapshot 补生成 baseline recap，但不作为上线阻塞项。

## 失败与降级策略

| 场景 | 策略 |
| --- | --- |
| LLM recap 生成失败 | 存档照常成功，写入失败占位 recap，返回 warning。 |
| 上个存档损坏 | 改用最近一个可读 snapshot；仍失败则 baseline。 |
| 事件日志缺失 | 使用 snapshot diff + 最近上下文生成保守 recap。 |
| 记忆触发失败 | 不影响存档，记录 warning，稍后可重放 `trigger_memory_from_recap`。 |
| Recap 包含疑似 DM 秘密 | 丢弃该项并重新生成；仍失败则使用结构化安全摘要。 |

## 实施步骤

### Phase 1：数据契约

- 定义 `recap` schema（snake_case，与 `snapshot_json` 风格一致）。
- 定义 `campaign_memories` 表结构 + migration。
- 定义记忆触发规则配置（P0/P1/P2 条件可配置，不硬编码）。
- 更新 DM / campaign manager 规则文档，明确存档回复必须包含 recap，记忆存在数据库不写 USER.md。

### Phase 2：存档写入链路

- 在 `CampaignSnapshotService.create()` 中，`capture_from_session()` 之后、构造 `CampaignSave` 行之前插入 recap 生成。
- 实现 delta 收集：从 `campaign_events` 表查询事件 + `snapshot_json.state` 字段 diff。
- 实现两层 recap 生成器（结构化提取 + summary 压缩）。
- 将 `recap` 写入 `snapshot_json` 顶层，`INSERT` 到 `campaign_saves` 行。
- 修改 `SnapshotInfo` 返回结构，包含 recap 和 memory_actions。

### Phase 3：记忆绑定

- 创建 `campaign_memories` 表（Alembic migration）。
- 实现 `trigger_memory_from_recap`：从 `recap.memory_candidates` + `future_impact` + `player_choices` 分类写入。
- 实现记忆去重：`(campaign_id, entity_type, entity_id, fact_type)` 组合唯一键。
- 支持 P0→permanent、P1→candidate→Dream 升级/降级、P2 仅 snapshot。
- 读档时加载 `status IN ('permanent', 'stable')` 的记忆作为 DM 连续性约束。

### Phase 4：读档与 UI 展示

- 读档时展示 snapshot recap summary + future_impact。
- 存档列表展示短 recap（60-100 字截断）。
- 详情展示完整 recap + 记忆状态。

### Phase 5：迁移与回放

- 批处理脚本：为历史 snapshot 补生成 baseline recap。
- 失败恢复：`regenerate_recap` + `retry_memory_trigger`。
- 回归测试：自动/手动存档、baseline/连续存档、旧存档兼容、记忆触发、并发锁。

## 测试计划

- 手动存档：确认返回 `SnapshotInfo` 包含 `recap` 和 `memory_actions`。
- 自动存档：升级、长休、章节结束均生成 recap。
- 首次存档：生成 baseline recap（`baseline: true`, `from_save_id: null`）。
- 连续存档：第二次 recap 只描述相对第一次的推进，不重复现状。
- 旧存档：`snapshot_json` 缺少 `recap` key 时读档正常；再次保存后新 snapshot 含 recap。
- 记忆写入：新 NPC → `campaign_memories` 写入 `candidate`；不可逆选择 → `permanent`；普通战斗 → 不写入表，仅 snapshot。
- 记忆去重：同一 `(campaign_id, entity_type, entity_id, fact_type)` 更新而非新增。
- USER.md 隔离：存档/读档不修改 USER.md 中战役叙事内容，仅保留 player_roles 块。
- 安全性：recap 不泄露隐藏 DC、未揭示房间、未来剧情。
- 降级：LLM 失败时存档成功并返回 warning；recap 占位文本正确。
- checksum：新增 `recap` key 后 snapshot_hash 正常校验。

## 推荐最终行为约束

上线后，任何”存档成功”的对外回复都必须同时满足：

1. 告诉玩家存档槽位、时间、章节、地点（从 `CampaignSave` 行提取）。
2. 给出”相比上个存档”的 recap（从 `snapshot_json.recap.summary` 读取）。
3. 明确指出影响后续走向的内容（`future_impact` 高优先级条目）。
4. 将 recap 写入 `snapshot_json` 随 `campaign_saves` 行持久化。
5. 对高优先级内容触发 `campaign_memories` 写入，或说明已加入记忆候选。
6. 所有战役叙事记忆存入数据库，**不写入 USER.md**。
