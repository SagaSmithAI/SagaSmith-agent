from nanobot.agent.tools.base import ToolResult
from nanobot.bus.events import HostMediaEnvelope, OutboundMessage


def test_host_media_envelope_is_model_invisible_and_bounded() -> None:
    envelope = HostMediaEnvelope(
        path="/host/private/combat.png",
        caption="caption " * 300,
        alt_text="battle map",
        attachment_role="combat_grid",
        audience_projection="party_public",
        checksum="ABC123",
        fallback_text="grid unavailable",
    )
    result = ToolResult("combat rendered", media_envelopes=[envelope])

    assert str(result) == "combat rendered"
    assert envelope.path not in str(result)
    assert envelope.path not in repr(envelope)
    assert len(envelope.caption) <= 1024
    assert envelope.checksum == "abc123"
    assert result.media == (envelope.path,)


def test_outbound_host_media_deduplicates_by_checksum_and_preserves_legacy_paths() -> None:
    first = HostMediaEnvelope(path="first.png", checksum="same", caption="first")
    duplicate = HostMediaEnvelope(path="duplicate.png", checksum="same", caption="duplicate")
    message = OutboundMessage(
        channel="test",
        chat_id="room",
        content="done",
        media=["legacy.png"],
        media_envelopes=[first, duplicate],
    )

    rows = message.host_media()
    assert [(row.path, row.caption) for row in rows] == [
        ("first.png", "first"),
        ("legacy.png", ""),
    ]
    assert rows[1].fallback_text == "[attachment: legacy.png - send failed]"


def test_outbound_host_media_deduplicates_same_path_even_if_checksum_differs() -> None:
    message = OutboundMessage(
        channel="test",
        chat_id="room",
        content="done",
        media_envelopes=[
            HostMediaEnvelope(path="combat.png", checksum="old"),
            HostMediaEnvelope(path="combat.png", checksum="new"),
        ],
    )

    assert len(message.host_media()) == 1
