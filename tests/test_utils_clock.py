from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nanobot.utils import clock


def test_unix_time_ms_uses_wall_clock_seconds(monkeypatch) -> None:
    monkeypatch.setattr(clock.time, "time", lambda: 123.4567)

    assert clock.unix_time_ms() == 123456


def test_utc_now_iso_normalizes_aware_timestamps() -> None:
    assert (
        clock.utc_now_iso(datetime(2026, 7, 28, 1, 2, 3, tzinfo=UTC))
        == "2026-07-28T01:02:03Z"
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        clock.utc_now_iso(datetime(2026, 7, 28, 1, 2, 3))
