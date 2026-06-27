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

在 snapshot 顶层增加 `recap` 字段，保持向后兼容；旧存档没有该字段时读档流程应正常工作。

```json
{
  "snapshotVersion": 2,
  "slot": 12,
  "campaignId": "...",
  "chapter": 2,
  "location": "艾尔托瑞尔废墟 · 断桥前",
  "createdAt": "2026-06-26T12:34:56Z",
  "recap": {
    "version": 1,
    "baseline": false,
    "fromSnapshotId": "snapshot_11",
    "toSnapshotId": "snapshot_12",
    "generatedAt": "2026-06-26T12:34:56Z",
    "language": "zh-CN",
    "summary": "自上次存档以来……",
    "plotProgress": ["穿过恶魔巡逻线", "发现断桥下的灵魂锁链"],
    "newCharacters": [
      {
        "name": "Nera",
        "role": "提夫林斥候",
        "relationship": "谨慎盟友",
        "firstSeenAt": "艾尔托瑞尔废墟"
      }
    ],
    "newLocations": ["断桥下方", "铁塔废墟线索"],
    "triggeredEvents": [
      {
        "type": "echo",
        "name": "地狱债印",
        "result": "魔鬼谈判风险上升"
      }
    ],
    "futureImpact": ["难民营渗透线索开启", "失踪牧师线索转向铁塔废墟"],
    "playerChoices": ["释放被锁链束缚的灵魂"],
    "memoryCandidates": [
      {
        "kind": "plot_commitment",
        "text": "Nera 已向队伍透露难民营可能被 Zariel 信徒渗透。",
        "priority": "high"
      }
    ],
    "source": {
      "mode": "delta_from_previous_snapshot",
      "previousSnapshotId": "snapshot_11",
      "eventsRange": {
        "fromEventSeq": 231,
        "toEventSeq": 279
      }
    }
  }
}
```

### 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `summary` | 是 | 给玩家看的自然语言 recap。 |
| `plotProgress` | 否 | 已完成或实质推进的剧情节点。 |
| `newCharacters` | 否 | 新角色或首次变重要的旧角色。 |
| `newLocations` | 否 | 新场景、可回访地点、重要空间。 |
| `triggeredEvents` | 否 | 回声、陷阱、派系反应、战斗结果、节点触发。 |
| `futureImpact` | 否 | 会影响后续走向的内容。 |
| `playerChoices` | 否 | 有分支意义的玩家选择。 |
| `memoryCandidates` | 否 | 需要进入长期记忆的候选项。 |
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

存档前先定位同一 campaign 的上一个有效 snapshot：

1. 按 `campaignId` 过滤。
2. 排除当前正在写入的临时 snapshot。
3. 按 `createdAt` 或自增 slot 取最新一个。
4. 若不存在，进入 baseline recap 模式。

### 3. 收集 delta 输入

优先级从高到低：

1. 事件日志中 `previousSnapshot.eventSeq + 1` 到当前 `eventSeq` 的结构化事件。
2. 当前 session 中自上次存档以来的用户选择、工具结果、战斗结果。
3. 当前世界状态、任务状态、队伍状态与上一 snapshot 的字段 diff。
4. 场景缓存中的已揭示场景文本。
5. 若以上不足，使用当前 DM 最近上下文生成保守摘要，并标记 `source.mode = "context_fallback"`。

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

存档写入时，把 recap 作为 snapshot 的一部分原子保存：

1. 生成基础 snapshot。
2. 嵌入场景缓存。
3. 生成 recap。
4. 校验 snapshot + recap。
5. 原子写入数据库或 JSON 存档。
6. 写入事件日志：`snapshot.saved` 与 `snapshot.recap.generated`。

如果 recap 生成失败：

- 存档不能失败，仍然写入 snapshot。
- `recap.summary` 使用降级文本，例如“本次存档已完成，但剧情 recap 生成失败；可稍后重新生成”。
- 写入 `recap.error` 和 `recap.source.mode = "failed"`。
- 返回用户时明确说明 recap 暂不可用。

### 6. 返回给用户

存档成功后，把存档元信息和 recap 一起返回：

- 存档槽位、名称、时间、章节、地点。
- 队伍关键状态摘要。
- Recap `summary`。
- 如果存在高优先级 `futureImpact`，展示 1-3 条“后续影响”。
- 如果触发长期记忆，追加“🧠 已记录关键记忆/已加入记忆候选”。

