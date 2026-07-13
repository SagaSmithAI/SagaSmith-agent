from __future__ import annotations

from pathlib import Path

from nanobot import dependencies


def test_check_dependency_updates_reports_uninitialized_submodules(tmp_path: Path) -> None:
    statuses = dependencies.check_dependency_updates(fetch=False, root=tmp_path)

    assert [status.name for status in statuses] == ["sagasmith-core", "sagasmith-dnd"]
    assert all(status.error == "submodule is not initialized" for status in statuses)
