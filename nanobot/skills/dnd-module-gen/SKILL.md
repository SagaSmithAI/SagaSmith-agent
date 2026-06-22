---
name: dnd-module-gen
description: Generate D&D 5e adventure modules. Supports one-shot, short (3 chapters), medium (5 chapters), long (8 chapters), and sandbox. Use when the user asks to create, generate, or make a new adventure, module, or campaign setting.
---

# D&D Module Generator

## Core Rules

1. **Always write to file first.** Never inline-import generated content. Write to `<workspace>/modules/<name>.md`, then import from that path via `dnd_module action=import source_path=...`. This preserves the generated module as a reusable artifact.

2. **One-shot and short are one-step generation.** Generate the complete module in a single pass, write to file, import.

3. **Medium and long are multi-step generation.** Each step produces one section of the module. User reviews each step before proceeding. This avoids context overflow and allows course correction.

4. **Sandbox generates top-level overview first**, then each region independently.

5. **Use `spawn` for parallel chapter/region generation in medium, long, and sandbox.** See Spawn Strategy below for rules.

---

## Spawn Strategy（并行加速）

For **medium**, **long**, and **sandbox** types, chapter body generation steps **must use the `spawn` tool** to run subagents in parallel. This avoids context overflow and dramatically reduces wall-clock time.

### When to spawn

| Type | Step | spawn 策略 |
|------|------|-----------|
| one-shot | — | ❌ 不 spawn，直接生成全文 |
| short | — | ❌ 不 spawn，直接生成全文 |
| medium | M2（前3章） | ✅ 每章一个 subagent，3 个并行 |
| medium | M3（后2章+附录） | ✅ 每章一个 + 附录各部分，并行 |
| long | L2（第一弧4章） | ✅ 每章一个 subagent，4 个并行 |
| long | L3（第二弧4章） | ✅ 每章一个 subagent，4 个并行 |
| long | L4（PC线+附录） | ✅ 每个 PC 一个 + 附录各部分，并行 |
| sandbox | S2-N（区域） | ✅ 每个区域一个 subagent，全部并行 |

### spawn task 必须包含的内容

每个 subagent 的 `task` 参数**必须**包含以下四部分，缺一不可：

1. **该章节/区域的完整大纲**（从骨架中复制对应行，含章节标题、核心冲突、情感节拍、关键 NPC、反转/揭示）
2. **全局约束**（模组名、主题、基调、反派、等级、与前后章的衔接点）
3. **输出格式要求**（章节 Markdown 模板：`# 第X章：标题` → `## <场景名>` → `#### <房间名>` + NPC 对话、DC 值、奖励）
4. **输出文件路径**（**每章写入独立临时文件**：`<workspace>/modules/<name>_chN.md`，后续步骤合并）

### spawn task 模板

```
spawn task = """
生成第<N>章正文，写入 <workspace>/modules/<name>_ch<N>.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 本章与前后的衔接：<前章收尾> → 本章 → <后章铺垫>

## 本章大纲（来自骨架）
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键NPC | 反转/揭示 |
|---|---|---|---|---|---|
| <N> | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> |

## 输出格式
# 第<N>章：<标题>
## <场景1名称> ← <一句话描述场景功能>
<NPC 列表、对话示例、DC 值、奖励>
#### <房间名称> ← 如果是地城场景
<环境描述、陷阱、遭遇>

## <场景2名称> ← ...
...

# 每章 3-5 个场景（medium）/ 4-8 个场景（long）。
# 使用 #### 标记房间级子标题。
# NPC 对话使用 **NPC名**： 格式。
# DC 值标注为 (DC N)。
# 禁止输出其他内容，只输出 Markdown 正文。
"""
```

### 并发控制

1. spawn 前先检查 `maxConcurrentSubagents`（用 `my(action="check", key="subagents")` 确认有空位）
2. 如果并发上限已满，等已有 subagent 完成再 spawn 剩余
3. 使用 `my(action="check", key="subagents")` 等待所有完成

### 合并步骤

所有 subagent 完成后：
1. 用 `read_file` 依次读取 `_ch1.md`, `_ch2.md`, ...
2. 用 `write_file` 按顺序拼接写入目标文件
3. 合并完成后，继续后续步骤（导入、索引等）

---

## Step 0: Determine Type & Parameters

