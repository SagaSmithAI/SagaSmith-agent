# 角色创建与升级

## 创建流程

1. 读取战役 edition、locale、可用 publication 和可选规则。
2. 确认玩家概念、等级及是否使用预设、标准数组、购点或掷骰。2014 只能使用代码校验的
   `standard_array`、`point_buy` 或 `roll_4d6_drop_lowest`；所有随机结果通过 CLI，并在
   玩家看到六组结果后才允许分配。
3. 按版本依次选择物种/种族、职业、背景、属性分配、熟练项、语言、工具、装备、
   职业特定选项与法术。每一步只展示该规则档案允许的选项。
4. 填写 `sheet v2`：完整属性、18 项技能表、战斗数值、特质、资源、法术、特性、效果和
   背包。六项属性均写入 score、豁免熟练和永久 bonus；18 项技能全部保留，未熟练者明确
   为 `proficiency: "none"`。法术使用 `spellcasting` 与 `content.spells`，将当天已准备
   法术写入 `preparation.selected_spell_ids`；法术书、始终准备和仪式资格不能混为一项。
5. 填写 `notes v2`：姓名、外观、动机、关系、信念及玩家名；PC、NPC 与怪物都必须有一段
   `profile.summary`。重要 NPC 对话只把未来裁决所需的摘要写入 `notes.memories`。
6. 展示完整草稿和来源；玩家最终确认前不得创建正式角色。
7. 创建后重新读取并验证派生值、资源上限和 campaign/player 绑定。

## 车卡提交清单

- `progression`：等级、经验、职业/子职、生命骰、背景、物种。
- `abilities` 与 `skills`：六项属性、豁免熟练，及完整 18 项技能表；详见
  `character-schema-v2.md` 的 Skill Table。
- `ability_generation`：记录标准数组、27 点购点或六组 4d6 去最低骰的生成证据和分配。
  种族调整、ASI 与其他后续合法加值可以改变当前属性，但不改写车卡基线。
- `combat` 与 `traits`：当前/最大/临时 HP、基础或覆写 AC、先攻、全部移动方式、生命骰、
  死亡豁免、力竭、体型、语言、工具/武器/护甲熟练、感官和伤害/状态防御。
- `resources`、`content.features`、`content.feats`、`content.activities`：每个有限能力的
  当前值、上限、恢复周期和规则来源；攻击、动作与特性不能只写在叙事里。
- `spellcasting` 与 `content.spells`：施法属性、法术位、契约魔法、准备模式/上限、法术书、
  仪式资格，以及每个法术的来源与访问状态。
- `inventory`：cp/sp/ep/gp/pp 钱包、每件起始物品的稳定 ID、名称、简短描述、数量、重量、
  价值、来源、容器、鉴定、同调、uses/charges 和 mechanics。起始护甲、盾牌、武器和防御
  魔法物品必须写入兼容装备槽，并以 `derived.armor_class_breakdown` 复核 AC。
- `conditions`、`effects` 和 `notes`：现有效果的 period/remaining/changes，人物文字设定、
  关系、目标和重要记忆。PC、NPC、怪物均需 `profile.summary`。

新玩家使用逐步对话；熟悉规则的玩家可一次提交草稿，但仍须逐项验证并最终确认。
PC、NPC 和怪物都使用同一完整角色卡，也都可以保存为公用模板或直接创建为战役实例。
PC 车卡使用 `character build`，一次原子创建公用模板与当前战役实例；不能把模板本身
当作活跃状态。不能用只含 AC/HP 的临时 JSON 代替完整卡。

## 升级

从当前角色增量升级，不得重建并覆盖既有选择。依次处理 HP、职业/子职能力、熟练加值、
技能与豁免、专长或属性提升、法术与法术位、装备资源及所有派生值。验证后提交，记录
升级事件并创建 Snapshot。

```powershell
sagasmith-dnd character ability roll --edition 2014 --json
sagasmith-dnd character build --campaign <id> --name "<name>" --type pc --player "<player>" --sheet '@<pc-sheet.json>' --notes '@<pc-notes.json>' --json
sagasmith-dnd character build --campaign <id> --name "<name>" --type pc --player "<player>" --ability-method standard_array --assignments '<six-ability-json>' --sheet '@<pc-sheet.json>' --notes '@<pc-notes.json>' --json
sagasmith-dnd character create --campaign <id> --name "<name>" --type npc|monster --sheet '@<sheet.json>' --notes '@<notes.json>' --json
sagasmith-dnd character instantiate --id <template-id> --campaign <id> --json
sagasmith-dnd character list --campaign <id> --json
sagasmith-dnd character show --id <character-id> --json
```

角色 sheet 必须是 `schema_version: 2`。创建后执行 `character show`，核对完整 `sheet`
和只读 `derived` 中的熟练、豁免、技能、AC、HP、速度、法术 DC、已准备法术和负重。
字段与草稿例子见 `../../../references/character-schema-v2.md`。
