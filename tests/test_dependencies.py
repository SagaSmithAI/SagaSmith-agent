from __future__ import annotations

from pathlib import Path

from nanobot import dependencies


def test_check_dependency_updates_has_no_embedded_domain_submodules(tmp_path: Path) -> None:
    statuses = dependencies.check_dependency_updates(fetch=False, root=tmp_path)

    assert statuses == []