| Type | Chapters | Sessions | Steps |
|------|----------|----------|-------|
| **one-shot** | 1 | 1 (3-6h) | 1 |
| **short** | 3 | 3-8 | 1 |
| **medium** | 5 | 2-4 months | 3 |
| **long** | 8 | 6+ months | 5 |
| **sandbox** | 4-6 regions | open | 1+N |

Ask the user, or randomize. Tell them:

- 类型 / 范式 / 主题 / 环境 / 反派 / 等级 / 氛围
- 反转次数 / NPC 深度 / 结局分支 / 种子

### Paradigm Reference

| Type | Default Paradigms | Alternatives |
|------|-------------------|--------------|
| one-shot | Five-Room Dungeon, Mystery, Heist | Beat Charts, Reverse Dungeon |
| short | Three-Act, Kishōtenketsu | Race Against Time, Island Design |
| medium | Hero's Journey, Plot Point | Fish Tank, Pointcrawl, Faction Turn |
| long | Double Triangle, Conspyramid | Megadungeon, Technoir |
| sandbox | Hexcrawl, Node-Based | Decision-Based, Blorb, Faction Turn |

Full paradigm glossary: see bottom of this skill.

---

## One-shot & Short: One-Step Generation

Generate the complete module in one pass.

**Output path:** `<workspace>/modules/<name>.md`

**One-shot template:**

```markdown
# <模组名>

## 冒险概要
<2-3 sentences>

## 冒险背景
<3-5 sentences>

# <scene1: 开场/社交>
<NPC list, dialogue, mission hook>

# <scene2: 探索/地城>
<3-5 rooms with #### headings>

# <scene3: Boss/高潮>
<enemies, tactics, resolution>

# <scene4: 尾声>
<optional epilogue>

# 附录
## 主要 NPC | 怪物 | 魔法物品
```

**Short template (3 chapters, Three-Act):**

```markdown
# <模组名>

## 冒险概要 | 冒险背景 | 运作本模组

# 第一章：建立
## <scene1> ← NPC 引入 + 任务
## <scene2> ← 首次挑战 + 线索
## <scene3> ← 过渡/旅程

# 第二章：对抗
## <scene1> ← 中场反转
## <scene2> ← 核心地城 (4-8 rooms)
## <scene3> ← 揭示 + 选择点

# 第三章：解决
## <scene1> ← 最终逼近
## <scene2> ← Boss 战
## <scene3> ← 结局 (2-3 分支)

# 附录
## 主要 NPC (want/fear/secret) | 伏笔-回收表 | 怪物 | 魔法物品
```

After writing the file, import and export scene index:

```
dnd_module action=import campaign_id=<id> module_name="<name>" source_path="<workspace>/modules/<name>.md"
dnd_module action=index campaign_id=<id>
python -m nanobot.dnd.db.cli module export-scenes --campaign <id> --output "<workspace>/modules/<name>_scenes.json"
```

---

## Medium: Three-Step Generation

### Step M1 — 骨架（概念 + 势力 + NPC）

**Output:** `<workspace>/modules/<name>_skeleton.md`

```markdown
# <模组名>（骨架）

## 冒险概要
<5-8 sentences: 整个 5 章的完整故事线>

## 冒险背景
<8-12 sentences: 世界历史、当前局势、核心冲突>

## 运作本模组
<level range, pacing, key choices that shape the campaign>

## 势力关系网
| 势力 | 目标 | 盟友 | 敌对 | 首领 |
|------|------|------|------|------|
<3-5 factions>

## 章节大纲
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键 NPC | 反转/揭示 |
|----|------|---------|---------|---------|----------|
| 1 | ... | ... | ... | ... | - |
| 2 | ... | ... | ... | ... | ... |
| 3 | ... | ... | ... | ... | 中期危机 |
| 4 | ... | ... | ... | ... | ... |
| 5 | ... | ... | ... | ... | 最终揭示 |

## 主要 NPC（完整）
每人: name, race, class, alignment, want, fear, secret, chapter appearances

## 伏笔-回收表
| 伏笔 | 埋入章节 | 回收章节 | 内容 |
```

**User reviews this before proceeding.** Adjust based on feedback.

---

### Step M2 — 前 3 章正文（spawn 并行）

**Output:** 先写 3 个临时文件，再合并为 `<workspace>/modules/<name>_ch1-3.md`