### 7. 触发记忆机制

Recap 写入 snapshot 后，触发一个轻量记忆判定器：

```text
memoryCandidates + futureImpact + playerChoices → memory trigger classifier
```

建议触发规则：

- `priority = high` 的 `memoryCandidates` 立即进入长期记忆候选队列。
- 新角色关系变化、永久性后果、路线分支、阵营敌对、魔法契约、死亡、重大物品归属必须触发。
- 单次普通战斗、短期资源消耗、纯氛围描写默认不触发，除非影响后续。
- 同一事实已有记忆时合并更新，不重复写入。

触发后的动作分三档：

| 档位 | 条件 | 动作 |
| --- | --- | --- |
| P0 即时写入 | 角色死亡、契约、章节结算、不可逆分支 | 立即更新 campaign memory / USER 同步所需字段。 |
| P1 候选入队 | 新 NPC、线索、重要承诺、未结任务 | 写入 memory queue，等待 Dream 或下次整理。 |
| P2 仅随存档保存 | 普通场景推进、临时战斗结果 | 只保留在 snapshot recap 中。 |

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

## API / 工具接口建议

### 写存档

```python
save_snapshot(
    campaign_id: str,
    snapshot: dict,
    generate_recap: bool = True,
    trigger_memory: bool = True,
) -> SaveSnapshotResult
```

返回：

```python
@dataclass
class SaveSnapshotResult:
    snapshot_id: str
    slot: int
    saved_at: str
    display_name: str
    recap: dict | None
    memory_actions: list[dict]
    warnings: list[str]
```

### 重新生成 recap

为旧存档和失败场景提供补救接口：

```python
regenerate_snapshot_recap(snapshot_id: str, compare_to: str | None = None) -> dict
```

### 记忆触发

```python
trigger_memory_from_recap(campaign_id: str, snapshot_id: str, recap: dict) -> list[MemoryAction]
```

## 校验规则

存档完成后新增 recap 校验：

- `recap.summary` 必须非空，除非 `recap.error` 存在。
- `fromSnapshotId` 必须等于实际比较对象；首次存档允许为空且 `baseline = true`。
- `futureImpact` 不得包含未揭示未来剧情。
- `memoryCandidates` 每条应有 `kind/text/priority`。
- `summary` 长度超过上限时自动截断或重新压缩。
- 旧 snapshot 无 `recap` 时不报错。

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

### Phase 1：文档与数据契约

- 定义 `recap` schema。
- 定义存档返回格式。
- 定义记忆触发规则与优先级。
- 更新 DM / campaign manager 规则文档，要求存档回复必须包含 recap。

### Phase 2：存档写入链路

- 在 snapshot 写入前查找上一个 snapshot。
- 收集事件日志和状态 diff。
- 生成 recap 并嵌入 snapshot。
- 存档成功后返回 recap 给用户。

### Phase 3：记忆绑定

- 实现 `trigger_memory_from_recap`。
- 支持 P0 即时写入、P1 候选队列、P2 仅存档保存。
- 对重复记忆做合并与去重。

### Phase 4：读档与 UI/API 展示

- 读档时展示 snapshot recap。
- 存档列表展示短 recap。
- 详情页或命令展示完整 recap、影响、记忆动作。

### Phase 5：迁移与回放

- 为旧 snapshot 添加可选 recap 重生成。
- 为 recap 失败或记忆触发失败提供重试命令。
- 增加回归测试覆盖自动存档、手动存档、读档、旧存档。

## 测试计划

- 手动存档：确认返回存档信息 + recap。
- 自动存档：升级、长休、章节结束均生成 recap。
- 首次存档：生成 baseline recap。
- 连续存档：第二次 recap 只描述相对第一次的推进。
- 旧存档：缺少 recap 仍能读档；再次存档后新 snapshot 有 recap。
- 记忆触发：新 NPC、不可逆选择、高优先级线索进入记忆队列。
- 安全性：recap 不泄露隐藏 DC、未揭示房间、未来剧情。
- 降级：LLM 失败时存档成功并返回 warning。

## 推荐最终行为约束

从上线后开始，任何“存档成功”的对外回复都必须同时满足：

1. 告诉玩家存档在哪里、什么时候、当前章节地点是什么。
2. 给出“相比上个存档”的 recap。
3. 明确指出影响后续走向的内容。
4. 将 recap 写入 snapshot。
5. 对高优先级内容触发记忆机制，或说明已加入记忆候选。
