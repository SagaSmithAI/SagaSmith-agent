import zipfile
from pathlib import Path

import pytest

from scripts.local_dnd_data import create_backup, restore_backup, verify_backup


def test_backup_verify_and_recover_workspace(tmp_path: Path) -> None:
    agent_root = tmp_path / "SagaSmith-agent"
    workspace = agent_root / "workspace"
    database = workspace / ".sagasmith-dnd-mcp" / "data" / "ttrpgbase.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"authoritative-state")
    session = workspace / "sessions" / "table.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("session\n", encoding="utf-8")
    archive = tmp_path / "backups" / "table.zip"

    manifest = create_backup(agent_root, archive)
    assert len(manifest["files"]) == 2
    assert len(verify_backup(archive)["files"]) == 2

    database.write_bytes(b"changed")
    previous = restore_backup(agent_root, archive)

    assert database.read_bytes() == b"authoritative-state"
    assert (previous / ".sagasmith-dnd-mcp" / "data" / "ttrpgbase.db").read_bytes() == b"changed"


def test_backup_refuses_active_runtime_and_workspace_destination(tmp_path: Path) -> None:
    agent_root = tmp_path / "SagaSmith-agent"
    workspace = agent_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".sagasmith-runtime.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="stop start.bat"):
        create_backup(agent_root, tmp_path / "backup.zip")

    (workspace / ".sagasmith-runtime.json").unlink()
    with pytest.raises(ValueError, match="outside"):
        create_backup(agent_root, workspace / "backup.zip")


def test_verify_rejects_unmanifested_archive_entry(tmp_path: Path) -> None:
    agent_root = tmp_path / "SagaSmith-agent"
    workspace = agent_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "state.txt").write_text("ok", encoding="utf-8")
    archive = tmp_path / "backup.zip"
    create_backup(agent_root, archive)

    with zipfile.ZipFile(archive, "a") as value:
        value.writestr("unexpected.txt", "no")

    with pytest.raises(ValueError, match="unmanifested"):
        verify_backup(archive)