**必须使用 `spawn` 并行生成 3 章。** 每章写入独立临时文件：

```
spawn(
  task="生成第1章正文，写入 <workspace>/modules/<name>_ch1.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 衔接：本章是冒险开篇，结尾埋下前往第2章的动机

## 本章大纲（来自骨架第1章行）
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键NPC | 反转/揭示 |
|---|---|---|---|---|---|
| 1 | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> |

## 输出格式
# 第一章：<标题>
## <场景1名称> ← <场景描述>
<NPC 对话、DC 值、奖励>
#### <房间名称> ← 地城场景时使用

## <场景2名称> ← <场景描述>
...

# 每章 3-5 个场景。使用 #### 标记房间。NPC 对话用 **NPC名**：。DC 标注为 (DC N)。
# 禁止输出其他内容，只输出 Markdown 正文。",
  label="Ch.1"
)

spawn(
  task="生成第2章正文，写入 <workspace>/modules/<name>_ch2.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 衔接：承接第1章结尾 <简述过渡点>，本章结尾设置中期危机伏笔

## 本章大纲（来自骨架第2章行）
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键NPC | 反转/揭示 |
|---|---|---|---|---|---|
| 2 | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> |

## 输出格式
# 第二章：<标题>
## <场景1名称> ← ...
...

# 每章 3-5 个场景。使用 #### 标记房间。禁止输出其他内容。",
  label="Ch.2"
)

spawn(
  task="生成第3章正文，写入 <workspace>/modules/<name>_ch3.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 衔接：承接第2章结尾 <简述过渡点>，本章结尾完成中期危机反转，自然过渡到第4章

## 本章大纲（来自骨架第3章行，含中期危机反转）
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键NPC | 反转/揭示 |
|---|---|---|---|---|---|
| 3 | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> | 中期危机 |

## 输出格式
# 第三章：<标题>
## <场景1名称> ← ...
...

# 每章 3-5 个场景。使用 #### 标记房间。禁止输出其他内容。",
  label="Ch.3"
)
```

**等待全部完成**：用 `my(action="check", key="subagents")` 确认 3 个 subagent 都 done。

**合并**：`read_file` 依次读取 `_ch1.md`, `_ch2.md`, `_ch3.md`，`write_file` 拼接写入 `_ch1-3.md`。

将骨架持久部分 + ch1-3 正文写入 `<workspace>/modules/<name>.md` 作为部分文件。用户审查后进入 M3。

---

### Step M3 — 后 2 章 + 附录（spawn 并行）

**Output:** 最终 `<workspace>/modules/<name>.md`

**必须使用 `spawn` 并行生成。** 第4章、第5章各一个 subagent，附录各部分再独立 spawn：

```
# 第4章
spawn(
  task="生成第4章正文，写入 <workspace>/modules/<name>_ch4.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 衔接：承接第3章中期危机后 <简述过渡>，本章结尾铺垫最终章

## 本章大纲（来自骨架第4章行）
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键NPC | 反转/揭示 |
|---|---|---|---|---|---|
| 4 | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> |

## 输出格式
# 第四章：<标题>
## <场景1名称> ← ...（3-5 个场景，规则同 M2）
# 禁止输出其他内容。",
  label="Ch.4"
)

# 第5章
spawn(
  task="生成第5章正文，写入 <workspace>/modules/<name>_ch5.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 衔接：承接第4章 <简述过渡>，本章为最终章，需要满足最终揭示

## 本章大纲（来自骨架第5章行，含最终揭示）
| 章 | 标题 | 核心冲突 | 情感节拍 | 关键NPC | 反转/揭示 |
|---|---|---|---|---|---|
| 5 | <从骨架复制> | <从骨架复制> | <从骨架复制> | <从骨架复制> | 最终揭示 |

## 输出格式
# 第五章：<标题>
## <场景1名称> ← ...（3-5 个场景）
# 禁止输出其他内容。",
  label="Ch.5"
)

# 附录各部分（可选，如果模块复杂可独立 spawn）
spawn(task="生成附录（NPC完整版、伏笔-回收表、势力变化）。基于骨架中的 NPC 数据和伏笔-回收表扩展。写入 <workspace>/modules/<name>_appendix.md", label="Appendix")
```

