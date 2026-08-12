# Windows 本地 D&D 系统安装

当前入口以 D&D 为核心，在 Windows 11 的同级多仓库工作区中安装 SagaSmith Agent、
D&D 权威运行时、Agent/D&D 两个 Web UI、D&D/ModuleGen Skills，以及可再分发的 D&D
内容目录。此阶段不安装或启动 CoC。

## 安装边界

`install-all.bat` 会使用已提交的锁文件同步 Agent 与 D&D MCP 环境，后者通过 editable
sources 安装 `sagasmith-core` 和 `sagasmith-dnd`。它还会构建两个 UI、检查 Skills、验证
公开内容目录，并创建 repo-local D&D 数据目录。

安装器不会 clone/pull 仓库、覆盖 `config/config.json`、读取 provider secret、修改战役、
导入或激活 Pack，也不会复制不可再分发的规则书或模组。

## 前置条件与目录

需要 `uv`、Python 3.11+、Node.js 22.12+ 和 npm。以下仓库应位于同一目录：

```text
SagaSmith/
  SagaSmith-agent/
  sagasmith-core/
  sagasmith-dnd/
  SagaSmith-dnd-mcp/
  SagaSmith-dnd-skills/
  SagaSmith-module-gen-skills/
  SagaSmith-dnd-content-library/
  SagaSmith-dnd-ui/
```

## 安装与验证

```powershell
cd C:\path\to\SagaSmith\SagaSmith-agent
.\install-all.bat

# 只验证，不安装或构建
.\install-all.bat --verify-only

# 仅用于后端排障，不代表完整产品安装
.\install-all.bat --skip-ui
```

首次使用时创建 repo-local Agent 配置：

```powershell
uv run nanobot onboard --wizard --config config\config.json --workspace workspace
```

然后显式写入 D&D-first 本地连接。该操作会保留 provider、secret 和无关 MCP server，
移除已知的 `sagasmith_coc` 运行项，并先生成 `config/config.json.bak`：

```powershell
.venv\Scripts\python.exe scripts\configure_dnd_local.py --apply
.\install-all.bat --verify-only
```

详细协议见 [MCP 配置指南](configure-mcp-tools.md)。D&D 使用
`http://127.0.0.1:8767/mcp`、`enabledTools: ["*"]` 和原生
`tools/list_changed`；不保留 stdio 子进程、固定工具全集或文本 fallback。

## 启动与停止

```powershell
.\start.bat

# 可从另一个终端停止三个精确的本地进程
.\stop.bat
```

`start.bat` 会先执行配置预检，再启动唯一的权威 D&D MCP、连接该 MCP 的 D&D Workbench
gateway，以及 SagaSmith Agent。Workbench 默认位于 `http://127.0.0.1:8766/`；Agent
WebUI 端口来自 repo-local 配置。两端都提供跳转入口。

启动脚本把三个进程的 PID 写入 `workspace/.sagasmith-runtime.json`。正常退出或
`stop.bat` 会停止这些精确进程并删除标记。Agent 后台日志位于 `workspace` 中的
`sagasmith-agent.stdout.log` 和 `sagasmith-agent.stderr.log`。

## 数据检查、备份与恢复

以下操作不包含 `config/config.json` 或商业源文件。备份和恢复要求 `start.bat` 已停止；
恢复前会校验 manifest 与每个文件的 SHA-256，并把原 workspace 保留为
`workspace.pre-restore-*`。

```powershell
.venv\Scripts\python.exe scripts\local_dnd_data.py doctor
.venv\Scripts\python.exe scripts\local_dnd_data.py backup C:\backups\sagasmith-dnd.zip
.venv\Scripts\python.exe scripts\local_dnd_data.py verify C:\backups\sagasmith-dnd.zip
.venv\Scripts\python.exe scripts\local_dnd_data.py restore C:\backups\sagasmith-dnd.zip --yes
```

## 私有内容边界

公开目录只包含可再分发的 SRD Pack。用户拥有的商业规则书与模组只能在本地私有环境中
进入 `draft → Agent 证据审核与修复 → finalize` 生命周期。安装成功不会把任何内容
静默放入战役；导入、依赖解析和激活仍属于 Lobby 内容控制。
