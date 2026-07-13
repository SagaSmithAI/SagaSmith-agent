"""Status checks for SagaSmith's bundled Git submodules."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DependencyStatus:
    """The local and remote state of one bundled dependency."""

    name: str
    path: Path
    current: str | None
    remote: str | None
    ahead: int | None
    behind: int | None
    dirty: bool
    error: str | None = None


DEPENDENCIES = {
    "sagasmith-core": "sagasmith/Sagasmith-core",
    "sagasmith-dnd": "sagasmith/Sagasmith-dnd",
}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def check_dependency_updates(*, fetch: bool = True, root: Path | None = None) -> list[DependencyStatus]:
    """Report whether pinned submodules differ from their ``origin/main`` heads."""
    root = root or repository_root()
    statuses: list[DependencyStatus] = []
    for name, relative_path in DEPENDENCIES.items():
        path = root / relative_path
        if not (path / ".git").exists():
            statuses.append(
                DependencyStatus(name, path, None, None, None, None, False, "submodule is not initialized")
            )
            continue

        if fetch:
            fetched = _git(path, "fetch", "--quiet", "origin", "main")
            if fetched.returncode:
                statuses.append(
                    DependencyStatus(
                        name,
                        path,
                        None,
                        None,
                        None,
                        None,
                        False,
                        fetched.stderr.strip() or "could not fetch origin/main",
                    )
                )
                continue

        current = _git(path, "rev-parse", "HEAD")
        remote = _git(path, "rev-parse", "--verify", "origin/main")
        if current.returncode or remote.returncode:
            statuses.append(
                DependencyStatus(
                    name,
                    path,
                    None,
                    None,
                    None,
                    None,
                    False,
                    "could not resolve the local or remote revision",
                )
            )
            continue

        counts = _git(path, "rev-list", "--left-right", "--count", "HEAD...origin/main")
        try:
            ahead, behind = (int(value) for value in counts.stdout.split())
        except ValueError:
            statuses.append(
                DependencyStatus(
                    name,
                    path,
                    current.stdout.strip(),
                    remote.stdout.strip(),
                    None,
                    None,
                    False,
                    "could not compare revisions",
                )
            )
            continue

        dirty = bool(_git(path, "status", "--porcelain").stdout.strip())
        statuses.append(
            DependencyStatus(
                name,
                path,
                current.stdout.strip(),
                remote.stdout.strip(),
                ahead,
                behind,
                dirty,
            )
        )
    return statuses