**等待全部完成**，然后组装最终文件：
- 骨架（概要、背景、运作、势力）
- Ch.1-3（来自 M2 的 `_ch1-3.md`）
- Ch.4-5（来自 `_ch4.md`, `_ch5.md`）
- 附录（来自 `_appendix.md`，含 NPC、伏笔-回收表、势力变化、怪物、魔法物品）

Import, index, and export scene index (see Import section).

---

## Long: Five-Step Generation

### Step L1 — 概念 + 双弧线大纲

**Output:** `<workspace>/modules/<name>_concept.md`

```markdown
# <模组名>（概念）

## 冒险概要 (10-15 sentences, full 8-chapter story)

## 反派时间线
| 阶段 | 章节 | 反派行动 | 玩家可见线索 |
<actions the villain takes even if players do nothing>

## 双弧线结构
### 第一弧：崛起 (Ch.1-4)
| 章 | 标题 | 核心冲突 | 虚假胜利的伏笔 |
### 第二弧：陨落与重生 (Ch.5-8)
| 章 | 标题 | 核心冲突 | 重生要素 |

## Ch.4 虚假胜利设计
<What makes players think they won? What did they actually cause?>

## Ch.8 多重结局设计
<3-5 ending branches based on accumulated player choices>

## 势力关系网 (5-8 factions, their evolution across 8 chapters)
## 主要 NPC (完整，含 arc)
```

User reviews. Adjust.

### Step L2 — 第一弧正文 (Ch.1-4)（spawn 并行）

**Output:** `<workspace>/modules/<name>_arc1.md`

**必须使用 `spawn` 并行生成 4 章。** 每章写入独立临时文件：

```
# 并行 spawn 4 个 subagent，每个一章
spawn(
  task="生成第1章正文，写入 <workspace>/modules/<name>_ch1.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 双弧线：本模块采用 Double Triangle 结构。第1章属第一弧（崛起）。
- 衔接：本章开篇，结尾引出第2章

## 本章大纲（来自 L1 概念中的双弧线结构表第1章行）
| 章 | 标题 | 核心冲突 | 虚假胜利伏笔/重生要素 |
|---|---|---|---|
| 1 | <从L1复制> | <从L1复制> | <从L1复制> |

## 输出格式
# 第一章：<标题>
## <场景1名称> ← <场景描述>
## <场景2名称> ← ...
# 每章 4-8 个场景。使用 #### 标记房间。NPC 对话用 **NPC名**：。DC 标注为 (DC N)。
# 禁止输出其他内容。",
  label="Ch.1"
)

spawn(task="生成第2章...", label="Ch.2")
spawn(task="生成第3章...", label="Ch.3")
spawn(
  task="生成第4章（虚假胜利章）正文，写入 <workspace>/modules/<name>_ch4.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 双弧线：本章是第一弧终点 + 虚假胜利。玩家以为赢了，实际埋下第二弧的祸根。
- 衔接：承接第3章，结尾埋下第5章的陨落伏笔

## 虚假胜利设计（来自 L1）
<从 L1 的 Ch.4 虚假胜利设计复制>

## 本章大纲（来自 L1 双弧线结构表第4章行）
| 章 | 标题 | 核心冲突 | 虚假胜利伏笔 |
|---|---|---|---|
| 4 | <从L1复制> | <从L1复制> | <从L1复制> |

## 输出格式
# 第四章：<标题>
# 每章 4-8 个场景。禁止输出其他内容。",
  label="Ch.4-false-victory"
)
```

**等待全部完成**，`read_file` 依次读取 `_ch1.md` ~ `_ch4.md`，`write_file` 拼接写入 `_arc1.md`。

### Step L3 — 第二弧正文 (Ch.5-8)（spawn 并行）

**Output:** `<workspace>/modules/<name>_arc2.md`

**必须使用 `spawn` 并行生成 4 章。** 每章写入独立临时文件：

