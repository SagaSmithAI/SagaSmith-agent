from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_installer_delegates_to_versioned_script() -> None:
    launcher = (ROOT / "install-all.bat").read_text(encoding="utf-8")

    assert "scripts\\install-all.bat" in launcher
    assert "SAGASMITH_INSTALL_AGENT_ROOT" in launcher


def test_full_installer_covers_current_workspace_without_content_mutation() -> None:
    installer = (ROOT / "scripts" / "install-all.bat").read_text(encoding="utf-8")

    for repository in (
        "sagasmith-core",
        "sagasmith-dnd",
        "sagasmith-coc",
        "SagaSmith-dnd-mcp",
        "SagaSmith-coc-mcp",
        "SagaSmith-dnd-skills",
        "SagaSmith-coc-skills",
        "SagaSmith-module-gen-skills",
        "SagaSmith-dnd-content-library",
        "SagaSmith-dnd-ui",
        "sagasmith-coc-ui",
    ):
        assert repository in installer

    assert installer.count("uv sync --all-extras --frozen") == 3
    assert installer.count("call npm ci") == 3
    assert installer.count("call npm run build") == 3
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
