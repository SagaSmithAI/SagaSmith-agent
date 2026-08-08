"""Tests for WebUI transcript cleanup."""

from __future__ import annotations

from nanobot.webui.thread_disk import delete_webui_thread
from nanobot.webui.transcript import (
    append_transcript_object,
    webui_transcript_path,
    webui_transcript_segments_dir,
)


def test_delete_webui_thread_removes_transcript(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("nanobot.webui.transcript._MAX_TRANSCRIPT_FILE_BYTES", 520)
    monkeypatch.setattr("nanobot.webui.transcript._TARGET_ACTIVE_TRANSCRIPT_BYTES", 260)
    key = "websocket:k1"
    for idx in range(1, 5):
        append_transcript_object(
            key,
            {"event": "user", "chat_id": "k1", "text": f"question {idx} " + ("x" * 24)},
        )
        append_transcript_object(
            key,
            {"event": "message", "chat_id": "k1", "text": f"answer {idx} " + ("y" * 24)},
        )
        append_transcript_object(key, {"event": "turn_end", "chat_id": "k1"})
    assert webui_transcript_path(key).is_file()
    assert webui_transcript_segments_dir(key).is_dir()
    assert delete_webui_thread(key) is True
    assert not webui_transcript_path(key).is_file()
    assert not webui_transcript_segments_dir(key).exists()
    assert delete_webui_thread(key) is False