```
# 并行 spawn 4 个 subagent，每个一章
spawn(
  task="生成第5章正文，写入 <workspace>/modules/<name>_ch5.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 双弧线：本章是第二弧开端（陨落）。承接第4章虚假胜利的后果，局势急转直下。
- 衔接：承接第4章虚假胜利，结尾埋下第6章的进一步恶化

## 本章大纲（来自 L1 双弧线结构表第5章行）
| 章 | 标题 | 核心冲突 | 重生要素 |
|---|---|---|---|
| 5 | <从L1复制> | <从L1复制> | <从L1复制> |

## 输出格式
# 第五章：<标题>
# 每章 4-8 个场景。禁止输出其他内容。",
  label="Ch.5-fall"
)

spawn(task="生成第6章...", label="Ch.6")
spawn(task="生成第7章...", label="Ch.7")
spawn(
  task="生成第8章（最终章）正文，写入 <workspace>/modules/<name>_ch8.md

## 全局约束
- 模组名：<name>，主题：<theme>，基调：<tone>
- 等级范围：<level>，核心反派：<villain>
- 双弧线：本章是第二弧终点 + 重生。需要包含多重结局分支。

## 多重结局设计（来自 L1）
<从 L1 的 Ch.8 多重结局设计复制，3-5 个分支>

## 本章大纲（来自 L1 双弧线结构表第8章行）
| 章 | 标题 | 核心冲突 | 重生要素 |
|---|---|---|---|
| 8 | <从L1复制> | <从L1复制> | <从L1复制> |

## 输出格式
# 第八章：<标题>
# 每章 4-8 个场景。包含结局分支段落。禁止输出其他内容。",
  label="Ch.8-finale"
)
```

**等待全部完成**，`read_file` 依次读取 `_ch5.md` ~ `_ch8.md`，`write_file` 拼接写入 `_arc2.md`。

### Step L4 — 角色个人线 + 附录（spawn 并行）

**Output:** 多个临时文件，后续组装时合并

**必须使用 `spawn` 并行生成。** 每个 PC 个人线 + 附录各部分都独立 spawn：

```
# PC 个人弧线（每个 PC 一个 subagent）
spawn(
  task="为 PC1（<角色名/职业>）生成 2-3 场景个人弧线，写入 <workspace>/modules/<name>_pc1.md

## 全局约束
- 模组名：<name>，主题：<theme>，反派：<villain>
- 角色简要：<PC1 的描述>
- 与主线相交点：<在哪些章节与主线交汇>

## 输出格式
# PC个人线：<角色名>
## <场景1> ← 与主线第X章相交
## <场景2> ← 与主线第Y章相交
## <场景3> ← 结局个人收束
# 禁止输出其他内容。",
  label="PC1-arc"
)

spawn(task="为 PC2 生成个人弧线...", label="PC2-arc")
# ...每个 PC 一个 spawn

# 附录各部分
spawn(
  task="生成 NPC 附录（含完整弧线）。基于 L1 概念中的 NPC 数据，为每个 NPC 补充 8 章完整弧线演变。
写入 <workspace>/modules/<name>_appendix_npc.md",
  label="Appendix-NPC"
)

spawn(
  task="生成伏笔-回收表（8 章完整链）。追踪每条伏笔在哪个章节埋下、哪个章节回收、具体内容。
写入 <workspace>/modules/<name>_appendix_foreshadow.md",
  label="Appendix-Foreshadow"
)

spawn(
  task="生成势力变化时间线。追踪每个势力在 8 章中的演变（目标变化、盟友变化、力量消长）。
写入 <workspace>/modules/<name>_appendix_faction.md",
  label="Appendix-Faction"
)

spawn(
  task="生成怪物附录（含 Boss 多阶段）和魔法物品附录。
写入 <workspace>/modules/<name>_appendix_bestiary.md",
  label="Appendix-Bestiary"
)
```

**等待全部完成**，所有临时文件在 L5 组装步骤中合并。

### Step L5 — 组装

Combine all parts into `<workspace>/modules/<name>.md`. Import, index, and export scene index (see Import section).

---

## Sandbox: 1+N Step Generation

### Step S1 — 世界骨架

**Output:** `<workspace>/modules/<name>_world.md`

```markdown
# <沙盒名>（世界）

## 世界概况
<8-12 sentences: map scope, core conflict, faction landscape>

## 区域总览
| 区域 | 地形 | 控制势力 | 危险等级 | 关键地标 |
<4-6 regions>

## 势力关系网
| 势力A | 关系 | 势力B | 说明 |

## 随机遭遇总表 (1d12)
## 关键 NPC 总表
```

### Step S2-N — 每个区域独立生成（spawn 并行）

用户说"全部区域"时，**必须使用 `spawn` 并行生成所有区域**。每个区域写入独立临时文件：

