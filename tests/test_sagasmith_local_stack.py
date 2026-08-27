from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.sagasmith_local.configuration import (
    LOOPBACK_CIDR,
    desired_servers,
    desired_skill_roots,
    reconcile_agent_config,
)
from nanobot.sagasmith_local.model import (
    PROFILE_MODES,
    InstallMode,
    InstallProfile,
    McpTransport,
    ProcessRecord,
    StackLayout,
    StackState,
    load_release_revisions,
    normalize_modes,
    normalize_profile,
    normalize_transport,
    selected_components,
    transport_for_mode,
)
from nanobot.sagasmith_local.runtime import (
    _domain_sync_command,
    backup,
    doctor,
    restore,
    status,
    verify_backup,
)


def layout_for(tmp_path: Path) -> StackLayout:
    agent = tmp_path / "SagaSmith-agent"
    agent.mkdir()
    return StackLayout.discover(
        agent_root=agent,
        workspace_root=tmp_path,
        state_root=tmp_path / "state",
        config_path=tmp_path / "config.json",
    )


def test_modes_are_independently_selectable_and_default_to_all() -> None:
    assert normalize_modes(None) == tuple(InstallMode)
    assert normalize_modes(["coc"]) == (InstallMode.COC,)
    assert normalize_modes(["narrative", "dnd", "narrative"]) == (
        InstallMode.NARRATIVE,
        InstallMode.DND,
    )
    repositories = {item.repository for item in selected_components((InstallMode.COC,))}
    assert repositories == {"SagaSmith-agent", "sagasmith-core", "sagasmith-coc"}


def test_named_profiles_and_transports_are_stable() -> None:
    assert normalize_profile("dnd-only") == (InstallMode.DND,)
    assert normalize_profile("multi-system") == tuple(InstallMode)
    assert PROFILE_MODES[InstallProfile.COC_ONLY] == (InstallMode.COC,)
    assert normalize_transport("stdio") == McpTransport.STDIO
    assert (
        transport_for_mode(McpTransport.MIXED, InstallMode.NARRATIVE)
        == McpTransport.STDIO
    )
    assert (
        transport_for_mode(McpTransport.MIXED, InstallMode.DND)
        == McpTransport.STREAMABLE_HTTP
    )


def test_domain_sync_installs_only_the_selected_mcp_and_required_gateway() -> None:
    assert _domain_sync_command("uv", InstallMode.DND, McpTransport.STDIO) == [
        "uv",
        "sync",
        "--package",
        "sagasmith-dnd-mcp",
        "--frozen",
    ]
    assert _domain_sync_command("uv", InstallMode.COC, McpTransport.MIXED) == [
        "uv",
        "sync",
        "--package",
        "sagasmith-coc-mcp",
        "--extra",
        "gateway",
        "--frozen",
    ]
    assert _domain_sync_command(
        "uv", InstallMode.NARRATIVE, McpTransport.STREAMABLE_HTTP
    ) == [
        "uv",
        "sync",
        "--package",
        "sagasmith-narrative-mcp",
        "--frozen",
    ]


def test_release_lock_selects_exact_revisions_for_each_mode(tmp_path: Path) -> None:
    lock = tmp_path / "stack-lock.json"
    components = {"sagasmith-core", "sagasmith-coc"}
    lock.write_text(
        json.dumps(
            {
                "schema": "sagasmith.release-lock/v2",
                "shared": {"sagasmith-core": "a" * 40},
                "profiles": {"coc": {"sagasmith-coc": "a" * 40}},
            }
        ),
        encoding="utf-8",
    )

    revisions = load_release_revisions(lock, (InstallMode.COC,))

    assert set(revisions) == components
    assert set(revisions.values()) == {"a" * 40}


