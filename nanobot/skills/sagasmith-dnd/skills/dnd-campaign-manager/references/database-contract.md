# Runtime 数据契约

Skill 不直接访问数据库。所有写入通过 `sagasmith-dnd --json`。

- SQL 是真实数据源。
- 规则、模组和 embedding 是静态索引。
- 战役、角色、场景进度、快照和记忆是可变状态。
- PC、NPC、怪物全部是同一 `Character` 模型，必须通过 `character_type` 区分，不能在
  campaign state 或 prose note 中维护第二张简化卡。所有活跃 actor 使用完整 `sheet v2`。
- 公用角色库可保存 PC、NPC、怪物模板；三种类型也都可以直接创建为 campaign 实例。
  `character instantiate` 从任意库模板复制实例；`character build` 是 PC 车卡的原子操作，
  同时创建库模板和 campaign 实例。实例带 `template_id` 溯源，但与模板独立更新。
- `sheet v2` 是数值、资源、法术、效果和个人背包的权威；`notes v2` 是描述、重要对话
  记忆、关系和目标的权威。NPC 与怪物的 `notes.profile.summary` 均为必填。
- `character show` 的 `derived` 是只读计算结果，不能写回或手改。
- 队伍共享背包和钱包位于 `campaign.state.party.inventory`，且与个人背包使用相同结构。
- 物品、钱包、装备、准备法术、效果和 NPC 记忆必须使用对应 `character`/`party` CLI
  命令；不得用 `character update --sheet` 局部篡改。
- 物品有稳定 `id`；装备状态必须同时更新 item 的 `equipped`/`equipped_slot` 与
  `equipment_slots` 映射，因此只能用 `character equipment equip|unequip`。AC 以
  `derived.armor_class_breakdown` 为准，不手改基础 AC 模拟装备。
- 战役事件是时间顺序；战役 memory 是跨会话事实；NPC/怪物 memory 是该 actor 的重要
  对话与事件摘要。三者都必须与角色卡和场景状态表达同一事实。
- Snapshot v2 回滚完整可变游戏状态和撤销游标：campaign、actor cards、party stash、
  scene progress、events、active memories。快照只捕获 `campaign_id` 匹配的实例，不回滚
  公用角色库模板；规则、模组原文和 embeddings 不在快照中。
- 快照不复制规则或模组原文。
- Dense 不可用时 Runtime 自动保留 exact/lexical 检索。
- 每个战役的 edition、locale 和 publications 由 rule profile 决定。

完整字段见 `../../../references/character-schema-v2.md`。
