"""Low-cardinality MCP Host counters safe for metrics export."""

from __future__ import annotations

from collections import Counter
from threading import Lock

_ALLOWED_PHASES = frozenset({"connect", "discover", "catalog", "tool", "projection"})
_ALLOWED_OUTCOMES = frozenset({"ok", "error", "timeout", "cancelled", "retry"})
_ALLOWED_TRANSPORTS = frozenset({"stdio", "sse", "http", "inproc", "unknown"})
_ALLOWED_PROTOCOLS = frozenset({"legacy", "2026-07-28", "unknown"})
_COUNTERS: Counter[tuple[str, str, str, str]] = Counter()
_CATALOG_SELECTIONS: Counter[tuple[str, str]] = Counter()
_LOCK = Lock()


def _size_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value <= 7:
        return "1-7"
    if value <= 16:
        return "8-16"
    if value <= 32:
        return "17-32"
    if value <= 64:
        return "33-64"
    if value <= 100:
        return "65-100"
    return "over-100"


def record_mcp_event(
    phase: str,
    outcome: str,
    *,
    transport: str = "unknown",
    protocol: str = "unknown",
) -> None:
    """Record one event without accepting user/campaign/tool/argument labels."""

    key = (
        phase if phase in _ALLOWED_PHASES else "tool",
        outcome if outcome in _ALLOWED_OUTCOMES else "error",
        transport if transport in _ALLOWED_TRANSPORTS else "unknown",
        protocol if protocol in _ALLOWED_PROTOCOLS else "unknown",
    )
    with _LOCK:
        _COUNTERS[key] += 1


def mcp_metrics_snapshot() -> list[dict[str, int | str]]:
    with _LOCK:
        items = sorted(_COUNTERS.items())
    return [
        {
            "phase": phase,
            "outcome": outcome,
            "transport": transport,
            "protocol": protocol,
            "count": count,
        }
        for (phase, outcome, transport, protocol), count in items
    ]


def record_mcp_catalog_selection(candidate_count: int, selected_count: int) -> None:
    """Record bounded catalog-size buckets without domain or request identifiers."""

    with _LOCK:
        _CATALOG_SELECTIONS[(_size_bucket(candidate_count), _size_bucket(selected_count))] += 1


def mcp_catalog_selection_snapshot() -> list[dict[str, int | str]]:
    with _LOCK:
        items = sorted(_CATALOG_SELECTIONS.items())
    return [
        {
            "candidate_bucket": candidate_bucket,
            "selected_bucket": selected_bucket,
            "count": count,
        }
        for (candidate_bucket, selected_bucket), count in items
    ]
