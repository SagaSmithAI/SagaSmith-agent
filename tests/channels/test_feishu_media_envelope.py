from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("lark_oapi")

from nanobot.bus.events import HostMediaEnvelope, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.feishu import FeishuChannel, FeishuConfig


@pytest.mark.asyncio
async def test_send_image_and_accessibility_copy_as_one_native_post(tmp_path) -> None:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="cli_test", app_secret="secret", allow_from=["*"]),
        MessageBus(),
    )
    channel._client = MagicMock()
    sent: list[tuple[str, str, str, str]] = []
    channel._upload_image_sync = lambda _path: "img_combat"
    channel._send_message_sync = lambda *args: sent.append(args)
    image = tmp_path / "combat.png"
    image.write_bytes(b"png")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_room",
            content="",
            media_envelopes=[
                HostMediaEnvelope(
                    path=str(image),
                    caption="Round 3 positions",
                    alt_text="A goblin stands north of the fighter.",
                )
            ],
        )
    )

    receive_id_type, chat_id, msg_type, content = sent[0]
    assert (receive_id_type, chat_id, msg_type) == ("chat_id", "oc_room", "post")
    post = json.loads(content)
    assert post["zh_cn"]["content"][0] == [
        {"tag": "img", "image_key": "img_combat"},
        {
            "tag": "text",
            "text": "Round 3 positions\n\nAlt: A goblin stands north of the fighter.",
        },
    ]
    assert channel.media_capabilities().native_card is True


@pytest.mark.asyncio
async def test_media_upload_failure_does_not_suppress_authoritative_text(tmp_path) -> None:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="cli_test", app_secret="secret", allow_from=["*"]),
        MessageBus(),
    )
    channel._client = MagicMock()
    sent: list[tuple[str, str, str, str]] = []

    def _fail_upload(_path: str) -> str:
        raise OSError("upload failed")

    channel._upload_image_sync = _fail_upload
    channel._send_message_sync = lambda *args: sent.append(args)
    image = tmp_path / "combat.png"
    image.write_bytes(b"png")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_room",
            content="Combat updated.",
            media_envelopes=[
                HostMediaEnvelope(
                    path=str(image),
                    fallback_text="Grid unavailable; dragon remains at C4.",
                )
            ],
        )
    )

    assert [row[2] for row in sent] == ["text", "text"]
    assert json.loads(sent[0][3]) == {"text": "Grid unavailable; dragon remains at C4."}
    assert json.loads(sent[1][3]) == {"text": "Combat updated."}
