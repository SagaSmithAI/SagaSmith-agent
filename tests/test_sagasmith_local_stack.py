from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.sagasmith_local.configuration import (
    LOOPBACK_CIDR,
    desired_servers,
    desired_skill_roots,
    reconcile_agent_config,
)
from nanobot.sagasmith_local.model import (
    InstallMode,
    ProcessRecord,
    StackLayout,
    StackState,
    normalize_modes,
    selected_components,
)
from nanobot.sagasmith_local.runtime import backup, restore, status, verify_backup


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
    assert "SagaSmith-coc-mcp" in repositories
    assert "sagasmith-coc-ui" in repositories
    assert "SagaSmith-dnd-mcp" not in repositories
    assert "SagaSmith-narrative-mcp" not in repositories


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
                    str(layout.repo("SagaSmith-dnd-skills") / "old"),
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