```
# 并行 spawn 每个区域
spawn(
  task="生成区域1：<区域名>，写入 <workspace>/modules/<name>_region1.md

## 全局约束
- 沙盒名：<name>，主题：<theme>，基调：<tone>
- 区域1来自世界骨架：<地形/控制势力/危险等级/关键地标>

## 区域大纲（来自 S1 区域总览表第1行）
| 区域 | 地形 | 控制势力 | 危险等级 | 关键地标 |
|---|---|---|---|---|
| 1: <名> | <从S1复制> | <从S1复制> | <从S1复制> | <从S1复制> |

## 输出格式
# 区域1：<名称>
## 区域特征 (3-5 sentences)
## 势力 (who controls this, what they want)
## 事件线 (2-4, each: trigger→process→outcome)
## 地点 (3-6 key locations with #### room headings)
# 禁止输出其他内容。",
  label="Region1"
)

spawn(task="生成区域2...", label="Region2")
spawn(task="生成区域3...", label="Region3")
# ...每个区域一个 spawn
```

**等待全部完成**，`read_file` 依次读取各临时文件，按区域编号顺序 `write_file` 追加到 `<workspace>/modules/<name>.md`。

如果用户说"生成区域X"（单个），直接生成该区域，不用 spawn。

After all regions: import, index, and export scene index (see Import section).

---

## Import & Scene Index

After the module file is complete:

```
# 1. Import into database
dnd_module action=import campaign_id=<id> module_name="<name>" source_path="<workspace>/modules/<name>.md"

# 2. Verify structure
dnd_module action=index campaign_id=<id>

# 3. Export scene index JSON (saved alongside the .md file)
python -m nanobot.dnd.db.cli module export-scenes --campaign <id> --output "<workspace>/modules/<name>_scenes.json"
```

The `_scenes.json` file mirrors the structure of `references/dnd-dm-skill/srd/scenes_index.json`:
- Scene boundaries with line numbers
- Keywords/tags per scene
- Room-type annotations
- Sub-section headings

This gives the DM a human-readable scene map they can reference without querying the database.

Report chapter/scene/chunk counts after import. If module already exists, ask user before deleting and re-importing.

---

## Paradigm Glossary

**Space-driven:**
- **Five-Room Dungeon**: entrance guardian → puzzle/RP → trick/setback → climax → reward/revelation
- **Node-Based**: nodes (places, NPCs, events) connected by clues/geography/time. Non-linear.
- **Hexcrawl**: hex grid map, each hex has encounters/locations. Random encounter tables.
- **Megadungeon**: multi-level dungeon, looping routes, factions within.
- **Island Design**: independent modular elements, any order.

**Story-driven:**
- **Three-Act**: establish → confront → resolve. Midpoint twist in Ch.2.
- **Hero's Journey**: ordinary world → cross threshold → trials → abyss → return.
- **Double Triangle**: rise (Ch.1-4) → fall (Ch.5-6) → redemption (Ch.7-8). Ch.4: false victory.
- **Kishōtenketsu** (起承转合): introduce → develop → twist → harmony. No required antagonist.
- **Beat Charts**: Hook → alternating Developments & Cliffhangers → Resolution.
- **Hamlet's Hit Points**: alternating hope/fear beats.
- **Seven-Point Story**: Hook → PT1 → Pinch1 → Midpoint → Pinch2 → PT2 → Resolution.

**Play-driven:**
- **Heist**: intel → planning → execution → complication → escape.
- **Mystery**: Hook → 3 cool locations → 3 clues per node → reveal. Three Clue Rule.
- **Conspyramid**: 6-layer conspiracy + 6-layer response pyramid.
- **Faction Turn**: factions advance independently of players.
- **Race Against Time**: clock: X rounds to complete Y.
- **Survival**: resource management focus.

**Character-driven:**
- **Plot Point Campaign**: main Episodes + character Savage Tales.
- **Fish Tank Intrigue**: events + factions; players are the variable dropped in.
- **Technoir Transmission**: 36-node table, 2d6 pick 3 seeds, define triangle.
- **Decision-Based**: clear choices → modular world → adaptation.
- **Blorb**: prep entities not plots. Prep > rules > improv.

**Hybrid:**
- **Iceberg Diagram**: surface hook → hidden depths.
- **Reverse Dungeon**: players defend, monsters attack.
