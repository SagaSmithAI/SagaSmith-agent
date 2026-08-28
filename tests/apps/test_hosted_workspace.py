from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.apps.hosted_workspace import HostedWorkspaceLease, HostedWorkspacePolicy


def lease(root: Path, name: str, *, max_workspaces: int = 2) -> HostedWorkspaceLease:
    return HostedWorkspaceLease(
        HostedWorkspacePolicy(
            root=root,
            ttl_seconds=60,
            max_bytes=1_048_576,
            max_workspaces=max_workspaces,
        ),
        root / name,
        owner=f"worker:{name}",
    )


def test_cleanup_removes_only_expired_registered_terminated_workspace(tmp_path: Path) -> None:
    expired = lease(tmp_path, "expired")
    expired.register(now=1)
    expired.terminate(now=2)
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("keep", encoding="utf-8")

    removed = HostedWorkspaceLease.cleanup_registered(expired.policy, now=100)

    assert removed == [expired.workspace]
    assert not expired.workspace.exists()
    assert (unknown / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_lru_cleanup_never_evicts_active_workspace(tmp_path: Path) -> None:
    older = lease(tmp_path, "older", max_workspaces=2)
    newer = lease(tmp_path, "newer", max_workspaces=2)
    active = lease(tmp_path, "active", max_workspaces=2)
    older.register(now=1)
    older.terminate(now=2)
    newer.register(now=3)
    newer.terminate(now=4)
    active.register(now=5)

    removed = HostedWorkspaceLease.cleanup_registered(active.policy, now=10)

    assert removed == [older.workspace]
    assert active.workspace.exists()
    assert newer.workspace.exists()


def test_capacity_rejects_oversized_registered_workspace(tmp_path: Path) -> None:
    current = lease(tmp_path, "large")
    current.register()
    (current.workspace / "large.bin").write_bytes(b"x" * 1_048_577)

    with pytest.raises(RuntimeError, match="exceeds"):
        current.enforce_capacity()
