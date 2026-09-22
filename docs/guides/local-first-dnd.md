# 本地优先 DND

源码工作区版本：Core 0.3.2、DND Runtime/MCP 0.3.1。此改造没有发布包或更新远端
release manifest；旧的发布锁不能证明包含这些功能。使用同级源码工作区安装：

```powershell
rtk uv run nanobot sagasmith install --source workspace --mode dnd --transport stdio --skip-ui
rtk uv run nanobot sagasmith doctor
rtk uv run nanobot sagasmith start
```

已有运行实例需先通过 `nanobot sagasmith stop` 正常停止，再重新安装配置。
配置器备份配置并保留 provider、secret、channel、无关 MCP 和 Skills。
此文的验证使用临时存档，没有修改用户战役或运行配置。

## 本地执行

- Host 启动一个 stdio Runtime，启动配置绑定 `system:local`；模型不能选择身份。
  本地模式不携带 HTTP 签名委托、不启用 session-scoped 子进程，也不启动 DND Gateway。
- Runtime 持有数据库生命周期排他锁。其他采用新版 Core 的 Runtime/共享服务不能同时
  打开同一数据库；异常退出由操作系统释放锁。锁文件不应删除。外部直接 SQLite 编辑
  不属于这个协作锁协议。
- 一个命令队列管理当前 campaign/actor、revision、branch/head 和 scene state version。
  CAS、事务、随机流、幂等回执、审计仍使用共享实现；不是另一套规则或存档格式。
- Host 在发送前持久化 operation ID；Runtime 在执行前持久化原请求与补齐后的协议参数。
  未知结果只恢复相同请求。不同写入会被 Host 阻止，直至原请求得到明确结果。
  已完成的旧回执带 `receipt_is_current`；当前状态另放在 `local_context`。
- branch/restore/timeline、规则、授权、受众变化失效旧上下文；玩家受众不会收到 DM 私有
  actor 切片。恢复后的历史操作不能在新时间线上重新执行。NPC 知识和事实提交边界保持有效。

备份需要同时保留领域 home 下的数据库、Snapshot 与 `runtime/local-commands`，以及
Agent session 文件；只复制 SQLite 文件无法恢复 Host 尚未确认的请求。现有栈备份工具
保留领域数据，Agent session 应按其工作区备份策略一并保留。

## 模型调用与超时

服务端 `tools/list` 保持稳定。Host 按阶段呈现小型日常工具集；
`mcp_<server>_local_capabilities` 按名称或描述检索并加入额外工具。
模型参数隐藏 Host 管理的身份、revision、幂等键、分支，以及已有的 campaign/actor。
普通完整攻击一次提交并返回随机回执和受影响状态；玩家反应或缺失裁决仍保持 pending。

默认读调用 30 秒、写调用 120 秒、长任务 900 秒，三个值可分别配置。
超时不是失败提交的证明。子 agent 可以提出行动，不能通过本地 MCP 提交写入。
Skills 复用有效 Host 状态和写回执，不再要求每轮全量读卡、读战役、写后全量回读。
不改变事实、资源、承诺或信息披露的环境描写可以直接叙述。

本地默认不预热 PDF/OCR/向量服务，也不在启动时重新扫描 SRD 文本索引。
内置确定性规则仍可使用；需要文本检索时通过 supplemental 工具调用 `rule_seed_bundled`，
或显式设置 `SAGASMITH_DND_MCP_AUTO_SEED=1`。既有文本索引不会被删除。

## 验证与性能

DND 安装器使用常规 CPython 3.12，避免 Windows 自动选中自由线程解释器后无法安装
`pywin32`。已有个人配置与战役迁移仍应使用显式安装路径和备份流程。

可复现的真实 Host → stdio → Runtime 基准（无需 provider）：

```powershell
rtk .venv\Scripts\python.exe -m nanobot.sagasmith_local.local_benchmark --dnd-python ..\Sagasmith-dnd\.venv\Scripts\python.exe --output local-benchmark.json
```

[2026-09-21 本机原始结果](../verification/local-authority-20260921.json)：冷连接约 2.89 秒；
5 次原子物品转移中位数约 54.2 毫秒；5 次 binding 查询中位数约 11.2 毫秒。
Lobby 投影 20 个工具，schema UTF-8 共 29,537 字节。时间受机器负载影响，不能外推为
真实 LLM 回合速度或跨版本性能提升。

以上为空内容库基准。完整内容库必须同时提供 SRD skills 路径；用以下命令在临时数据库中
分别测量首次导入和已安装内容的重启，不修改现有战役：

```powershell
rtk .venv\Scripts\python.exe -m nanobot.sagasmith_local.local_benchmark --dnd-python ..\Sagasmith-dnd\.venv\Scripts\python.exe --dnd-skills ..\Sagasmith-dnd\skills --official-library ..\local-content-20260922 --measure-restart --output local-library-benchmark.json
```

`--official-library` 应指向自行验证的本地内容库。首次安装需要导入内容，不能与空库冷连接混算；
`restart.cold_connection_ms` 单独记录复用已安装内容时的连接耗时。测量过程不用 LLM，
也不修改输入档案。可参见 [2026-09-22 验证记录](../verification/local-authority-20260922.md)。

回合指标保存在 Agent session 的 `local_turn_metrics`，包含 LLM/工具调用次数及耗时、
读取、重试、超时、数据库查询与耗时、首段完整叙事耗时。首段完整叙事不是 streaming
首 token 时间。Runtime 回执的 `local_execution` 还包含排队时间；不记录提示词或秘密。
LLM 次数按 Host 请求统计，包含 Host 的格式修复重试；provider 内部网络重试未单独展开。

自动验证覆盖进程排他与异常释放、丢失响应后重启、相同攻击不重掷、快照恢复失效、
玩家视角、Host 持久化操作身份、子 agent 写入限制、真实 MCP/Host stdio，以及共享契约。
