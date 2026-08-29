from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.apps.hosted_workspace import (
    HostedWorkspaceError,
    HostedWorkspaceLease,
    HostedWorkspacePolicy,
    derive_workspace_owner,
)


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


def test_host_managed_owner_is_stable_across_worker_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "conversation-a"
    policy = HostedWorkspacePolicy(root=tmp_path, ttl_seconds=60)
    owner = derive_workspace_owner(workspace, "host-workspace-019")
    first_worker = HostedWorkspaceLease(policy, workspace, owner=owner)
    first_marker = first_worker.register(now=1)

    restarted_worker = HostedWorkspaceLease(
        policy,
        workspace,
        owner=derive_workspace_owner(workspace, "host-workspace-019"),
    )
    restarted_marker = restarted_worker.register(now=2)

    assert restarted_marker["owner"] == owner
    assert restarted_marker["workspace_id"] == first_marker["workspace_id"]
    assert restarted_marker["created_at"] == 1
    assert restarted_marker["last_access_at"] == 2


def test_workspace_owner_is_path_bound_and_rejects_another_host_owner(tmp_path: Path) -> None:
    workspace = tmp_path / "conversation-a"
    policy = HostedWorkspacePolicy(root=tmp_path, ttl_seconds=60)
    first_owner = derive_workspace_owner(workspace, "host-workspace-019")
    other_owner = derive_workspace_owner(workspace, "host-workspace-020")
    HostedWorkspaceLease(policy, workspace, owner=first_owner).register(now=1)

    assert first_owner != other_owner
    assert first_owner != derive_workspace_owner(
        tmp_path / "conversation-b", "host-workspace-019"
    )
    with pytest.raises(HostedWorkspaceError, match="another owner"):
        HostedWorkspaceLease(policy, workspace, owner=other_owner).register(now=2)


@pytest.mark.parametrize("workspace_id", ["", " leading", "trailing ", "line\nbreak"])
def test_workspace_owner_rejects_ambiguous_host_identity(
    tmp_path: Path, workspace_id: str
) -> None:
    with pytest.raises(ValueError, match="workspace ID"):
        derive_workspace_owner(tmp_path / "conversation", workspace_id)
