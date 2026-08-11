# Windows 全工作区安装

本流程从源码安装当前 SagaSmith Agent、D&D/CoC 权威运行时、三个 Web UI、Agent Skills 与可再分发内容目录。它适合把多个独立仓库作为同级目录维护的 Windows 11 工作区。

## 1. 安装边界

`install-all.bat` 会：

- 用各自的 `uv.lock` 同步 Agent 全部 extras、D&D MCP 和 CoC MCP；
- 通过两个 MCP 的 editable sources 安装 `sagasmith-core`、`sagasmith-dnd` 和 `sagasmith-coc`；
- 用 `npm ci` 构建 Agent WebUI、D&D UI 和 CoC UI；
- 核对 Full D&D、Full CoC 和当前 Module Pack generation Skill；
- 校验 `SagaSmith-dnd-content-library` 中已提交的公开目录；
- 创建两个空的 repo-local MCP home（若尚不存在）。

它不会 clone/pull 仓库、覆盖 `config/config.json`、读取 provider secret、修改 campaign 数据、导入/激活 Pack，或复制不具备再分发许可的书籍与模组。

## 2. 前置条件与目录

需要：

- Windows 11；
- `uv` 与 Python 3.11+；
- Node.js 22.12+ 与 npm；
- 下列仓库均位于 `SagaSmith-agent` 的父目录。

```text
SagaSmith/
  SagaSmith-agent/
  sagasmith-core/
  sagasmith-dnd/
  sagasmith-coc/
  SagaSmith-dnd-mcp/
  SagaSmith-coc-mcp/
  SagaSmith-dnd-skills/
  SagaSmith-coc-skills/
  SagaSmith-module-gen-skills/
  SagaSmith-dnd-content-library/
  SagaSmith-dnd-ui/
  sagasmith-coc-ui/
```

先在 PowerShell 中核对工具：

```powershell
uv --version
uv python find ">=3.11"
node --version
npm --version
```

## 3. 完整安装

```powershell
cd C:\path\to\SagaSmith\SagaSmith-agent
.\install-all.bat
```

默认流程按以下顺序执行：Python 运行时 → 三个 UI → runtime imports → Skills → 公开内容目录 → 已有配置的只读 preflight。任一步软件安装失败都会返回非零退出码；配置缺失或尚未完成只会明确提示下一步，因为安装器不应替用户写入凭据或授权策略。

可用模式：

```powershell
# 不安装、不构建，只检查当前工作区
.\install-all.bat --verify-only

# 只安装/检查 Python、Skills 和内容目录，跳过 Node/UI
.\install-all.bat --skip-ui

# 查看参数和边界
.\install-all.bat --help
```

`--skip-ui` 适合后端排障，不代表完整产品安装。

## 4. 配置与启动

首次安装后创建 repo-local 配置：

```powershell
uv run nanobot onboard --wizard --config config\config.json --workspace workspace
```

然后按 [MCP 配置指南](configure-mcp-tools.md) 加入 Full Skills、`sagasmith_dnd` 与 `sagasmith_coc`。D&D 必须允许服务端通过 `tools/list_changed` 管理 native tool surface；规则书与模组导入根目录应分别 allowlist。完成后运行：

```powershell
.\install-all.bat --verify-only
.\start.bat
```

`start.bat` 会再次执行配置 preflight，然后启动 D&D UI Gateway 与前台 Agent gateway。Agent 退出时，启动脚本会清理它创建的 UI Gateway 子进程。

## 5. “全部内容”的含义

源码安装覆盖当前公开软件、Skills 和可再分发内容目录。公开目录当前只有两个 CC-BY SRD preset Pack；私有完整库包含从用户自有资料构建的 core/addon/module/preset，不能由公共安装器分发。

私有 Pack 应通过最新流程在本地生成：机械 first pass → Agent 逐证据审阅和修复 draft → finalize 为不可变 Pack。之后再由 Lobby 的内容控制流程选择导入、依赖解析和 campaign activation。安装成功不等于 Pack 已进入某个战役。

## 6. 常见故障

- `Missing required sibling repository`：目录缺失或名称与上面的布局不一致。
- `Python 3.11 or newer was not found`：运行 `uv python install 3.12` 后重试。
- `Node.js 22.12 or newer is required`：升级 Node，再打开新终端。
- `Windows executable is locked`：停止正在运行的 `start.bat`、MCP 或全流程回归后再安装；运行期间可使用 `--verify-only`。
- UI build 缺失：不要使用 `--skip-ui`，重新运行完整安装。
- 软件验证成功但 config preflight 失败：安装已完成；按 MCP 配置指南修正 repo-local 配置，再运行 `--verify-only`。
- 公开目录校验失败：不要绕过 checksum/license gate；先修复内容库仓库或切换到一致的提交。
