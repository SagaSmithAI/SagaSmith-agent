"""Audience-safe resolution presentation handling for local Agent clients."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

RESOLUTION_PRESENTATION_SCHEMA = "sagasmith.resolution-presentation/v1"

_ALLOWED_FIELDS = frozenset(
    {
        "schema",
        "resolution_id",
        "thread_id",
        "event_sequence",
        "system_id",
        "campaign_id",
        "branch_id",
        "operation",
        "status",
        "audience",
        "actor_refs",
        "rolls",
        "outcome",
        "pending_choice",
        "campaign_revision",
        "random_stream_receipt",
    }
)
_AUDIENCE_SCOPES = frozenset({"dm", "principal", "actors"})


def _required_text(presentation: dict[str, Any], field: str) -> str:
    value = presentation.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"resolution presentation {field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"resolution presentation {field} must be a string list")
    return value


def normalize_resolution_presentation(value: Any) -> dict[str, Any] | None:
    """Return a defensive v1 projection, or ``None`` for unrelated tool output.

    The MCP remains responsible for authorization and audience projection. This
    consumer deliberately does not infer a broader audience or reconstruct data
    omitted by the MCP.
    """

    if not isinstance(value, dict) or value.get("schema") != RESOLUTION_PRESENTATION_SCHEMA:
        return None

    presentation = {key: deepcopy(item) for key, item in value.items() if key in _ALLOWED_FIELDS}
    for field in (
        "resolution_id",
        "thread_id",
        "system_id",
        "campaign_id",
        "operation",
        "status",
    ):
        _required_text(presentation, field)

    for field in ("event_sequence", "campaign_revision"):
        number = presentation.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ValueError(f"resolution presentation {field} must be a non-negative integer")
    if presentation["event_sequence"] == 0:
        raise ValueError("resolution presentation event_sequence must be positive")

    branch_id = presentation.get("branch_id")
    if branch_id is not None and (not isinstance(branch_id, str) or not branch_id.strip()):
        raise ValueError("resolution presentation branch_id must be null or a non-empty string")

    audience = presentation.get("audience")
    if not isinstance(audience, dict):
        raise ValueError("resolution presentation audience must be an object")
    scope = audience.get("scope")
    if scope not in _AUDIENCE_SCOPES:
        raise ValueError("resolution presentation audience scope is invalid")
    audience_actor_refs = _string_list(audience.get("actor_refs"), "audience.actor_refs")
    actor_refs = _string_list(presentation.get("actor_refs"), "actor_refs")
    if actor_refs != audience_actor_refs:
        raise ValueError("resolution presentation actor_refs must match its audience")
    if scope != "actors" and actor_refs:
        raise ValueError("non-actor resolution audience cannot contain actor_refs")
    disclosure = audience.get("disclosure")
    if not isinstance(disclosure, str) or not disclosure.strip():
        raise ValueError("resolution presentation audience disclosure must be a non-empty string")
    presentation["audience"] = {
        "scope": scope,
        "actor_refs": deepcopy(audience_actor_refs),
        "disclosure": disclosure,
    }

    if not isinstance(presentation.get("rolls"), list):
        raise ValueError("resolution presentation rolls must be a list")
    if not isinstance(presentation.get("outcome"), dict):
        raise ValueError("resolution presentation outcome must be an object")
    pending_choice = presentation.get("pending_choice")
    if pending_choice is not None and not isinstance(pending_choice, dict):
        raise ValueError("resolution presentation pending_choice must be null or an object")
    if not isinstance(presentation.get("random_stream_receipt"), dict):
        raise ValueError("resolution presentation random_stream_receipt must be an object")
    return presentation
