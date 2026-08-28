"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.bus.outbound_events import OutboundEvent

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Internal-only inbound metadata used by in-process channels to ask the agent
# loop to update runtime state without going through a user session.
INBOUND_META_RUNTIME_CONTROL = "_runtime_control"
RUNTIME_CONTROL_ACK = "_ack"
RUNTIME_CONTROL_MCP_RELOAD = "mcp_reload"

_HOST_MEDIA_CAPTION_LIMIT = 1024
_HOST_MEDIA_ALT_LIMIT = 2048
_HOST_MEDIA_FALLBACK_LIMIT = 512


def _bounded_host_text(value: str | None, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class HostMediaEnvelope:
    """Host-only attachment metadata; never serialize this into model context."""

    path: str = field(repr=False)
    mime_type: str = "image/png"
    caption: str = ""
    alt_text: str = ""
    attachment_role: str = "image"
    audience_projection: str | None = None
    checksum: str | None = None
    fallback_text: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path or "").strip())
        object.__setattr__(self, "mime_type", str(self.mime_type or "application/octet-stream"))
        object.__setattr__(
            self, "caption", _bounded_host_text(self.caption, _HOST_MEDIA_CAPTION_LIMIT)
        )
        object.__setattr__(self, "alt_text", _bounded_host_text(self.alt_text, _HOST_MEDIA_ALT_LIMIT))
        role = _bounded_host_text(self.attachment_role, 64) or "attachment"
        object.__setattr__(self, "attachment_role", role)
        audience = _bounded_host_text(self.audience_projection, 32) or None
        object.__setattr__(self, "audience_projection", audience)
        checksum = _bounded_host_text(self.checksum, 128).casefold() or None
        object.__setattr__(self, "checksum", checksum)
        fallback = _bounded_host_text(self.fallback_text, _HOST_MEDIA_FALLBACK_LIMIT)
        if not fallback:
            fallback = f"[{role.replace('_', ' ')} attachment unavailable]"
        object.__setattr__(self, "fallback_text", fallback)

    @property
    def dedup_key(self) -> str:
        if self.checksum:
            return f"checksum:{self.checksum}"
        try:
            return f"path:{Path(self.path).expanduser().resolve(strict=False)}"
        except OSError:
            return f"path:{self.path}"


@dataclass(frozen=True, slots=True)
class HostMediaCapabilities:
    """Discoverable native-media behavior for one channel adapter."""

    atomic_caption: bool = False
    native_alt_text: bool = False
    native_card: bool = False
    max_file_bytes: int | None = None
    max_caption_chars: int = 0
    supports_multi_image: bool = True


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    actor_principal: str | None = None  # Trusted sender identity for authorization/tool calls
    conversation_principal: str | None = None  # Trusted room/group identity for routing/audit

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``event`` carries internal runtime/UI semantics. ``metadata`` is reserved
    for channel routing context (``message_id``, thread ids, etc.) and optional
    ``OUTBOUND_META_AGENT_UI`` blobs for rich clients.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    media_envelopes: list[HostMediaEnvelope] = field(default_factory=list, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
    event: "OutboundEvent | None" = None

    def host_media(self) -> list[HostMediaEnvelope]:
        """Return explicit envelopes plus legacy paths, deduplicated deterministically."""
        rows = [*self.media_envelopes]
        explicit_paths = {row.path for row in rows}
        rows.extend(
            HostMediaEnvelope(
                path=path,
                fallback_text=f"[attachment: {Path(path).name} - send failed]",
            )
            for path in self.media
            if isinstance(path, str) and path.strip() and path not in explicit_paths
        )
        deduplicated: list[HostMediaEnvelope] = []
        seen_checksums: set[str] = set()
        seen_paths: set[str] = set()
        for row in rows:
            if not row.path:
                continue
            path_key = HostMediaEnvelope(path=row.path).dedup_key
            checksum_key = row.dedup_key if row.checksum else None
            if path_key in seen_paths or (checksum_key is not None and checksum_key in seen_checksums):
                continue
            seen_paths.add(path_key)
            if checksum_key is not None:
                seen_checksums.add(checksum_key)
            deduplicated.append(row)
        return deduplicated
