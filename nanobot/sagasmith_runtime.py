"""Runtime setup for the bundled SagaSmith D&D command-line tools."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def configure_sagasmith_runtime(workspace: Path) -> None:
    """Make the active Python environment's D&D CLI and database deterministic.

    An explicitly configured ``DND_DATABASE_URL`` always wins. This keeps an
    installed deployment configurable while giving the bundled local runtime a
    single workspace-scoped SQLite database by default.
    """
    scripts_dir = str(Path(sys.executable).resolve().parent)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if not any(Path(entry).resolve(strict=False) == Path(scripts_dir) for entry in path_entries if entry):
        os.environ["PATH"] = scripts_dir + (os.pathsep + os.environ["PATH"] if os.environ.get("PATH") else "")

    if "DND_DATABASE_URL" not in os.environ and shutil.which("sagasmith-dnd"):
        database_path = (workspace.resolve() / "ttrpgbase.db").as_posix()
        os.environ["DND_DATABASE_URL"] = f"sqlite+pysqlite:///{database_path}"
