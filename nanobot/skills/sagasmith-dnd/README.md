# 🐉 SagaSmith D&D Skills — 完整版

[中文](README.md) | [English](README-en.md)

<p align="center"><img src="images/Sagasmith.png" alt="SagaSmith" width="200"></p>

**跨平台 D&D 5e 2014/2024 Agent Skills** — 为 Claude Code、NanoBot、Codex、Cursor 等所有支持 SKILL.md 标准的 Agent 平台提供 D&D 地下城主持能力。

> *"骰子已经掷下。"*

完整模式需要安装 `sagasmith-core` 和 `sagasmith-dnd`。所有 Agent 平台通过同一 `sagasmith-dnd --json` CLI 操作——本仓库不包含数据库、向量运行时、游戏引擎或平台专属工具。

---

## 生态

| 仓库 | 定位 |
|------|------|
| 📦 **SagaSmith-dnd-skills**（本仓库） | D&D Agent Skill 定义 |
| ⚔️ [sagasmith-dnd](https://github.com/dajiaohuang/sagasmith-dnd) | D&D 5e 运行时 + CLI |
| 🏗️ [sagasmith-core](https://github.com/dajiaohuang/sagasmith-core) | 通用引擎 — DB、文档、RAG |
| 🎲 [SagaSmith-agent](https://github.com/dajiaohuang/SagaSmith-agent) | 完整 AI DM 运行时 |
| ✍️ [SagaSmith-module-gen-skills](https://github.com/dajiaohuang/SagaSmith-module-gen-skills) | 冒险模组生成器 |

---

## Skill 清单

| Skill | 文件 | 职责 |
|-------|------|------|
| 🎲 **dnd-dm** | `skills/dnd-dm/SKILL.md` | 核心 DM 人格（always-on）、规则裁判、战斗引擎、SRD 检索、作用域式场景追踪 |
| 📋 **dnd-campaign-manager** | `skills/dnd-campaign-manager/SKILL.md` | 战役生命周期、Snapshot 存档/读档、模组导入、USER.md 同步 |

---

## 使用场景

### 需要 Runtime（持久化能力）

```powershell
sagasmith-dnd module current --campaign <id> --scope player:alice --json
sagasmith-dnd module set-progress --campaign <id> --scope party --scene <scene-id> --progress 50 --state '<merged-json>' --json
sagasmith-dnd save create --campaign <id> --label "进入地城前" --json
```

### Portable 模式（无安装）

未安装 Runtime 时可查阅随附 SRD，但不能持久化战役。适用于快速规则查询和备课。

---

## 规则语料

| 语料 | 版本 | 语言 |
|------|------|------|
| SRD 5.2.1 | 2024 | 英文 |
| SRD 5.1 | 2014 | 英文 |
| SRD 5.1 | 2014 | 中文便利译本 |

---

## 带团状态机

每次处理玩家行动：

1. **Scope 决议** — 根据行动者选择 `party` / `group:<id>` / `player:<id>`，调用 `module current` 获取当前场景
2. **场景读取** — 读取该 scope 的当前场景；搜索候选后用 `expand` 确认再使用
3. **按类型执行** — `intro` 铺垫 / `combat` 开战 / `dungeon` 按 subsection 推进 / `transition` 核验条件
4. **State 合并** — 先读取 `progress.state`，合并新事实后整体写回
5. **记录与保存** — 事件日志、记忆、关键节点创建 Snapshot

---

## 快速安装

```bash
# Claude Code / Codex / Cursor
npx skills add dajiaohuang/SagaSmith-dnd-skills
```

---

## 许可证

MIT
