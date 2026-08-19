from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.apps import hosted_audit


def _hosted_root(root: Path) -> Path:
    for relative in (
        "agent/loop.py",
        "agent/tools/mcp.py",
        "agent/tools/structured_output.py",
        "apps/hosted_worker.py",
        "config/schema.py",
        "session/manager.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return root


def test_prune_removes_every_local_only_surface(tmp_path: Path) -> None:
    root = _hosted_root(tmp_path / "nanobot")
    for name in hosted_audit.LOCAL_ONLY_PACKAGES:
        (root / name).mkdir(parents=True)
        (root / name / "payload.py").touch()

    hosted_audit.prune_local_surfaces(root)

    assert all(not (root / name).exists() for name in hosted_audit.LOCAL_ONLY_PACKAGES)


def test_verify_rejects_channel_sdk_and_accepts_minimal_hosted_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _hosted_root(tmp_path / "nanobot")
    monkeypatch.setattr(hosted_audit.importlib.metadata, "distributions", lambda: [])
    hosted_audit.verify(root)

    forbidden = SimpleNamespace(metadata={"Name": "discord.py"})
    monkeypatch.setattr(
        hosted_audit.importlib.metadata,
        "distributions",
        lambda: [forbidden],
    )
    with pytest.raises(RuntimeError, match="Channel SDKs"):
        hosted_audit.verify(root)


def test_hosted_runtime_import_graph_is_self_contained() -> None:
    hosted_audit.verify_runtime_imports()
