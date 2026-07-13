from __future__ import annotations

import os
import sys
from pathlib import Path

from nanobot.sagasmith_runtime import configure_sagasmith_runtime


def test_configure_sagasmith_runtime_uses_workspace_database(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DND_DATABASE_URL", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("nanobot.sagasmith_runtime.shutil.which", lambda name: "/bin/sagasmith-dnd")

    configure_sagasmith_runtime(tmp_path)

    assert Path(os.environ["PATH"].split(os.pathsep)[0]).resolve() == Path(sys.executable).resolve().parent
    assert os.environ["DND_DATABASE_URL"] == (
        f"sqlite+pysqlite:///{(tmp_path.resolve() / 'ttrpgbase.db').as_posix()}"
    )


def test_configure_sagasmith_runtime_preserves_configured_database(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", "postgresql://configured")
    monkeypatch.setattr("nanobot.sagasmith_runtime.shutil.which", lambda name: "/bin/sagasmith-dnd")

    configure_sagasmith_runtime(tmp_path)

    assert os.environ["DND_DATABASE_URL"] == "postgresql://configured"
