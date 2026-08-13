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
`--source release --release-ref <tag-or-branch>` 会把发布检出放在独立目录，
不修改开发工作区。

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

每个 Agent 逻辑会话都有独立 SagaSmith MCP 连接和动态工具注册表，
`tools/list_changed` 不会污染其他聊天的战役、阶段或身份上下文。

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

卸载默认保留数据；只有显式 `--purge-data` 才删除领域数据：

```powershell
uv run nanobot sagasmith uninstall --yes
uv run nanobot sagasmith uninstall --yes --purge-data
```

软件安装不会导入或激活任何 Pack。私有书籍与模组仍须经过机械草稿、Agent 证据
审计、不可变 finalize、Lobby 导入和显式激活。
