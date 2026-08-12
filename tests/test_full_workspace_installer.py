from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_installer_delegates_to_versioned_script() -> None:
    launcher = (ROOT / "install-all.bat").read_text(encoding="utf-8")

    assert "scripts\\install-all.bat" in launcher
    assert "SAGASMITH_INSTALL_AGENT_ROOT" in launcher


def test_dnd_installer_covers_local_runtime_without_content_mutation() -> None:
    installer = (ROOT / "scripts" / "install-all.bat").read_text(encoding="utf-8")

    for repository in (
        "sagasmith-core",
        "sagasmith-dnd",
        "SagaSmith-dnd-mcp",
        "SagaSmith-dnd-skills",
        "SagaSmith-module-gen-skills",
        "SagaSmith-dnd-content-library",
        "SagaSmith-dnd-ui",
    ):
        assert repository in installer

    assert installer.count("uv sync --all-extras --frozen") == 2
    assert installer.count("call npm ci") == 2
    assert installer.count("call npm run build") == 2
    assert "SagaSmith-coc-mcp" not in installer
    assert "sagasmith-coc-ui" not in installer
    assert "tasklist /FI" in installer
    assert "scripts\\validate_catalog.py" in installer
    assert "scripts\\validate_agent_runtime.py" in installer
    assert "--verify-only" in installer
    assert "never overwrites config\\config.json" in installer
    assert "never imports or" in installer
    assert "activates private/commercial content Packs" in installer


def test_full_installer_docs_keep_private_pack_boundary_explicit() -> None:
    readme = (ROOT / "README-en.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "guides" / "install-full-workspace-windows.md").read_text(
        encoding="utf-8"
    )

    assert ".\\install-all.bat" in readme
    assert "--verify-only" in readme
    assert "does not copy commercial sources" in readme
    assert "does not silently place content in a campaign" in guide


def test_start_script_owns_one_remote_dnd_mcp_and_workbench() -> None:
    start = (ROOT / "scripts" / "start-all.bat").read_text(encoding="utf-8")

    assert "SAGASMITH_DND_MCP_TRANSPORT=streamable-http" in start
    assert "SAGASMITH_DND_MCP_HTTP_PORT=8767" in start
    assert "SAGASMITH_DND_MCP_URL=http://127.0.0.1:" in start
    assert "SAGASMITH_DND_UI_DIST" in start
    assert "SAGASMITH_AGENT_WEBUI_URL" in start
    assert "tools/list_changed" in start
    assert "SagaSmith-coc-mcp" not in start

    stop = (ROOT / "scripts" / "stop-all.bat").read_text(encoding="utf-8")
    assert ".sagasmith-runtime.json" in stop
    assert "agent_pid" in stop
    assert "dnd_gateway_pid" in stop
    assert "dnd_mcp_pid" in stop
    assert "Stop-Process -Id" in stop