def test_release_lock_rejects_missing_or_moving_component_refs(tmp_path: Path) -> None:
    lock = tmp_path / "stack-lock.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "sagasmith.release-lock/v2",
                "shared": {"sagasmith-core": "main"},
                "profiles": {"dnd": {}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid revision|missing component"):
        load_release_revisions(lock, (InstallMode.DND,))


def test_config_reconciler_owns_only_sagasmith_entries(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    unrelated_skill = tmp_path / "my-skills"
    original = {
        "providers": {"openai": {"apiKey": "keep-secret"}},
        "channels": {"websocket": {"port": 19001, "tokenIssueSecret": "keep-me"}},
        "tools": {
            "mcpServers": {
                "other": {"command": "other"},
                "sagasmith_dnd": {"command": "obsolete"},
            },
            "ssrfWhitelist": ["10.0.0.0/8"],
        },
        "agents": {
            "defaults": {
                "externalSkillsDirs": [
                    str(unrelated_skill),
                    str(layout.repo("sagasmith-dnd") / "skills" / "old"),
                ]
            }
        },
    }
    updated = reconcile_agent_config(original, layout, (InstallMode.COC, InstallMode.NARRATIVE))
    assert updated["providers"] == original["providers"]
    assert updated["channels"] == original["channels"]
    assert updated["tools"]["mcpServers"]["other"] == {"command": "other"}
    assert "sagasmith_dnd" not in updated["tools"]["mcpServers"]
    assert set(updated["tools"]["mcpServers"]) == {
        "other",
        "sagasmith_coc",
        "sagasmith_narrative",
    }
    assert LOOPBACK_CIDR in updated["tools"]["ssrfWhitelist"]
    skills = updated["agents"]["defaults"]["externalSkillsDirs"]
    assert str(unrelated_skill) in skills
    assert skills[-3:] == desired_skill_roots(
        layout, (InstallMode.COC, InstallMode.NARRATIVE)
    )


@pytest.mark.parametrize(
    ("modes", "names"),
    [
        ((InstallMode.DND,), {"sagasmith_dnd"}),
        ((InstallMode.COC,), {"sagasmith_coc"}),
        ((InstallMode.NARRATIVE,), {"sagasmith_narrative"}),
        (tuple(InstallMode), {"sagasmith_dnd", "sagasmith_coc", "sagasmith_narrative"}),
    ],
)
def test_each_mode_has_an_explicit_transport(
    tmp_path: Path,
    modes: tuple[InstallMode, ...],
    names: set[str],
) -> None:
    servers = desired_servers(layout_for(tmp_path), modes)
    assert set(servers) == names
    if "sagasmith_dnd" in servers:
        assert servers["sagasmith_dnd"]["url"].endswith(":8767/mcp")
    if "sagasmith_coc" in servers:
        assert servers["sagasmith_coc"]["url"].endswith(":8769/mcp")
    if "sagasmith_narrative" in servers:
        assert servers["sagasmith_narrative"]["type"] == "stdio"


@pytest.mark.parametrize(
    ("transport", "expected_type"),
    [
        (McpTransport.STDIO, "stdio"),
        (McpTransport.STREAMABLE_HTTP, "streamableHttp"),
    ],
)
def test_every_domain_supports_each_local_transport(
    tmp_path: Path,
    transport: McpTransport,
    expected_type: str,
) -> None:
    servers = desired_servers(layout_for(tmp_path), tuple(InstallMode), transport=transport)
    assert {item["type"] for item in servers.values()} == {expected_type}
    if transport == McpTransport.STREAMABLE_HTTP:
        assert servers["sagasmith_narrative"]["url"].endswith(":8770/mcp")
    else:
        assert servers["sagasmith_dnd"]["args"] == ["-m", "sagasmith_dnd_mcp.server"]
        assert servers["sagasmith_coc"]["args"] == ["-m", "sagasmith_coc_mcp.server"]


def test_distribution_manifest_declares_all_profiles_and_templates() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "sagasmith-local-kit.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "sagasmith.local-agent-kit/v1"
    assert manifest["authoritative_contract"] == "sagasmith.authoritative-mcp/v1"
    assert set(manifest["profiles"]) == {
        "dnd-only",
        "coc-only",
        "narrative-only",
        "multi-system",
    }
    assert set(manifest["transports"]) == {"mixed", "stdio", "streamable-http"}
    for relative in manifest["templates"].values():
        assert (root / relative).is_file()


def test_wheel_build_maps_distribution_assets_into_the_python_package() -> None:
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"sagasmith-local-kit.json" = "nanobot/sagasmith_local/assets/' in pyproject
    assert '"examples/local-agent-kit" = "nanobot/sagasmith_local/assets/' in pyproject


def test_doctor_checks_provider_database_skills_and_transport(
    tmp_path: Path, monkeypatch
) -> None:
    layout = layout_for(tmp_path)
    (layout.agent_root / "pyproject.toml").write_text("", encoding="utf-8")
    narrative = layout.repo("sagasmith-narrative")
    (narrative / "skills" / "narrative-project-generator").mkdir(parents=True)
    (narrative / "skills" / "narrative-project-generator" / "SKILL.md").write_text(
        "# Skill\n", encoding="utf-8"
    )
    (narrative / "pyproject.toml").write_text("", encoding="utf-8")
    (narrative / "packages" / "domain").mkdir(parents=True)
    (narrative / "packages" / "domain" / "pyproject.toml").write_text("", encoding="utf-8")
    (narrative / "packages" / "mcp").mkdir(parents=True)
    (narrative / "packages" / "mcp" / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "nanobot.sagasmith_local.runtime._venv_python", lambda repo: Path("python")
    )
    monkeypatch.setattr(
        "nanobot.sagasmith_local.runtime._run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-provider-key")
    config = reconcile_agent_config(
        {"providers": {"openai": {"apiKey": "${OPENAI_API_KEY}"}}},
        layout,
        (InstallMode.NARRATIVE,),
        transport=McpTransport.STREAMABLE_HTTP,
    )
    layout.config_path.write_text(json.dumps(config), encoding="utf-8")
    report = doctor(
        layout,
        modes=(InstallMode.NARRATIVE,),
        transport=McpTransport.STREAMABLE_HTTP,
        include_runtime=False,
    )
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["provider"]["ok"] is True
    assert checks["provider"]["required"] is False
    assert checks["database-narrative"]["ok"] is True
    skill_checks = [item for name, item in checks.items() if name.startswith("skills-")]
    assert len(skill_checks) == 1
    assert skill_checks[0]["ok"] is True
    assert report["mcp_transport"] == "streamable-http"


def test_state_round_trip_and_status_never_invents_processes(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    state = StackState(
        modes=["dnd"],
        workspace_root=str(tmp_path),
        config_path=str(layout.config_path),
        processes=[ProcessRecord("dnd_mcp", 99999999, ["python"], str(tmp_path), "x", "now")],
    )
    layout.save_state(state)
    loaded = layout.load_state()
    assert loaded.modes == ["dnd"]
    payload = status(layout)
    assert payload["running"] is False
    assert payload["processes"][0]["running"] is False

    rediscovered = StackLayout.discover(
        agent_root=layout.agent_root,
        state_root=layout.state_root,
    )
    assert rediscovered.workspace_root == tmp_path
    assert rediscovered.config_path == layout.config_path


def test_backup_restore_keeps_domain_stores_separate(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    layout.ensure()
    (layout.data_dir / "dnd").mkdir()
    (layout.data_dir / "coc").mkdir()
    (layout.data_dir / "dnd" / "runtime.db").write_bytes(b"dnd")
    (layout.data_dir / "coc" / "runtime.db").write_bytes(b"coc")
    layout.save_state(
        StackState(
            modes=["dnd", "coc"],
            workspace_root=str(tmp_path),
            config_path=str(layout.config_path),
        )
    )
    archive = backup(layout, tmp_path / "backup.zip")
    report = verify_backup(archive)
    assert report["manifest"]["modes"] == ["dnd", "coc"]
    (layout.data_dir / "dnd" / "runtime.db").write_bytes(b"changed")
    restored = restore(layout, archive)
    assert restored.revision == 1
    assert (layout.data_dir / "dnd" / "runtime.db").read_bytes() == b"dnd"
    assert (layout.data_dir / "coc" / "runtime.db").read_bytes() == b"coc"
    assert json.loads(layout.state_file.read_text(encoding="utf-8"))["processes"] == []
