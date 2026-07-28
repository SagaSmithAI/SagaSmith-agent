"""Durable JSON run records for automation executions."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from nanobot.utils.clock import unix_time_ms as _now_ms
from nanobot.utils.helpers import write_text_atomic as _atomic_write


def safe_run_record_name(run_id: str) -> str:
    """Return a filesystem-safe filename stem for a run ID."""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in run_id)


def write_run_record(runs_dir: Path, run_id: str, record: dict[str, Any]) -> Path:
    """Write or replace one durable automation run audit record."""
    name = safe_run_record_name(run_id) or str(uuid.uuid4())
    path = runs_dir / f"{name}.json"
    payload = {
        **record,
        "run_id": run_id,
        "updated_at_ms": _now_ms(),
    }
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return path
