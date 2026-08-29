"""Bounded lifecycle for explicitly registered Hosted Worker workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

MARKER_NAME = ".sagasmith-hosted-workspace.json"
MARKER_SCHEMA = "sagasmith.hosted-workspace/v1"
OWNER_NAMESPACE = "sagasmith.hosted-workspace-owner/v1"
ROOT_LOCK_NAME = ".sagasmith-hosted-workspaces.lock"
ROOT_LOCK_TIMEOUT_SECONDS = 30


class HostedWorkspaceError(RuntimeError):
    """Raised when a Hosted workspace violates its lifecycle policy."""


def derive_workspace_owner(workspace: Path, workspace_id: str) -> str:
    """Derive a stable opaque owner from Host-managed workspace identity and path."""

    if workspace_id != workspace_id.strip() or not workspace_id:
        raise ValueError("workspace ID must be non-empty without surrounding whitespace")
    if len(workspace_id.encode("utf-8")) > 512:
        raise ValueError("workspace ID must not exceed 512 UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in workspace_id):
        raise ValueError("workspace ID must not contain control characters")
    namespace_path = os.path.normcase(str(workspace.expanduser().resolve(strict=False)))
    material = f"{OWNER_NAMESPACE}\0{namespace_path}\0{workspace_id}".encode("utf-8")
    return f"workspace:{hashlib.sha256(material).hexdigest()}"


@dataclass(frozen=True, slots=True)
class HostedWorkspacePolicy:
    root: Path
    ttl_seconds: int = 86_400
    max_bytes: int = 1_073_741_824
    max_workspaces: int = 128

    def __post_init__(self) -> None:
        root = self.root.expanduser().resolve(strict=False)
        object.__setattr__(self, "root", root)
        if self.ttl_seconds < 60:
            raise ValueError("workspace TTL must be at least 60 seconds")
        if self.max_bytes < 1_048_576:
            raise ValueError("workspace capacity must be at least 1 MiB")
        if self.max_workspaces < 1:
            raise ValueError("workspace count limit must be positive")


class HostedWorkspaceLease:
    """Own one explicitly selected workspace without inferring other directories."""

    def __init__(self, policy: HostedWorkspacePolicy, workspace: Path, owner: str) -> None:
        self.policy = policy
        self.workspace = workspace.expanduser().resolve(strict=False)
        self.owner = owner.strip()
        if not self.owner:
            raise ValueError("workspace owner is required")
        self._assert_below_root(self.workspace)
        self.marker = self.workspace / MARKER_NAME

    def _root_lock(self) -> FileLock:
        self.policy.root.mkdir(parents=True, exist_ok=True)
        return FileLock(
            str(self.policy.root / ROOT_LOCK_NAME),
            timeout=ROOT_LOCK_TIMEOUT_SECONDS,
        )

    def _assert_below_root(self, path: Path) -> None:
        if path == self.policy.root or self.policy.root not in path.parents:
            raise HostedWorkspaceError("Hosted workspace must be a child of the configured root")

    def _read_marker(self) -> dict[str, Any] | None:
        if not self.marker.is_file():
            return None
        try:
            value = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HostedWorkspaceError("Hosted workspace marker is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema") != MARKER_SCHEMA:
            raise HostedWorkspaceError("Hosted workspace marker has an unsupported schema")
        if Path(str(value.get("workspace") or "")).resolve(strict=False) != self.workspace:
            raise HostedWorkspaceError("Hosted workspace marker names another path")
        return value

    def _write_marker(self, value: dict[str, Any]) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        temporary = self.marker.with_name(f"{MARKER_NAME}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.marker)

    def register(self, *, now: float | None = None) -> dict[str, Any]:
        instant = float(now if now is not None else time.time())
        with self._root_lock():
            existing = self._read_marker()
            if existing is not None and existing.get("owner") != self.owner:
                raise HostedWorkspaceError("Hosted workspace is registered to another owner")
            restarting_active = existing is not None and existing.get("status") == "active"
            if not restarting_active:
                active_count = sum(
                    1
                    for path, marker in self._registered_markers(self.policy)
                    if path != self.workspace and marker.get("status") == "active"
                )
                if active_count >= self.policy.max_workspaces:
                    raise HostedWorkspaceError(
                        "Hosted workspace root has reached its active workspace limit"
                    )
            value = {
                "schema": MARKER_SCHEMA,
                "workspace_id": str((existing or {}).get("workspace_id") or uuid.uuid4()),
                "workspace": str(self.workspace),
                "owner": self.owner,
                "status": "active",
                "created_at": float((existing or {}).get("created_at") or instant),
                "last_access_at": instant,
                "terminated_at": None,
            }
            self._write_marker(value)
            self.enforce_capacity()
            return value

    def touch(self, *, now: float | None = None) -> None:
        with self._root_lock():
            value = self._read_marker()
            if value is None or value.get("status") != "active":
                raise HostedWorkspaceError("Hosted workspace is not active")
            value["last_access_at"] = float(now if now is not None else time.time())
            self._write_marker(value)

    def terminate(self, *, now: float | None = None) -> None:
        with self._root_lock():
            value = self._read_marker()
            if value is None:
                return
            instant = float(now if now is not None else time.time())
            value.update(status="terminated", last_access_at=instant, terminated_at=instant)
            self._write_marker(value)

    def logical_size(self) -> int:
        total = 0
        if not self.workspace.exists():
            return 0
        for root, directories, files in os.walk(self.workspace, followlinks=False):
            directories[:] = [
                name for name in directories if not (Path(root) / name).is_symlink()
            ]
            for name in files:
                path = Path(root) / name
                if path.is_symlink():
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def enforce_capacity(self) -> int:
        size = self.logical_size()
        if size > self.policy.max_bytes:
            raise HostedWorkspaceError(
                f"Hosted workspace exceeds its {self.policy.max_bytes}-byte capacity"
            )
        return size

    @classmethod
    def _registered_markers(
        cls, policy: HostedWorkspacePolicy
    ) -> list[tuple[Path, dict[str, Any]]]:
        """Return only marker-owned children with a self-consistent canonical path."""

        registered: list[tuple[Path, dict[str, Any]]] = []
        for child in policy.root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            marker = child / MARKER_NAME
            if not marker.is_file():
                continue
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
                resolved = child.resolve(strict=False)
                recorded = Path(str(value.get("workspace") or "")).resolve(strict=False)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if (
                not isinstance(value, dict)
                or value.get("schema") != MARKER_SCHEMA
                or recorded != resolved
                or resolved == policy.root
                or policy.root not in resolved.parents
            ):
                continue
            registered.append((resolved, value))
        return registered

    @classmethod
    def cleanup_registered(
        cls,
        policy: HostedWorkspacePolicy,
        *,
        now: float | None = None,
    ) -> list[Path]:
        """Remove only expired or LRU-evicted, terminated, marker-owned workspaces."""

        instant = float(now if now is not None else time.time())
        policy.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(
            str(policy.root / ROOT_LOCK_NAME),
            timeout=ROOT_LOCK_TIMEOUT_SECONDS,
        )
        with lock:
            registered = cls._registered_markers(policy)
            active_count = sum(
                1 for _, value in registered if value.get("status") == "active"
            )
            known = [
                (
                    float(value.get("last_access_at") or value.get("created_at") or 0),
                    path,
                    value,
                )
                for path, value in registered
                if value.get("status") != "active"
            ]

            remove: list[Path] = [
                path for accessed, path, _ in known if instant - accessed >= policy.ttl_seconds
            ]
            remaining_slots = max(0, policy.max_workspaces - active_count)
            survivors = sorted(
                ((accessed, path) for accessed, path, _ in known if path not in remove),
                key=lambda item: item[0],
            )
            if len(survivors) > remaining_slots:
                remove.extend(path for _, path in survivors[: len(survivors) - remaining_slots])

            removed: list[Path] = []
            for path in sorted(set(remove), key=str):
                marker = path / MARKER_NAME
                if not marker.is_file():
                    continue
                try:
                    current = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if (
                    current.get("schema") != MARKER_SCHEMA
                    or current.get("status") != "terminated"
                ):
                    continue
                shutil.rmtree(path)
                removed.append(path)
            return removed
