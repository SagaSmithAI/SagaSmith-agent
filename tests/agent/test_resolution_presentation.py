from types import SimpleNamespace

import pytest

from nanobot.agent.hook import SDKCaptureHook
from nanobot.agent.resolution_presentation import normalize_resolution_presentation
from nanobot.sdk.types import result_from_response


def presentation(**overrides):
    value = {
        "schema": "sagasmith.resolution-presentation/v1",
        "resolution_id": "resolution-1",
        "thread_id": "thread-1",
        "event_sequence": 1,
        "system_id": "dnd5e",
        "campaign_id": "campaign-1",
        "branch_id": None,
        "operation": "attack",
        "status": "settled",
        "audience": {"scope": "actors", "actor_refs": ["hero"], "disclosure": "private"},
        "actor_refs": ["hero"],
        "rolls": [],
        "outcome": {"hit": True},
        "pending_choice": None,
        "campaign_revision": 4,
        "random_stream_receipt": {"draw_count": 1},
    }
    value.update(overrides)
    return value


def test_normalizer_accepts_only_the_audience_safe_v1_surface() -> None:
    value = presentation(private_debug={"hidden_roll": 3})

    normalized = normalize_resolution_presentation(value)

    assert normalized is not None
    assert normalized["resolution_id"] == "resolution-1"
    assert "private_debug" not in normalized
    assert normalized is not value


def test_normalizer_rejects_a_claimed_projection_with_mismatched_audience() -> None:
    with pytest.raises(ValueError, match="must match"):
        normalize_resolution_presentation(presentation(actor_refs=["other-actor"]))


async def test_sdk_capture_hook_ignores_unrelated_structured_content() -> None:
    hook = SDKCaptureHook()
    call = SimpleNamespace(name="mcp_roll")

    await hook.after_execute_tool(None, call, None, None, SimpleNamespace(structured_content={"roll": 7}))
    await hook.after_execute_tool(
        None,
        call,
        None,
        None,
        SimpleNamespace(structured_content=presentation()),
    )

    assert [item["resolution_id"] for item in hook.resolution_presentations] == ["resolution-1"]


def test_sdk_run_result_exposes_captured_presentations_without_service() -> None:
    capture = SimpleNamespace(
        tools_used=[],
        messages=[],
        usage={},
        stop_reason="stop",
        error=None,
        resolution_presentations=[presentation()],
    )

    result = result_from_response(SimpleNamespace(content="done", metadata={}), capture)

    assert result.metadata["resolution_presentations"][0]["campaign_id"] == "campaign-1"
