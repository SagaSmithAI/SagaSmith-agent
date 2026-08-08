"""WebUI transcript deletion."""

from __future__ import annotations

from nanobot.webui.transcript import delete_webui_transcript


def delete_webui_thread(session_key: str) -> bool:
    """Remove the append-only transcript for *session_key*."""
    return delete_webui_transcript(session_key)
