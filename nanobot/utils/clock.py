"""Wall-clock primitives shared by Agent infrastructure."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def unix_time_ms() -> int:
    """Return the current Unix wall-clock position in whole milliseconds."""

    return int(time.time() * 1000)


def utc_now_iso(now: datetime | None = None) -> str:
    """Return one canonical UTC wall-clock timestamp with a ``Z`` suffix."""

    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("UTC timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
