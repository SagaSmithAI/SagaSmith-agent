# 安装与运行 SagaSmith 本地服务

SagaSmith Agent 是统一入口。D&D、CoC 和 Narrative 是三个彼此独立的可选模式；
选择其中一个不会隐式安装或配置另外两个。

当前实现只有 Python CLI，不再提供 BAT 安装、启动或停止协议。

## 前置条件

- Python 3.11+ 与 `uv`
- 构建 Web UI 时需要 Node.js 22.12+ 与 npm
- `--source workspace` 需要同级源码仓库
- 启动前需先完成 Agent provider 配置

首次创建 Agent 配置：

```powershell
uv run nanobot onboard --wizard --config config\config.json --workspace workspace
```

## 选择模式

重复 `--mode` 可安装任意组合；不传 `--mode` 时安装三个模式。

```powershell
# 仅 D&D
uv run nanobot sagasmith install --mode dnd

# CoC 与 Narrative
uv run nanobot sagasmith install --mode coc --mode narrative

# 三个模式
uv run nanobot sagasmith install
```

`--skip-ui` 用于仅后端开发安装，`--verify-only` 用于只读审计。
`--source release` 默认按仓库内的 `sagasmith-stack-lock.json` 为每个组件检出独立的
不可变 commit；`--release-manifest <path>` 可选择另一份已审计版本锁。只有同一 tag
确实存在于所有所选仓库时，才使用 `--release-ref <coordinated-tag>`。发布检出不会修改
开发工作区。

内置 v3 锁要求 MCP 2026-07-28、auth-context v2 与共享 authoritative-MCP 契约，且只
包含 Core 和三个当前领域仓库。含未知组件（包括已归档拆分仓库）的 manifest 会在 clone
前被拒绝。
它是尚未发布的兼容锁，不会发布 package、image、tag 或 GitHub Release。

配置器只维护 SagaSmith 自己的 MCP 与 Skills 字段；修改前备份现有配置，
并保留 provider、secret、channel、无关 MCP 和无关 Skills 路径。

## 生命周期

```powershell
uv run nanobot sagasmith doctor
uv run nanobot sagasmith start
uv run nanobot sagasmith status
uv run nanobot sagasmith logs
uv run nanobot sagasmith stop
```

精确子进程 PID、命令和日志位于 `workspace/.sagasmith-local`。停止命令只处理
该清单记录的进程。

默认端口：Agent WebUI 8765、D&D Gateway 8766、D&D MCP 8767、
CoC Gateway 8768、CoC MCP 8769。Narrative 由 Agent 按会话启动 stdio。

现代 MCP 目录对同一 authorization 保持确定、有序并使用 private cache scope。Agent
按当前 system、phase 与 task 只选择有界 facade 子集；每次调用仍由 MCP 独立校验身份、
战役、角色、revision、expiry 与 allowed operation。只有显式 legacy 回滚适配器使用
session-local exposure 与 `tools/list_changed`。

## 备份、恢复与升级

执行以下操作前先停止本地服务：

```powershell
uv run nanobot sagasmith backup C:\backups\sagasmith.zip
uv run nanobot sagasmith verify-backup C:\backups\sagasmith.zip
uv run nanobot sagasmith restore C:\backups\sagasmith.zip --yes
uv run nanobot sagasmith upgrade
uv run nanobot sagasmith rollback --yes
```

备份保留三个相互独立的领域数据目录和栈清单，不包含 provider secret、日志、
原书或源码检出。升级会拒绝脏仓库，先备份，再仅快进更新、重装并验证；回滚同样
拒绝脏仓库。

若只需紧急回滚协议，可在保持相同锁定组件和签名授权的前提下，把受影响服务的
`protocolMode` 设为 `legacy`。不得恢复已归档仓库，也不得把 `Mcp-Session-Id` 当成身份
边界；兼容故障解决后应恢复 `2026-07-28`。

卸载默认保留数据；只有显式 `--purge-data` 才删除领域数据：

```powershell
uv run nanobot sagasmith uninstall --yes
uv run nanobot sagasmith uninstall --yes --purge-data
```

软件安装不会导入或激活任何 Pack。私有书籍与模组仍须经过机械草稿、Agent 证据
审计、不可变 finalize、Lobby 导入和显式激活。
