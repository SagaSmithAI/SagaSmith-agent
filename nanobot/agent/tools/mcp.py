"""MCP client: connects to MCP servers and wraps their tools as native nanobot tools."""

import asyncio
import hashlib
import importlib
import json
import os
import re
import secrets
import shutil
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from datetime import datetime
from typing import Any, Mapping, Protocol
from weakref import WeakKeyDictionary

import httpx
import httpx2
from loguru import logger

from nanobot.agent.auth_context import (
    AUTH_CONTEXT_META_KEY,
    AUTH_CONTEXT_RECEIPT_META_KEY,
    sign_auth_context,
    sign_delegated_auth_context,
)
from nanobot.agent.domain_context import (
    DomainContextBinding,
    bind_session_context,
    binding_from_metadata,
    principal_fingerprint,
)
from nanobot.agent.mcp_observability import record_mcp_event
from nanobot.agent.mcp_tasks import (
    TasksExtension,
    TaskTimeoutControl,
    task_authorization_context,
    task_authorization_from_meta,
    task_timeout_context,
)
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MCP_RELOAD,
    HostMediaEnvelope,
    InboundMessage,
)
from nanobot.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from nanobot.security.network import (
    PinnedDNSAsyncTransport,
    env_proxy_applies_to_url,
    httpx_env_proxy_mounts,
    resolve_url_target,
    validate_url_target,
)
from nanobot.utils.cancellation import task_is_cancelling

_MCP_AUTHORIZATION_EPOCHS_KEY = "_mcp_authorization_epochs"

# Transient connection errors that warrant a single retry.
# These typically happen when an MCP server restarts or a network
# connection is interrupted between calls.
_TRANSIENT_EXC_NAMES: frozenset[str] = frozenset(
    (
        "ClosedResourceError",
        "BrokenResourceError",
        "EndOfStream",
        "BrokenPipeError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "ConnectionAbortedError",
        "ConnectionError",
    )
)

_WINDOWS_SHELL_LAUNCHERS: frozenset[str] = frozenset(("npx", "npm", "pnpm", "yarn", "bunx"))

# Characters allowed in tool names by model providers (Anthropic, OpenAI, etc.).
# Replace anything outside [a-zA-Z0-9_-] with underscore and collapse runs.
_SANITIZE_RE = re.compile(r"_+")
_RELOAD_LOCKS: WeakKeyDictionary[Any, asyncio.Lock] = WeakKeyDictionary()
_ReconnectCallback = Callable[[str, str, Tool], Awaitable[Tool | None]]
_PostCallSyncCallback = Callable[
    [bool, frozenset[str], frozenset[str]], Awaitable[None]
]
_MCP_CONTEXT_TOOL_LIMIT = 48
_MCP_EXPOSURE_REFRESH_TIMEOUT_SECONDS = 1.0


def _mcp_field(value: Any, snake_name: str, camel_name: str, default: Any = None) -> Any:
    """Read SDK v2 snake_case fields and legacy/test-double camelCase fields."""

    if hasattr(value, snake_name):
        return getattr(value, snake_name)
    return getattr(value, camel_name, default)


def _serialize_call_tool_result(result: Any) -> dict[str, Any]:
    """Preserve the standard MCP result envelope at the Host boundary."""

    dump = getattr(result, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True, exclude_none=True)
    content = []
    for block in getattr(result, "content", ()):
        block_dump = getattr(block, "model_dump", None)
        if callable(block_dump):
            content.append(block_dump(mode="json", by_alias=True, exclude_none=True))
        elif isinstance(block, Mapping):
            content.append(dict(block))
        else:
            content.append({"type": "text", "text": str(block)})
    payload: dict[str, Any] = {"content": content}
    structured = _mcp_field(result, "structured_content", "structuredContent")
    if structured is not None:
        payload["structuredContent"] = structured
    if _mcp_field(result, "is_error", "isError", False):
        payload["isError"] = True
    metadata = _mcp_field(result, "meta", "_meta")
    if isinstance(metadata, Mapping):
        payload["_meta"] = dict(metadata)
    return payload


def _first_string_field(value: Any, names: frozenset[str]) -> str | None:
    """Read identity fields only from protocol-owned envelope containers."""

    if isinstance(value, Mapping):
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for key in ("authority", "branch", "payload", "data", "result"):
            nested = value.get(key)
            if not isinstance(nested, Mapping):
                continue
            if (candidate := _first_string_field(nested, names)) is not None:
                return candidate
    return None


def _host_context_binding(value: Any) -> Mapping[str, Any] | None:
    """Read a binding only from code-owned MCP envelope positions."""

    if isinstance(value, Mapping):
        candidate = value.get("host_context_binding")
        if isinstance(candidate, Mapping):
            return candidate
        authority = value.get("authority")
        if isinstance(authority, Mapping):
            candidate = authority.get("host_context_binding")
            if isinstance(candidate, Mapping):
                return candidate
        result = value.get("result")
        if isinstance(result, Mapping):
            return _host_context_binding(result)
    return None


def _context_payload_from_result(content: Any, structured_content: Any) -> Any:
    """Prefer a Host binding carried in MCP text over a redacted structured result."""

    if _host_context_binding(structured_content) is not None:
        return structured_content
    for block in content or []:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if _host_context_binding(decoded) is not None:
            return decoded
    return structured_content


def _auth_context_receipt_from_result(content: Any) -> dict[str, Any] | None:
    """Read code-owned audit metadata without rendering it into model content."""

    for block in content or []:
        metadata = getattr(block, "meta", None)
        if not isinstance(metadata, Mapping):
            continue
        receipt = metadata.get(AUTH_CONTEXT_RECEIPT_META_KEY)
        if isinstance(receipt, Mapping):
            return dict(receipt)
    return None


def routing_context_provider(registry: ToolRegistry):
    """Return per-turn MCP routing context for the tools currently connected."""

    async def provide(_request: Any) -> RuntimeContextBlock | None:
        names = sorted(name for name in registry.definition_names() if name.startswith("mcp_"))
        if not names:
            return None
        visible = names[:_MCP_CONTEXT_TOOL_LIMIT]
        extra = len(names) - len(visible)
        lines = [
            "MCP-first routing: when an `mcp_*` capability matches the task, use it "
            "before shell commands, temporary scripts, or direct data access.",
            "MCP tools own their domain state; use MCP prompts/resources before recreating "
            "their instructions locally.",
            "Available MCP capabilities: " + ", ".join(visible),
        ]
        if extra:
            lines.append(f"{extra} additional MCP capabilities are available through tool calling.")
        return RuntimeContextBlock(
            source="mcp_routing",
            content=wrap_runtime_context_lines(lines),
        )

    return provide


class MCPConnection(Protocol):
    async def aclose(self) -> None: ...


class _OwnedMCPConnection:
    """Close an MCP transport from the task that originally opened it."""

    def __init__(self, owner: asyncio.Task[None], close_requested: asyncio.Event) -> None:
        self._owner = owner
        self._close_requested = close_requested

    async def aclose(self) -> None:
        self._close_requested.set()
        try:
            await asyncio.shield(self._owner)
        except asyncio.CancelledError:
            if not self._owner.cancelled():
                raise


def _is_malformed_mcp_progress_notification(message: Any) -> bool:
    payload = _mcp_jsonrpc_payload(message)
    if _payload_value(payload, "method") != "notifications/progress":
        return False

    params = _payload_value(payload, "params")
    return not _progress_params_have_token(params)


def _mcp_jsonrpc_payload(message: Any) -> Any:
    """Return the JSON-RPC payload across current and future MCP SDK shapes."""
    envelope = getattr(message, "message", message)
    return getattr(envelope, "root", None) or envelope


def _payload_value(payload: Any, key: str) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)


def _progress_params_have_token(params: Any) -> bool:
    if isinstance(params, Mapping):
        return "progressToken" in params
    return hasattr(params, "progressToken") or hasattr(params, "progress_token")


class _MalformedProgressNotificationFilter:
    def __init__(self, read_stream: Any, server_name: str) -> None:
        self._read_stream = read_stream
        self._server_name = server_name
        self._iterator: Any | None = None

    async def __aenter__(self) -> "_MalformedProgressNotificationFilter":
        await self._read_stream.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return await self._read_stream.__aexit__(exc_type, exc, tb)

    def __aiter__(self) -> "_MalformedProgressNotificationFilter":
        self._iterator = self._read_stream.__aiter__()
        return self

    async def __anext__(self) -> Any:
        if self._iterator is None:
            self._iterator = self._read_stream.__aiter__()

        while True:
            message = await self._iterator.__anext__()
            if _is_malformed_mcp_progress_notification(message):
                logger.debug(
                    "MCP server '{}': dropped progress notification without progressToken",
                    self._server_name,
                )
                continue
            return message

    async def aclose(self) -> None:
        close = getattr(self._read_stream, "aclose", None)
        if close is not None:
            await close()


def _filter_malformed_mcp_progress_notifications(read_stream: Any, server_name: str) -> Any:
    if not all(hasattr(read_stream, name) for name in ("__aenter__", "__aexit__", "__aiter__")):
        return read_stream
    return _MalformedProgressNotificationFilter(read_stream, server_name)


def _sanitize_name(name: str) -> str:
    """Sanitize an MCP-derived name for model API compatibility."""
    return _SANITIZE_RE.sub("_", re.sub(r"[^a-zA-Z0-9_-]", "_", name))


_MAX_TOOL_NAME_LENGTH = 64
_HASH_LENGTH = 8


def _limit_tool_name(name: str, max_length: int = _MAX_TOOL_NAME_LENGTH) -> str:
    """Limit a tool name while keeping short names unchanged."""
    if len(name) <= max_length:
        return name

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
    prefix_length = max_length - _HASH_LENGTH - 1
    return f"{name[:prefix_length]}_{digest}"


def _sanitize_mcp_tool_name(name: str) -> str:
    """Sanitize and limit an MCP-derived tool name."""
    return _limit_tool_name(_sanitize_name(name))


def _is_transient(exc: BaseException) -> bool:
    """Check if an exception looks like a transient connection error."""
    return type(exc).__name__ in _TRANSIENT_EXC_NAMES


def _is_session_terminated(exc: BaseException) -> bool:
    """Return True when the MCP SDK reports a dead client session."""
    if _is_transient(exc):
        return True
    messages = [str(exc)]
    error = getattr(exc, "error", None)
    if error is not None:
        messages.append(str(getattr(error, "message", "")))
    return any(
        marker in message.lower()
        for marker in ("session terminated", "connection closed")
        for message in messages
    )


def _mcp_error_details(exc: BaseException) -> tuple[int | str, str]:
    error = getattr(exc, "error", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if error is not None:
        code = getattr(error, "code", code)
        message = getattr(error, "message", message)
    return code if code is not None else "unknown", str(message or exc)


async def _probe_http_url(url: str, timeout: float = 3.0) -> bool:
    """Quick TCP probe to check if an HTTP MCP server is reachable.

    Avoids entering ``streamable_http_client`` / ``sse_client`` when the port is
    closed — those transports use anyio task groups whose cleanup can raise
    ``RuntimeError`` / ``ExceptionGroup`` that escape the caller's try/except
    and crash the event loop.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        port = 443 if parsed.scheme == "https" else 80
    ok, _, resolved_ips = resolve_url_target(url)
    if not ok:
        return False
    if env_proxy_applies_to_url(url):
        return True
    for target_host in resolved_ips or (host,):
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, port),
                timeout=timeout,
            )
            writer.close()
            with suppress(OSError, asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), timeout=0.2)
            return True
        except (OSError, asyncio.TimeoutError):
            continue
    return False


def _redact_url(url: str) -> str:
    """Strip credentials and query/fragment before logging an MCP URL.

    Server URLs may embed secrets (``https://user:token@host/sse`` or a
    ``?token=`` query). Some deployments also put opaque tokens in the path, so
    log only the origin and a path placeholder.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = parts.hostname or ""
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        path = "/..." if parts.path and parts.path != "/" else parts.path
        return urllib.parse.urlunsplit((parts.scheme, netloc, path, "", ""))
    except Exception:
        return "<redacted-url>"


class _SafeRedirectTransport(httpx.AsyncBaseTransport):
    """Validate redirect destinations before httpx follows them."""

    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self._delegate = delegate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._delegate.handle_async_request(request)
        location = response.headers.get("location")
        if location and 300 <= response.status_code < 400:
            target = str(request.url.join(location))
            ok, error = validate_url_target(target)
            if not ok:
                await response.aclose()
                raise httpx.RequestError(
                    f"Blocked unsafe MCP redirect {_redact_url(target)} ({error})",
                    request=request,
                )
        return response

    async def aclose(self) -> None:
        await self._delegate.aclose()


def _pinned_transport_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"transport": _SafeRedirectTransport(PinnedDNSAsyncTransport())}
    mounts = httpx_env_proxy_mounts()
    if mounts:
        kwargs["mounts"] = {
            pattern: (None if transport is None else _SafeRedirectTransport(transport))
            for pattern, transport in mounts.items()
        }
    return kwargs


async def _validate_mcp_request_url(request: httpx.Request) -> None:
    """Validate each outgoing MCP HTTP request, including redirect targets."""
    ok, error = validate_url_target(str(request.url))
    if not ok:
        raise httpx.RequestError(
            f"Blocked unsafe MCP URL {_redact_url(str(request.url))} ({error})",
            request=request,
        )


async def _validate_mcp_redirect_response(response: httpx.Response) -> None:
    """Validate a redirect Location before httpx follows it."""
    location = response.headers.get("location")
    if not location or not (300 <= response.status_code < 400):
        return
    target = str(response.request.url.join(location))
    ok, error = validate_url_target(target)
    if not ok:
        raise httpx.RequestError(
            f"Blocked unsafe MCP redirect {_redact_url(target)} ({error})",
            request=response.request,
        )


async def _validate_mcp2_request_url(request: httpx2.Request) -> None:
    """Apply the same per-request SSRF policy to the MCP v2 HTTP stack."""

    ok, error = validate_url_target(str(request.url))
    if not ok:
        raise httpx2.RequestError(
            f"Blocked unsafe MCP URL {_redact_url(str(request.url))} ({error})",
            request=request,
        )


async def _reject_mcp2_redirect_response(response: httpx2.Response) -> None:
    """Reject redirects so a validated MCP origin cannot redirect to a private target."""

    location = response.headers.get("location")
    if location and 300 <= response.status_code < 400:
        target = str(response.request.url.join(location))
        await response.aclose()
        raise httpx2.RequestError(
            f"MCP redirects are disabled; refused {_redact_url(target)}",
            request=response.request,
        )


def _windows_command_basename(command: str) -> str:
    """Return the lowercase basename for a Windows command or path."""
    return command.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()


def _normalize_windows_stdio_command(
    command: str,
    args: list[str] | None,
    env: dict[str, str] | None,
) -> tuple[str, list[str], dict[str, str] | None]:
    """Wrap Windows shell launchers so MCP stdio servers start reliably."""
    normalized_args = list(args or [])
    if os.name != "nt":
        return command, normalized_args, env

    basename = _windows_command_basename(command)
    if basename in {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return command, normalized_args, env

    if basename.endswith((".exe", ".com")):
        return command, normalized_args, env

    resolved = shutil.which(command, path=(env or {}).get("PATH")) or command
    resolved_basename = _windows_command_basename(resolved)
    should_wrap = (
        basename in _WINDOWS_SHELL_LAUNCHERS
        or basename.endswith((".cmd", ".bat"))
        or resolved_basename.endswith((".cmd", ".bat"))
    )
    if not should_wrap:
        return command, normalized_args, env

    comspec = (env or {}).get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
    return comspec, ["/d", "/c", command, *normalized_args], env


def _extract_nullable_branch(options: Any) -> tuple[dict[str, Any], bool] | None:
    """Return the single non-null branch for nullable unions."""
    if not isinstance(options, list):
        return None

    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
            continue
        non_null.append(option)

    if saw_null and len(non_null) == 1:
        return non_null[0], True
    return None


def _normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    """Normalize only nullable JSON Schema patterns for tool definitions."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True

    for key in ("oneOf", "anyOf"):
        nullable_branch = _extract_nullable_branch(normalized.get(key))
        if nullable_branch is not None:
            branch, _ = nullable_branch
            merged = {k: v for k, v in normalized.items() if k != key}
            merged.update(branch)
            normalized = merged
            normalized["nullable"] = True
            break

    if "properties" in normalized and isinstance(normalized["properties"], dict):
        normalized["properties"] = {
            name: _normalize_schema_for_openai(prop) if isinstance(prop, dict) else prop
            for name, prop in normalized["properties"].items()
        }

    if "items" in normalized and isinstance(normalized["items"], dict):
        normalized["items"] = _normalize_schema_for_openai(normalized["items"])

    if normalized.get("type") != "object":
        return normalized

    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    required = set(normalized["required"])
    for name, prop in normalized["properties"].items():
        if (
            name not in required
            and isinstance(prop, dict)
            and prop.get("nullable") is True
        ):
            # MCP/Pydantic commonly describes an omitted optional parameter as
            # ``anyOf: [value, null]`` with ``default: null``.  Responses tools
            # need only the non-null branch when the property is not required;
            # retaining the non-standard nullable marker encourages models to
            # fabricate string/list/number null sentinels instead of omitting
            # the argument.  Required nullable properties keep their marker.
            prop.pop("nullable", None)
            if prop.get("default") is None:
                prop.pop("default", None)
    return normalized


class _MCPWrapperBase(Tool):
    """Common reconnect handling for wrappers bound to one MCP server session."""

    _plugin_discoverable = False

    def _set_mcp_connection(self, session: Any, server_name: str) -> None:
        self._session = session
        self._server_name = server_name
        self._reconnect: _ReconnectCallback | None = None

    def set_reconnect_handler(self, reconnect: _ReconnectCallback) -> None:
        self._reconnect = reconnect

    def is_available(self, context: Any | None) -> bool:
        """Apply the trusted per-turn operation allowlist at model and call time."""

        operations = tuple(getattr(context, "allowed_operations", ()) or ())
        if not operations:
            return True
        operation = getattr(self, "_original_name", None)
        return isinstance(operation, str) and operation in operations

    async def _refresh_session_after_termination(
        self,
        exc: BaseException,
        already_refreshed: bool,
        capability_kind: str,
    ) -> bool:
        if already_refreshed or not _is_session_terminated(exc) or self._reconnect is None:
            return False
        logger.warning(
            "MCP {} '{}' session terminated; reconnecting server '{}' before retry",
            capability_kind,
            self._name,
            self._server_name,
        )
        refreshed_tool = await self._reconnect(self._server_name, self._name, self)
        refreshed_session = getattr(refreshed_tool, "_session", None)
        if refreshed_session is None:
            logger.warning(
                "MCP {} '{}' could not refresh session for server '{}'",
                capability_kind,
                self._name,
                self._server_name,
            )
            return False
        self._session = refreshed_session
        return True


def _image_block_data_url(block: Any, types: Any) -> str | None:
    """Return a base64 ``data:`` URL for an MCP image-bearing content block.

    Handles ``ImageContent`` directly and ``EmbeddedResource`` wrapping a binary
    blob with an ``image/*`` MIME type. Returns ``None`` for anything else.
    ``getattr`` guards keep this safe when the installed/faked ``mcp`` SDK does
    not expose a given type.
    """
    image_cls = getattr(types, "ImageContent", None)
    if image_cls is not None and isinstance(block, image_cls):
        mime = _mcp_field(block, "mime_type", "mimeType", "image/png")
        return f"data:{mime};base64,{block.data}"

    embedded_cls = getattr(types, "EmbeddedResource", None)
    blob_cls = getattr(types, "BlobResourceContents", None)
    if embedded_cls is not None and isinstance(block, embedded_cls):
        resource = getattr(block, "resource", None)
        if blob_cls is not None and isinstance(resource, blob_cls):
            mime = _mcp_field(resource, "mime_type", "mimeType", "")
            if isinstance(mime, str) and mime.startswith("image/"):
                return f"data:{mime};base64,{resource.blob}"
    return None


def _media_block_payload(block: Any, types: Any) -> tuple[str, str] | None:
    """Return base64 data and MIME type for standard MCP image/audio blocks."""

    for class_name, fallback in (("ImageContent", "image/png"), ("AudioContent", "audio/mpeg")):
        content_cls = getattr(types, class_name, None)
        if content_cls is not None and isinstance(block, content_cls):
            mime = str(_mcp_field(block, "mime_type", "mimeType", fallback) or fallback)
            return str(block.data), mime
    embedded_cls = getattr(types, "EmbeddedResource", None)
    blob_cls = getattr(types, "BlobResourceContents", None)
    if embedded_cls is not None and isinstance(block, embedded_cls):
        resource = getattr(block, "resource", None)
        if blob_cls is not None and isinstance(resource, blob_cls):
            mime = str(_mcp_field(resource, "mime_type", "mimeType", "") or "")
            if mime.startswith(("image/", "audio/")):
                return str(resource.blob), mime
    return None


def _request_is_shared_conversation() -> bool:
    """Return whether the active tool request is known to target a shared chat."""
    request = current_request_context()
    if request is None:
        return False
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    is_group = metadata.get("is_group")
    if is_group is True:
        return True
    chat_type = str(metadata.get("chat_type") or "").strip().casefold()
    if chat_type in {"group", "room", "channel", "guild", "supergroup", "thread"}:
        return True
    conversation = str(request.conversation_principal or "").strip().casefold()
    if not conversation:
        return False
    parts = {part for part in re.split(r"[:/]+", conversation) if part}
    return bool(parts & {"group", "room", "channel", "guild", "supergroup", "thread"})


def _shared_media_delivery_blocked(structured_content: Any) -> bool:
    """Only explicit party-public projections may cross into a shared chat."""
    if not isinstance(structured_content, Mapping):
        return False
    projection = str(structured_content.get("audience_projection") or "").strip().casefold()
    # Preserve ordinary MCP images that predate projection metadata.  Once a
    # server opts into projection semantics, fail closed on unknown values.
    return bool(projection and projection != "party_public" and _request_is_shared_conversation())


def _host_media_envelope(
    artifact: Mapping[str, Any],
    structured_content: Any,
) -> HostMediaEnvelope:
    metadata = structured_content if isinstance(structured_content, Mapping) else {}
    share_card = metadata.get("share_card")
    share_card = share_card if isinstance(share_card, Mapping) else {}
    caption = str(
        metadata.get("suggested_caption")
        or share_card.get("suggested_caption")
        or metadata.get("caption")
        or ""
    )
    alt_text = str(metadata.get("alt_text") or share_card.get("alt_text") or "")
    audience = str(metadata.get("audience_projection") or "") or None
    attachment_role = str(metadata.get("attachment_role") or "")
    if not attachment_role:
        artifact_mime = str(artifact.get("mime") or metadata.get("mime_type") or "")
        attachment_role = (
            "audio" if artifact_mime.startswith("audio/") else "combat_grid" if audience else "image"
        )
    fallback = str(metadata.get("fallback_text") or alt_text or caption or "")
    return HostMediaEnvelope(
        path=str(artifact.get("path") or ""),
        mime_type=str(
            artifact.get("mime") or metadata.get("mime_type") or "application/octet-stream"
        ),
        caption=caption,
        alt_text=alt_text,
        attachment_role=attachment_role,
        audience_projection=audience,
        checksum=str(artifact.get("checksum") or metadata.get("image_checksum") or "") or None,
        fallback_text=fallback,
    )


_HOST_ONLY_MEDIA_KEYS = frozenset(
    {
        "alt_text",
        "artifact_path",
        "attachment_role",
        "audience_projection",
        "base64",
        "caption",
        "checksum",
        "data_url",
        "fallback_text",
        "file_path",
        "image_base64",
        "image_checksum",
        "local_path",
        "path",
        "share_card",
        "suggested_caption",
    }
)


def _model_visible_image_payload(value: Any) -> Any:
    """Remove transport-only image metadata while retaining authoritative game state."""
    if isinstance(value, Mapping):
        return {
            str(key): _model_visible_image_payload(item)
            for key, item in value.items()
            if str(key).casefold() not in _HOST_ONLY_MEDIA_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_model_visible_image_payload(item) for item in value]
    if isinstance(value, str) and value.strip().casefold().startswith("data:image/"):
        return "(host-only image data omitted)"
    return value


def _mcp_image_tool_result(
    text_parts: list[str],
    artifacts: list[dict[str, Any]],
    *,
    delivery_blocked: bool = False,
) -> str:
    """Build the compact tool result for an MCP call that returned image(s).

    Host-only artifact paths and image bytes stay out of model context. Delivery
    is handled by the agent host through ``ToolResult.media``.
    """
    if delivery_blocked:
        return json.dumps(
            {
                "delivery": (
                    "Automatic attachment was blocked because this caller-only image "
                    "targets a shared conversation."
                ),
                "suggested_audience_projection": "party_public",
            },
            ensure_ascii=False,
        )
    payload: dict[str, Any] = {
        "images": [
            {key: artifact[key] for key in ("id", "mime") if artifact.get(key)}
            for artifact in artifacts
        ],
        "delivery": "The host saved and will attach these images to the current conversation.",
    }
    text = "\n".join(part for part in text_parts if part)
    if text:
        payload["text"] = text
    return json.dumps(payload, ensure_ascii=False)


class MCPToolWrapper(_MCPWrapperBase):
    """Wraps a single MCP server tool as a nanobot Tool."""

    _plugin_discoverable = False

    def __init__(
        self,
        session,
        server_name: str,
        tool_def,
        tool_timeout: int = 30,
        task_timeout: int = 900,
        *,
        inject_principal: bool = False,
        auth_context_secret: str = "",
        delegation_secret: str = "",
        authorization_audience: str = "",
        target_service: str = "",
        session_store: Any | None = None,
        post_call_sync: _PostCallSyncCallback | None = None,
        call_lock: asyncio.Lock | None = None,
        transport: str = "unknown",
        protocol: str = "unknown",
    ):
        self._set_mcp_connection(session, server_name)
        self._original_name = tool_def.name
        self._name = _sanitize_mcp_tool_name(f"mcp_{server_name}_{tool_def.name}")
        self._description = tool_def.description or tool_def.name
        raw_schema = _mcp_field(tool_def, "input_schema", "inputSchema") or {
            "type": "object",
            "properties": {},
        }
        self._parameters = _normalize_schema_for_openai(raw_schema)
        self._tool_timeout = tool_timeout
        self._task_timeout = task_timeout
        self._inject_principal = inject_principal
        self._auth_context_secret = auth_context_secret
        self._delegation_secret = delegation_secret
        self._authorization_audience = authorization_audience or server_name
        self._target_service = target_service or server_name
        self._session_store = session_store
        self._post_call_sync = post_call_sync
        self._call_lock = call_lock
        self._metrics_transport = transport
        self._metrics_protocol = protocol
        meta = getattr(tool_def, "meta", None)
        if not isinstance(meta, dict):
            meta = {}
        domain_context = meta.get("sagasmith_domain_context")
        self._domain_context = (
            domain_context.strip()
            if isinstance(domain_context, str) and domain_context.strip()
            else None
        )
        self._context_sync = meta.get("sagasmith_context_sync") is True
        properties = self._parameters.get("properties", {})
        # Grant tools have a subject principal_id and a separate caller field.
        # Prefer the caller field so transport identity can never overwrite the
        # principal being granted access.  Older tools use principal_id itself.
        if "auth_principal_id" in properties:
            self._principal_argument = "auth_principal_id"
        elif "by_principal_id" in properties:
            self._principal_argument = "by_principal_id"
        elif "principal_id" in properties:
            self._principal_argument = "principal_id"
        else:
            self._principal_argument = None
        principal_schema = (
            properties.get(self._principal_argument)
            if self._principal_argument is not None
            else None
        )
        advertised_default = (
            principal_schema.get("default") if isinstance(principal_schema, dict) else None
        )
        self._local_principal_default = (
            advertised_default.strip()
            if isinstance(advertised_default, str) and advertised_default.strip()
            else None
        )
        if inject_principal:
            # Transport-authentication input is never an LLM argument.  Tools
            # without a trusted identity parameter receive no injected field.
            if self._principal_argument is not None:
                properties.pop(self._principal_argument, None)
            required = self._parameters.get("required")
            if isinstance(required, list):
                self._parameters["required"] = [
                    item for item in required if item != self._principal_argument
                ]
        # These values come from the trusted Host request envelope. Keeping
        # them out of the model schema prevents identity/revision selection by
        # prompt text while preserving compatibility with explicit-argument servers.
        self._trusted_arguments = frozenset(
            name
            for name in (
                "room_turn_id",
                "base_revision",
                "idempotency_key",
                "campaign_id",
                "acting_character_id",
            )
            if name in properties
        )
        for name in self._trusted_arguments:
            properties.pop(name, None)
        required = self._parameters.get("required")
        if isinstance(required, list):
            self._parameters["required"] = [
                item for item in required if item not in self._trusted_arguments
            ]

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    def _trusted_principal(self) -> str:
        # Wrappers are shared across sessions, so identity must remain task-local.
        ctx = current_request_context()
        if ctx is not None and ctx.requester_principal:
            return ctx.requester_principal
        actor_principal = ctx.actor_principal if ctx is not None else None
        if ctx is None or not (actor_principal or ctx.sender_id):
            # Local cron/CLI calls have no user identity. They remain explicitly
            # distinguishable from an inbound platform user and cannot be spoofed
            # by a model parameter. The capability server owns the local identity;
            # the generic Agent adapter must not invent its own principal name.
            if self._local_principal_default is None:
                raise RuntimeError("MCP tool does not advertise a local principal default")
            return self._local_principal_default
        channel = re.sub(r"[^a-zA-Z0-9_.:-]", "_", ctx.channel or "unknown")
        sender = re.sub(
            r"[^a-zA-Z0-9_.:-]",
            "_",
            actor_principal or ctx.sender_id,
        )
        if actor_principal and sender.startswith(f"{channel}:"):
            return sender
        return f"{channel}:{sender}"

    def _auth_context_meta(
        self,
        arguments: Mapping[str, Any],
        *,
        trusted_principal: str | None,
    ) -> dict[str, Any] | None:
        secret = self._delegation_secret or self._auth_context_secret
        if not secret or trusted_principal is None:
            return None
        request = current_request_context()
        channel = request.channel if request is not None else "local"
        safe_channel = re.sub(r"[^a-zA-Z0-9_.:-]", "_", channel or "unknown")
        session_id = (
            request.session_key
            if request is not None and request.session_key
            else f"{safe_channel}:local"
        )
        if request is not None and request.conversation_principal:
            conversation_fragment = request.conversation_principal
            conversation_principal = (
                conversation_fragment
                if conversation_fragment.startswith(f"{safe_channel}:")
                else f"{safe_channel}:{conversation_fragment}"
            )
        else:
            conversation_principal = f"{safe_channel}:session:{session_id}"
        campaign_id = _first_string_field(arguments, frozenset({"campaign_id"})) or ""
        authorization_epoch = 0
        if self._session_store is not None:
            session = self._session_store.get_or_create(session_id)
            binding = binding_from_metadata(session.metadata)
            if (
                not campaign_id
                and binding is not None
                and binding.domain == self._domain_context
                and binding.principal_fingerprint == principal_fingerprint(trusted_principal)
            ):
                campaign_id = binding.campaign_id
            epochs = session.metadata.get(_MCP_AUTHORIZATION_EPOCHS_KEY)
            stored_epoch = epochs.get(self._server_name) if isinstance(epochs, Mapping) else None
            if (
                isinstance(stored_epoch, int)
                and not isinstance(stored_epoch, bool)
                and stored_epoch >= 0
            ):
                authorization_epoch = stored_epoch
            elif binding is not None:
                authorization_epoch = binding.authorization_epoch
        if self._original_name == "exposure" and arguments.get("action") == "open":
            authorization_epoch = 0
        metadata = request.metadata if request is not None else {}
        if (
            request is not None
            and request.room_turn_id
            and request.campaign_id
            and request.requester_principal
            and request.resource_owner_principal
            and request.acting_host_principal
            and request.allowed_operations
            and request.base_revision is not None
        ):
            if self._original_name not in request.allowed_operations:
                raise PermissionError(
                    f"trusted delegation does not allow MCP operation {self._original_name!r}"
                )
            expires_at = None
            if request.delegation_expires_at:
                expires_at = datetime.fromisoformat(
                    request.delegation_expires_at.replace("Z", "+00:00")
                )
            metadata = request.metadata
            delegation = sign_delegated_auth_context(
                secret=secret,
                issuer=str(metadata.get("delegation_issuer") or "sagasmith-web"),
                target_service=self._target_service,
                caller_principal=str(
                    metadata.get("caller_principal") or "workload:sagasmith-agent"
                ),
                workload_identity=str(
                    metadata.get("workload_identity") or "sagasmith-agent-hosted-worker"
                ),
                requester_principal=request.requester_principal,
                resource_owner_principal=request.resource_owner_principal,
                acting_host_principal=request.acting_host_principal,
                acting_character_id=request.acting_character_ref or "",
                authorized_audience=self._authorization_audience,
                allowed_operations=request.allowed_operations,
                conversation_principal=conversation_principal,
                tenant_id=str(metadata.get("tenant_id") or ""),
                campaign_id=request.campaign_id,
                room_turn_id=request.room_turn_id,
                base_revision=request.base_revision,
                expires_at=expires_at,
            )
            meta: dict[str, Any] = {AUTH_CONTEXT_META_KEY: delegation}
            for key in ("traceparent", "tracestate", "baggage"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip() and len(value) <= 8192:
                    meta[key] = value.strip()
            return meta
        if self._metrics_protocol == "2026-07-28":
            requester_principal = (
                request.requester_principal
                if request is not None and request.requester_principal
                else trusted_principal
            )
            resource_owner_principal = (
                request.resource_owner_principal
                if request is not None and request.resource_owner_principal
                else requester_principal
            )
            acting_host_principal = (
                request.acting_host_principal
                if request is not None and request.acting_host_principal
                else "workload:sagasmith-agent"
            )
            base_revision = (
                request.base_revision
                if request is not None and request.base_revision is not None
                else arguments.get("base_revision", arguments.get("expected_revision", 0))
            )
            if (
                isinstance(base_revision, bool)
                or not isinstance(base_revision, int)
                or base_revision < 0
            ):
                base_revision = 0
            local_campaign_id = campaign_id or f"local:{conversation_principal}"
            room_turn_id = (
                request.room_turn_id or request.turn_id or request.message_id
                if request is not None
                else ""
            ) or f"{session_id}:local"
            return {
                AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
                    secret=secret,
                    issuer="sagasmith-agent-local",
                    target_service=self._target_service,
                    caller_principal="workload:sagasmith-agent",
                    workload_identity="sagasmith-agent-local",
                    requester_principal=requester_principal,
                    resource_owner_principal=resource_owner_principal,
                    acting_host_principal=acting_host_principal,
                    authorized_audience=self._authorization_audience,
                    allowed_operations=(self._original_name,),
                    conversation_principal=conversation_principal,
                    tenant_id=str(metadata.get("tenant_id") or ""),
                    campaign_id=local_campaign_id,
                    room_turn_id=room_turn_id,
                    base_revision=base_revision,
                )
            }
        return {
            AUTH_CONTEXT_META_KEY: sign_auth_context(
                secret=self._auth_context_secret,
                host="sagasmith-agent",
                channel=safe_channel,
                actor_principal=trusted_principal,
                conversation_principal=conversation_principal,
                tenant_id=str(metadata.get("tenant_id") or ""),
                campaign_id=campaign_id,
                session_id=session_id,
                authorization_epoch=authorization_epoch,
            )
        }

    def _inject_trusted_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = current_request_context()
        if request is None:
            return arguments
        metadata = request.metadata
        values: dict[str, Any] = {
            "room_turn_id": request.room_turn_id,
            "base_revision": request.base_revision,
            "idempotency_key": metadata.get("idempotency_key"),
            "campaign_id": request.campaign_id,
            "acting_character_id": request.acting_character_ref,
        }
        return {
            **arguments,
            **{
                name: values[name]
                for name in self._trusted_arguments
                if values.get(name) is not None
            },
        }

    def _remember_authorization_epoch(self, payload: Any) -> None:
        """Retain server-owned exposure state even before a campaign is bound."""

        if self._session_store is None or not isinstance(payload, Mapping):
            return
        epoch = None
        if (binding := _host_context_binding(payload)) is not None:
            epoch = binding.get("authorization_epoch")
        if epoch is None and self._original_name == "exposure":
            current = payload.get("result", payload)
            if isinstance(current, Mapping):
                epoch = current.get("revision")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            return
        request = current_request_context()
        if request is None:
            return
        session_id = request.session_key or f"{request.channel}:{request.chat_id}"
        session = self._session_store.get_or_create(session_id)
        epochs = session.metadata.get(_MCP_AUTHORIZATION_EPOCHS_KEY)
        updated = dict(epochs) if isinstance(epochs, Mapping) else {}
        updated[self._server_name] = epoch
        session.metadata[_MCP_AUTHORIZATION_EPOCHS_KEY] = updated

    async def execute(self, **kwargs: Any) -> str:
        trusted_principal: str | None = None
        if self._inject_principal:
            trusted_principal = self._trusted_principal()
            if self._principal_argument is not None:
                kwargs = {**kwargs, self._principal_argument: trusted_principal}
        kwargs = self._inject_trusted_arguments(kwargs)
        if self._call_lock is not None:
            async with self._call_lock:
                return await self._execute_call(kwargs, trusted_principal=trusted_principal)
        return await self._execute_call(kwargs, trusted_principal=trusted_principal)

    async def _execute_call(
        self,
        kwargs: dict[str, Any],
        *,
        trusted_principal: str | None,
    ) -> str:
        retried_transient = False
        refreshed_session = False
        while True:
            try:
                # Keep MCP SDK requests in the wrapper's task. asyncio.wait_for
                # creates a child task, which can break AnyIO session ownership
                # across a tools/call -> tools/list_changed -> tools/list handoff.
                timeout_control: TaskTimeoutControl | None = None
                async with asyncio.timeout(self._tool_timeout) as call_timeout:
                    timeout_control = TaskTimeoutControl(call_timeout, self._task_timeout)
                    meta = self._auth_context_meta(
                        kwargs,
                        trusted_principal=trusted_principal,
                    )
                    request = current_request_context()
                    task_authorization = task_authorization_from_meta(
                        secret=self._delegation_secret or self._auth_context_secret,
                        meta=meta,
                        hard_expires_at=(
                            request.delegation_expires_at if request is not None else None
                        ),
                    )
                    with (
                        task_authorization_context(task_authorization),
                        task_timeout_context(timeout_control),
                    ):
                        if meta is None:
                            result = await self._session.call_tool(
                                self._original_name,
                                arguments=kwargs,
                            )
                        else:
                            result = await self._session.call_tool(
                                self._original_name,
                                arguments=kwargs,
                                meta=meta,
                            )
            except asyncio.TimeoutError:
                record_mcp_event(
                    "tool",
                    "timeout",
                    transport=self._metrics_transport,
                    protocol=self._metrics_protocol,
                )
                timeout_seconds = (
                    self._task_timeout
                    if timeout_control is not None and timeout_control.claimed
                    else self._tool_timeout
                )
                logger.warning("MCP tool '{}' timed out after {}s", self._name, timeout_seconds)
                return ToolResult.error(f"(MCP tool call timed out after {timeout_seconds}s)")
            except asyncio.CancelledError:
                # MCP SDK's anyio cancel scopes can leak CancelledError on timeout/failure.
                # Re-raise only if our task was externally cancelled (e.g. /stop).
                if task_is_cancelling():
                    raise
                record_mcp_event(
                    "tool",
                    "cancelled",
                    transport=self._metrics_transport,
                    protocol=self._metrics_protocol,
                )
                logger.warning("MCP tool '{}' was cancelled by server/SDK", self._name)
                return ToolResult.error("(MCP tool call was cancelled)")
            except Exception as exc:
                if await self._refresh_session_after_termination(
                    exc,
                    refreshed_session,
                    "tool",
                ):
                    refreshed_session = True
                    continue
                if _is_transient(exc):
                    if not retried_transient:
                        retried_transient = True
                        record_mcp_event(
                            "tool",
                            "retry",
                            transport=self._metrics_transport,
                            protocol=self._metrics_protocol,
                        )
                        logger.warning(
                            "MCP tool '{}' hit transient error ({}), retrying once...",
                            self._name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(1)  # Brief backoff before retry
                        continue
                    # Second transient failure — give up with retry-specific message
                    logger.exception(
                        "MCP tool '{}' failed after retry: {}",
                        self._name,
                        type(exc).__name__,
                    )
                    record_mcp_event(
                        "tool",
                        "error",
                        transport=self._metrics_transport,
                        protocol=self._metrics_protocol,
                    )
                    return ToolResult.error(
                        f"(MCP tool call failed after retry: {type(exc).__name__})"
                    )
                logger.exception(
                    "MCP tool '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                record_mcp_event(
                    "tool",
                    "error",
                    transport=self._metrics_transport,
                    protocol=self._metrics_protocol,
                )
                return ToolResult.error(f"(MCP tool call failed: {type(exc).__name__})")
            else:
                if self._post_call_sync is not None:
                    # A remote tools/list_changed notification can arrive after
                    # tools/call has returned. Exposure mutations are the one
                    # protocol operation for which the next model iteration must
                    # see the new native schema, so refresh them deterministically
                    # instead of racing the notification transport.
                    force_tool_refresh = self._original_name == "exposure" and kwargs.get(
                        "action"
                    ) in {"open", "set"} and not _mcp_field(
                        result, "is_error", "isError", False
                    )
                    expected_present = frozenset(
                        str(tool_id)
                        for tool_id in (kwargs.get("add_tool_ids") or [])
                        if isinstance(tool_id, str) and tool_id
                    )
                    expected_absent = frozenset(
                        str(tool_id)
                        for tool_id in (kwargs.get("remove_tool_ids") or [])
                        if isinstance(tool_id, str) and tool_id
                    )
                    await self._post_call_sync(
                        force_tool_refresh,
                        expected_present,
                        expected_absent,
                    )
                # Success — extract text and persist any image content as artifacts.
                try:
                    is_error = bool(_mcp_field(result, "is_error", "isError", False))
                    structured_content = (
                        None
                        if is_error
                        else _mcp_field(result, "structured_content", "structuredContent")
                    )
                    authoritative_payload = _context_payload_from_result(
                        result.content,
                        structured_content,
                    )
                    self._remember_authorization_epoch(authoritative_payload)
                    rendered, media_envelopes = self._render_call_result(
                        result.content,
                        kwargs,
                        structured_content=structured_content,
                        block_media_delivery=_shared_media_delivery_blocked(
                            structured_content
                        ),
                    )
                    mcp_result = _serialize_call_tool_result(result)
                    if is_error:
                        record_mcp_event(
                            "tool",
                            "error",
                            transport=self._metrics_transport,
                            protocol=self._metrics_protocol,
                        )
                        return ToolResult(rendered, is_error=True, mcp_result=mcp_result)
                    context_changed = self._persist_domain_context(
                        rendered,
                        kwargs,
                        trusted_principal=trusted_principal,
                        authoritative_payload=authoritative_payload,
                    )
                    record_mcp_event(
                        "tool",
                        "ok",
                        transport=self._metrics_transport,
                        protocol=self._metrics_protocol,
                    )
                    return ToolResult(
                        rendered,
                        context_barrier=context_changed,
                        structured_content=structured_content,
                        audit_receipt=_auth_context_receipt_from_result(result.content),
                        media_envelopes=media_envelopes,
                        mcp_result=mcp_result,
                    )
                except Exception as exc:
                    record_mcp_event(
                        "tool",
                        "error",
                        transport=self._metrics_transport,
                        protocol=self._metrics_protocol,
                    )
                    logger.exception(
                        "MCP tool '{}' failed while rendering result: {}: {}",
                        self._name,
                        type(exc).__name__,
                        exc,
                    )
                    return ToolResult.error(
                        f"(MCP tool returned malformed content: {type(exc).__name__})"
                    )

    def _persist_domain_context(
        self,
        rendered: str,
        arguments: Mapping[str, Any],
        *,
        trusted_principal: str | None,
        authoritative_payload: Any = None,
    ) -> bool:
        if self._domain_context is None or self._session_store is None:
            return False
        request = current_request_context()
        session_key = request.session_key or f"{request.channel}:{request.chat_id}"
        if session_key is None:
            return False
        try:
            payload = json.loads(rendered)
        except (TypeError, json.JSONDecodeError):
            payload = None

        session = self._session_store.get_or_create(session_key)
        previous = binding_from_metadata(session.metadata)
        trusted_fingerprint = (
            principal_fingerprint(trusted_principal)
            if trusted_principal is not None
            else (previous.principal_fingerprint if previous is not None else "")
        )
        exact = _host_context_binding(authoritative_payload)
        if exact is None:
            exact = _host_context_binding(payload)
        if exact is not None:
            binding = DomainContextBinding.from_mapping(exact)
            if binding.domain != self._domain_context:
                raise ValueError("MCP host_context_binding names another domain")
            requested_campaign = _first_string_field(arguments, frozenset({"campaign_id"}))
            if requested_campaign and binding.campaign_id != requested_campaign:
                raise ValueError("MCP host_context_binding names another campaign")
            if not trusted_fingerprint:
                raise ValueError(
                    "MCP host_context_binding has no transport-authenticated principal"
                )
            if binding.principal_fingerprint != trusted_fingerprint:
                raise ValueError("MCP host_context_binding names another principal")
        else:
            campaign_id = _first_string_field(arguments, frozenset({"campaign_id"}))
            if not campaign_id:
                return False
            raise ValueError(
                "campaign-scoped MCP result is missing the current host_context_binding"
            )
        changed = bind_session_context(session, binding)
        self._session_store.save(session)
        if changed:
            logger.info(
                "MCP domain context barrier advanced for {} ({})",
                session_key,
                binding.domain,
            )
        return changed

    def _render_call_result(
        self,
        content: Any,
        arguments: Mapping[str, Any],
        *,
        structured_content: Any = None,
        block_media_delivery: bool = False,
    ) -> tuple[str, tuple[HostMediaEnvelope, ...]]:
        """Turn MCP content blocks into a tool result string.

        Structured MCP output is authoritative when present, avoiding an invalid
        newline-joined document when a server renders a list as several text
        blocks. Image blocks are decoded and saved as
        local artifacts (mirroring the built-in image generation tool) so the
        model can deliver them via the message tool instead of trying to forward
        base64 — which would be truncated and bloat the context window.
        """
        from mcp import types

        text_parts: list[str] = []
        artifacts: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, types.TextContent):
                if structured_content is None:
                    text_parts.append(block.text)
                continue
            media_payload = _media_block_payload(block, types)
            if media_payload is not None:
                encoded, mime = media_payload
                stored = self._store_media_block(encoded, mime, arguments)
                if stored is not None:
                    artifacts.append(stored)
                else:
                    kind = "an image" if mime.startswith("image/") else "media"
                    text_parts.append(f"(MCP tool returned {kind} that could not be stored)")
                continue
            text_parts.append(str(block))

        if artifacts:
            if structured_content is not None:
                visible = _model_visible_image_payload(structured_content)
                if visible not in ({}, []):
                    text_parts.insert(0, json.dumps(visible, ensure_ascii=False, indent=2))
            return (
                _mcp_image_tool_result(
                    text_parts,
                    artifacts,
                    delivery_blocked=block_media_delivery,
                ),
                ()
                if block_media_delivery
                else tuple(
                    _host_media_envelope(artifact, structured_content)
                    for artifact in artifacts
                ),
            )
        if structured_content is not None:
            text_parts.insert(0, json.dumps(structured_content, ensure_ascii=False, indent=2))
        return "\n".join(text_parts) or "(no output)", ()

    def _store_media_block(
        self, encoded: str, mime: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Persist one standard MCP image/audio block as a Host artifact."""
        from nanobot.utils.artifacts import ArtifactError, store_mcp_media_artifact

        try:
            return store_mcp_media_artifact(
                encoded,
                mime=mime,
                save_dir="mcp",
                provider=f"mcp:{self._server_name}",
            )
        except (ArtifactError, OSError) as exc:
            logger.warning(
                "MCP tool '{}' returned media that could not be stored: {}",
                self._name,
                exc,
            )
            return None


class MCPHostPrivateToolWrapper(MCPToolWrapper):
    """Callable by Host code while absent from every model tool definition."""

    _model_visible = False

    def __init__(self, *args: Any, host_token: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._host_token = host_token

    async def execute(self, **kwargs: Any) -> str:
        return await super().execute(**kwargs, host_token=self._host_token)


class MCPResourceWrapper(_MCPWrapperBase):
    """Wraps an MCP resource URI as a read-only nanobot Tool."""

    _plugin_discoverable = False

    def __init__(self, session, server_name: str, resource_def, resource_timeout: int = 30):
        self._set_mcp_connection(session, server_name)
        self._uri = resource_def.uri
        self._name = _sanitize_mcp_tool_name(f"mcp_{server_name}_resource_{resource_def.name}")
        desc = resource_def.description or resource_def.name
        self._description = f"[MCP Resource] {desc}\nURI: {self._uri}"
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._resource_timeout = resource_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        retried_transient = False
        refreshed_session = False
        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.read_resource(self._uri),
                    timeout=self._resource_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP resource '{}' timed out after {}s", self._name, self._resource_timeout
                )
                return f"(MCP resource read timed out after {self._resource_timeout}s)"
            except asyncio.CancelledError:
                if task_is_cancelling():
                    raise
                logger.warning("MCP resource '{}' was cancelled by server/SDK", self._name)
                return "(MCP resource read was cancelled)"
            except Exception as exc:
                if await self._refresh_session_after_termination(
                    exc,
                    refreshed_session,
                    "resource",
                ):
                    refreshed_session = True
                    continue
                if _is_transient(exc):
                    if not retried_transient:
                        retried_transient = True
                        logger.warning(
                            "MCP resource '{}' hit transient error ({}), retrying once...",
                            self._name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(1)
                        continue
                    logger.exception(
                        "MCP resource '{}' failed after retry: {}",
                        self._name,
                        type(exc).__name__,
                    )
                    return f"(MCP resource read failed after retry: {type(exc).__name__})"
                logger.exception(
                    "MCP resource '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP resource read failed: {type(exc).__name__})"
            else:
                parts: list[str] = []
                for block in result.contents:
                    if isinstance(block, types.TextResourceContents):
                        parts.append(block.text)
                    elif isinstance(block, types.BlobResourceContents):
                        parts.append(f"[Binary resource: {len(block.blob)} bytes]")
                    else:
                        parts.append(str(block))
                return "\n".join(parts) or "(no output)"


class MCPPromptWrapper(_MCPWrapperBase):
    """Wraps an MCP prompt as a read-only nanobot Tool."""

    _plugin_discoverable = False

    def __init__(self, session, server_name: str, prompt_def, prompt_timeout: int = 30):
        self._set_mcp_connection(session, server_name)
        self._prompt_name = prompt_def.name
        self._name = _sanitize_mcp_tool_name(f"mcp_{server_name}_prompt_{prompt_def.name}")
        desc = prompt_def.description or prompt_def.name
        self._description = (
            f"[MCP Prompt] {desc}\n"
            "Returns a filled prompt template that can be used as a workflow guide."
        )
        self._prompt_timeout = prompt_timeout

        # Build parameters from prompt arguments
        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg in prompt_def.arguments or []:
            prop: dict[str, Any] = {"type": "string"}
            if getattr(arg, "description", None):
                prop["description"] = arg.description
            properties[arg.name] = prop
            if arg.required:
                required.append(arg.name)
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types
        try:
            from mcp.shared.exceptions import MCPError
        except ImportError:  # SDK v1 compatibility
            from mcp.shared.exceptions import McpError as MCPError

        retried_transient = False
        refreshed_session = False
        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.get_prompt(self._prompt_name, arguments=kwargs),
                    timeout=self._prompt_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP prompt '{}' timed out after {}s", self._name, self._prompt_timeout
                )
                return f"(MCP prompt call timed out after {self._prompt_timeout}s)"
            except asyncio.CancelledError:
                if task_is_cancelling():
                    raise
                logger.warning("MCP prompt '{}' was cancelled by server/SDK", self._name)
                return "(MCP prompt call was cancelled)"
            except MCPError as exc:
                if await self._refresh_session_after_termination(
                    exc,
                    refreshed_session,
                    "prompt",
                ):
                    refreshed_session = True
                    continue
                code, message = _mcp_error_details(exc)
                logger.exception(
                    "MCP prompt '{}' failed: code={} message={}",
                    self._name,
                    code,
                    message,
                )
                return f"(MCP prompt call failed: {message} [code {code}])"
            except Exception as exc:
                if await self._refresh_session_after_termination(
                    exc,
                    refreshed_session,
                    "prompt",
                ):
                    refreshed_session = True
                    continue
                if _is_transient(exc):
                    if not retried_transient:
                        retried_transient = True
                        logger.warning(
                            "MCP prompt '{}' hit transient error ({}), retrying once...",
                            self._name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(1)
                        continue
                    logger.exception(
                        "MCP prompt '{}' failed after retry: {}",
                        self._name,
                        type(exc).__name__,
                    )
                    return f"(MCP prompt call failed after retry: {type(exc).__name__})"
                logger.exception(
                    "MCP prompt '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP prompt call failed: {type(exc).__name__})"
            else:
                parts: list[str] = []
                for message in result.messages:
                    content = message.content
                    if isinstance(content, types.TextContent):
                        parts.append(content.text)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, types.TextContent):
                                parts.append(block.text)
                            else:
                                parts.append(str(block))
                    else:
                        parts.append(str(content))
                return "\n".join(parts) or "(no output)"


async def connect_mcp_servers(
    mcp_servers: dict,
    registry: ToolRegistry,
    *,
    session_store: Any | None = None,
) -> dict[str, MCPConnection]:
    """Connect to configured MCP servers and register their tools, resources, prompts.

    Returns one connection handle per server.  Each handle keeps the task that
    entered the MCP SDK contexts alive so reconnect and shutdown can close
    AnyIO cancel scopes from their owning task.
    """
    mcp_module = importlib.import_module("mcp")
    types = mcp_module.types
    stdio_parameters_cls = mcp_module.StdioServerParameters
    mcp_client_cls = getattr(mcp_module, "Client", None)
    legacy_client_session_cls = getattr(mcp_module, "ClientSession", None)
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    async def open_single_server(name: str, cfg) -> tuple[str, AsyncExitStack | None]:
        server_stack = AsyncExitStack()
        await server_stack.__aenter__()
        session: Any | None = None
        refresh_task: asyncio.Task[None] | None = None
        refresh_lock = asyncio.Lock()
        call_lock = asyncio.Lock()
        refresh_generation = 0
        synced_generation = 0
        legacy_catalog = True
        metrics_transport = "unknown"
        metrics_protocol = "unknown"

        try:
            transport_type = cfg.type
            if not transport_type:
                if cfg.command:
                    transport_type = "stdio"
                elif cfg.url:
                    transport_type = (
                        "sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp"
                    )
                else:
                    logger.warning("MCP server '{}': no command or url configured, skipping", name)
                    await server_stack.aclose()
                    return name, None
            metrics_transport = (
                "http" if transport_type == "streamableHttp" else str(transport_type)
            )
            if metrics_transport not in {"stdio", "sse", "http"}:
                metrics_transport = "unknown"

            host_token = str((cfg.env or {}).get("SAGASMITH_NPC_HOST_TOKEN") or "").strip()
            sagasmith_stdio = (
                "sagasmith" in " ".join((name, cfg.command, *(cfg.args or []))).casefold()
            )
            if transport_type == "stdio" and sagasmith_stdio and not host_token:
                host_token = secrets.token_urlsafe(32)

            if transport_type in {"sse", "streamableHttp"}:
                ok, error = validate_url_target(cfg.url)
                if not ok:
                    logger.warning(
                        "MCP server '{}': blocked unsafe URL {} ({})",
                        name,
                        _redact_url(cfg.url),
                        error,
                    )
                    await server_stack.aclose()
                    return name, None

            if transport_type == "stdio":
                child_env = dict(cfg.env or {})
                if host_token:
                    child_env["SAGASMITH_NPC_HOST_TOKEN"] = host_token
                command, args, env = _normalize_windows_stdio_command(
                    cfg.command,
                    cfg.args,
                    child_env or None,
                )
                params = stdio_parameters_cls(
                    command=command,
                    args=args,
                    env=env,
                    cwd=cfg.cwd or None,
                )
                client_transport: Any = (
                    params if mcp_client_cls is not None else stdio_client(params)
                )
            elif transport_type == "sse":
                if not await _probe_http_url(cfg.url):
                    logger.warning(
                        "MCP server '{}': {} unreachable, skipping", name, _redact_url(cfg.url)
                    )
                    await server_stack.aclose()
                    return name, None

                def httpx_client_factory(
                    headers: dict[str, str] | None = None,
                    timeout: httpx.Timeout | None = None,
                    auth: httpx.Auth | None = None,
                ) -> httpx.AsyncClient:
                    merged_headers = {
                        "Accept": "application/json, text/event-stream",
                        **(cfg.headers or {}),
                        **(headers or {}),
                    }
                    return httpx.AsyncClient(
                        headers=merged_headers or None,
                        event_hooks={
                            "request": [_validate_mcp_request_url],
                            "response": [_validate_mcp_redirect_response],
                        },
                        follow_redirects=True,
                        timeout=timeout,
                        auth=auth,
                        **_pinned_transport_kwargs(),
                    )

                if cfg.protocol_mode != "legacy":
                    logger.warning(
                        "MCP server '{}': SSE is legacy-only; forcing initialize compatibility",
                        name,
                    )
                client_transport = sse_client(
                    cfg.url,
                    httpx_client_factory=httpx_client_factory,
                )
            elif transport_type == "streamableHttp":
                if not await _probe_http_url(cfg.url):
                    logger.warning(
                        "MCP server '{}': {} unreachable, skipping", name, _redact_url(cfg.url)
                    )
                    await server_stack.aclose()
                    return name, None

                if mcp_client_cls is None:
                    http_client = await server_stack.enter_async_context(
                        httpx.AsyncClient(
                            headers=cfg.headers or None,
                            event_hooks={
                                "request": [_validate_mcp_request_url],
                                "response": [_validate_mcp_redirect_response],
                            },
                            follow_redirects=True,
                            timeout=httpx.Timeout(30.0, connect=10.0),
                            **_pinned_transport_kwargs(),
                        )
                    )
                else:
                    http_client = await server_stack.enter_async_context(
                        httpx2.AsyncClient(
                            headers=cfg.headers or None,
                            event_hooks={
                                "request": [_validate_mcp2_request_url],
                                "response": [_reject_mcp2_redirect_response],
                            },
                            follow_redirects=False,
                            timeout=httpx2.Timeout(30.0, connect=10.0),
                        )
                    )
                client_transport = streamable_http_client(cfg.url, http_client=http_client)
            else:
                logger.warning("MCP server '{}': unknown transport type '{}'", name, transport_type)
                await server_stack.aclose()
                return name, None

            async def wait_for_pending_tool_refresh(
                force: bool = False,
                expected_present: frozenset[str] = frozenset(),
                expected_absent: frozenset[str] = frozenset(),
            ) -> None:
                nonlocal synced_generation
                # The receive loop dispatches list_changed independently of the
                # tool-call task. Run tools/list in its own task after tools/call
                # has fully unwound; MCP SDK transports may reject a request made
                # inline from the outer task during that response handoff.
                await asyncio.sleep(0)
                if force:
                    # Streamable HTTP can make the changed list observable only
                    # after tools/call has returned. Reconcile until the exact
                    # exposure delta requested by the model is visible, with a
                    # short bound so a broken server cannot stall the turn.
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + _MCP_EXPOSURE_REFRESH_TIMEOUT_SECONDS
                    delay = 0.01
                    available: set[str] = set()
                    while True:
                        target_generation = refresh_generation
                        task = asyncio.create_task(
                            sync_tools(),
                            name=f"mcp-tools-post-exposure-refresh:{name}",
                        )
                        available = set(await asyncio.shield(task))
                        synced_generation = max(synced_generation, target_generation)
                        converged = expected_present <= available and not (
                            expected_absent & available
                        )
                        if converged:
                            break
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            logger.warning(
                                "MCP server '{}': exposure tool list did not converge; "
                                "missing={}, still_present={}",
                                name,
                                ", ".join(sorted(expected_present - available)) or "(none)",
                                ", ".join(sorted(expected_absent & available)) or "(none)",
                            )
                            break
                        await asyncio.sleep(min(delay, remaining))
                        delay = min(delay * 2, 0.1)
                    logger.info(
                        "MCP server '{}': refreshed tools after exposure mutation",
                        name,
                    )
                    return
                if synced_generation >= refresh_generation:
                    return
                task = asyncio.create_task(
                    sync_pending_tool_refresh(),
                    name=f"mcp-tools-post-call-refresh:{name}",
                )
                await asyncio.shield(task)
                logger.info("MCP server '{}': refreshed tools after list_changed", name)

            async def sync_tools(*, warn_unmatched: bool = False) -> list[str]:
                if session is None:
                    return []
                async with refresh_lock:
                    listed = await session.list_tools()
                    record_mcp_event(
                        "catalog",
                        "ok",
                        transport=metrics_transport,
                        protocol=metrics_protocol,
                    )
                    enabled_tools = set(cfg.enabled_tools)
                    allow_all_tools = "*" in enabled_tools
                    desired: dict[str, Any] = {}
                    matched_enabled_tools: set[str] = set()
                    tool_definitions = sorted(listed.tools, key=lambda item: item.name)
                    available_raw_names = [tool_def.name for tool_def in tool_definitions]
                    available_wrapped_names = [
                        _sanitize_mcp_tool_name(f"mcp_{name}_{tool_def.name}")
                        for tool_def in tool_definitions
                    ]
                    for tool_def in tool_definitions:
                        wrapped_name = _sanitize_mcp_tool_name(f"mcp_{name}_{tool_def.name}")
                        if (
                            not allow_all_tools
                            and tool_def.name not in enabled_tools
                            and wrapped_name not in enabled_tools
                        ):
                            continue
                        desired[wrapped_name] = tool_def
                        if tool_def.name in enabled_tools:
                            matched_enabled_tools.add(tool_def.name)
                        if wrapped_name in enabled_tools:
                            matched_enabled_tools.add(wrapped_name)

                    for registered_name in list(registry.tool_names):
                        existing = registry.get(registered_name)
                        if (
                            type(existing) is MCPToolWrapper
                            and getattr(existing, "_server_name", None) == name
                            and registered_name not in desired
                        ):
                            registry.unregister(registered_name)

                    reconnect = next(
                        (
                            getattr(registry.get(registered_name), "_reconnect", None)
                            for registered_name in registry.tool_names
                            if getattr(registry.get(registered_name), "_server_name", None) == name
                            and getattr(registry.get(registered_name), "_reconnect", None)
                            is not None
                        ),
                        None,
                    )
                    for wrapped_name, tool_def in desired.items():
                        wrapper = MCPToolWrapper(
                            session,
                            name,
                            tool_def,
                            tool_timeout=cfg.tool_timeout,
                            task_timeout=getattr(cfg, "task_timeout", 900),
                            inject_principal=cfg.inject_principal,
                            auth_context_secret=cfg.auth_context_secret,
                            delegation_secret=cfg.delegation_secret,
                            authorization_audience=cfg.authorization_audience,
                            target_service=cfg.target_service,
                            session_store=session_store,
                            post_call_sync=(
                                wait_for_pending_tool_refresh if legacy_catalog else None
                            ),
                            call_lock=call_lock,
                            transport=metrics_transport,
                            protocol=metrics_protocol,
                        )
                        if reconnect is not None:
                            wrapper.set_reconnect_handler(reconnect)
                        registry.register(wrapper)
                        logger.debug(
                            "MCP: registered tool '{}' from server '{}'", wrapped_name, name
                        )

                    if warn_unmatched and enabled_tools and not allow_all_tools:
                        unmatched = sorted(enabled_tools - matched_enabled_tools)
                        if unmatched:
                            logger.warning(
                                "MCP server '{}': enabledTools entries not found: {}. "
                                "Available raw names: {}. Available wrapped names: {}",
                                name,
                                ", ".join(unmatched),
                                ", ".join(available_raw_names) or "(none)",
                                ", ".join(available_wrapped_names) or "(none)",
                            )
                    return available_raw_names

            async def sync_pending_tool_refresh() -> None:
                nonlocal synced_generation
                while synced_generation < refresh_generation:
                    target_generation = refresh_generation
                    await sync_tools()
                    synced_generation = target_generation

            async def refresh_after_notification() -> None:
                try:
                    async with call_lock:
                        await sync_pending_tool_refresh()
                    logger.info("MCP server '{}': refreshed tools after list_changed", name)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("MCP server '{}': failed to refresh changed tool list", name)

            async def handle_server_message(message: Any) -> None:
                nonlocal refresh_generation, refresh_task
                if not legacy_catalog:
                    return
                payload = _mcp_jsonrpc_payload(message)
                if _payload_value(payload, "method") != "notifications/tools/list_changed":
                    return
                refresh_generation += 1
                # A model call holds this barrier through its post-call refresh.
                # Let that path create and await the tools/list task; scheduling
                # another refresher here would duplicate the request.
                if call_lock.locked():
                    return
                if refresh_task is None or refresh_task.done():
                    refresh_task = asyncio.create_task(
                        refresh_after_notification(),
                        name=f"mcp-tools-refresh:{name}",
                    )

            if mcp_client_cls is None:
                streams = await server_stack.enter_async_context(client_transport)
                read, write = streams[0], streams[1]
                read = _filter_malformed_mcp_progress_notifications(read, name)
                assert legacy_client_session_cls is not None
                session = await server_stack.enter_async_context(
                    legacy_client_session_cls(
                        read,
                        write,
                        message_handler=handle_server_message,
                    )
                )
                await session.initialize()
                legacy_catalog = True
                metrics_protocol = "legacy"
            else:
                configured_mode = "legacy" if transport_type == "sse" else cfg.protocol_mode
                # SDK pinned-modern mode synthesizes a discover result and therefore
                # cannot observe real server extension capabilities. Probe the peer,
                # then enforce the configured version instead of trusting synthetic
                # capabilities or silently accepting a legacy fallback.
                mode = "auto" if configured_mode == "2026-07-28" else configured_mode
                session = await server_stack.enter_async_context(
                    mcp_client_cls(
                        client_transport,
                        mode=mode,
                        message_handler=handle_server_message,
                        extensions=(
                            [TasksExtension()]
                            if cfg.delegation_secret or cfg.auth_context_secret
                            else None
                        ),
                    )
                )
                if (
                    configured_mode == "2026-07-28"
                    and str(session.protocol_version) != "2026-07-28"
                ):
                    raise RuntimeError(
                        f"MCP server {name!r} does not support required protocol 2026-07-28"
                    )
                legacy_catalog = str(session.protocol_version) != "2026-07-28"
                metrics_protocol = "legacy" if legacy_catalog else "2026-07-28"
            record_mcp_event(
                "discover",
                "ok",
                transport=metrics_transport,
                protocol=metrics_protocol,
            )

            available_raw_names = await sync_tools(warn_unmatched=True)
            enabled_tools = set(cfg.enabled_tools)
            allow_all_tools = "*" in enabled_tools
            registered_count = len(
                [
                    tool_name
                    for tool_name in registry.tool_names
                    if getattr(registry.get(tool_name), "_server_name", None) == name
                ]
            )

            # SagaSmith conversation v3 keeps activation transport out of tools/list. The
            # trusted Host synthesizes one hidden wrapper only after verifying
            # the server-advertised conversation capability.
            if host_token and "server_capabilities" in available_raw_names:
                try:
                    capability_result = await session.call_tool("server_capabilities", arguments={})
                    capability_text = next(
                        (
                            block.text
                            for block in capability_result.content
                            if isinstance(block, types.TextContent)
                        ),
                        "{}",
                    )
                    capability_data = json.loads(capability_text)
                    capability_payload = capability_data.get("result", capability_data)
                    npc_capability = dict(capability_payload.get("npc_conversations") or {})
                except Exception:
                    npc_capability = {}
                if npc_capability.get("host_transport") == "private_authenticated_unlisted":
                    private_def = types.Tool(
                        name="npc_conversation_transport",
                        description="Host-private SagaSmith NPC activation transport.",
                        inputSchema={
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "campaign_id",
                                "conversation_id",
                                "action",
                                "payload",
                                "host_token",
                            ],
                            "properties": {
                                "campaign_id": {"type": "string"},
                                "conversation_id": {"type": "string"},
                                "action": {
                                    "type": "string",
                                    "enum": [
                                        "claim_activation",
                                        "submit_proposal",
                                        "cancel_activation",
                                    ],
                                },
                                "payload": {"type": "object"},
                                "host_token": {"type": "string"},
                                "principal_id": {"type": "string", "default": "system:local"},
                            },
                        },
                    )
                    private_wrapper = MCPHostPrivateToolWrapper(
                        session,
                        name,
                        private_def,
                        tool_timeout=cfg.tool_timeout,
                        inject_principal=cfg.inject_principal,
                        auth_context_secret=cfg.auth_context_secret,
                        delegation_secret=cfg.delegation_secret,
                        authorization_audience=cfg.authorization_audience,
                        target_service=cfg.target_service,
                        session_store=session_store,
                        host_token=host_token,
                    )
                    registry.register(private_wrapper)
                    registered_count += 1

            # Only register resources and prompts when no tool restriction is
            # active.  enabledTools is a per-*tool* allowlist; resources and
            # prompts have no equivalent name filter, so they must be skipped
            # whenever the operator specified a tool subset.  An empty list
            # (deny-all) or a list of specific tool names both indicate that
            # the operator intended to restrict capabilities — registering
            # unrestricted resource/prompt wrappers would violate that intent.
            # The default ["*"] (allow-all) means no restriction was intended.
            register_extras = allow_all_tools and cfg.expose_resources_and_prompts
            if register_extras:
                try:
                    resources_result = await session.list_resources()
                    for resource in resources_result.resources:
                        wrapper = MCPResourceWrapper(
                            session, name, resource, resource_timeout=cfg.tool_timeout
                        )
                        registry.register(wrapper)
                        registered_count += 1
                        logger.debug(
                            "MCP: registered resource '{}' from server '{}'",
                            wrapper.name,
                            name,
                        )
                except Exception as e:
                    logger.debug("MCP server '{}': resources not supported or failed: {}", name, e)

                try:
                    prompts_result = await session.list_prompts()
                    for prompt in prompts_result.prompts:
                        wrapper = MCPPromptWrapper(
                            session, name, prompt, prompt_timeout=cfg.tool_timeout
                        )
                        registry.register(wrapper)
                        registered_count += 1
                        logger.debug(
                            "MCP: registered prompt '{}' from server '{}'",
                            wrapper.name,
                            name,
                        )
                except Exception as e:
                    logger.debug("MCP server '{}': prompts not supported or failed: {}", name, e)
            else:
                logger.info(
                    "MCP server '{}': skipping resource/prompt registration "
                    "(tool restriction or exposeResourcesAndPrompts=false)",
                    name,
                )

            logger.info(
                "MCP server '{}': connected, {} capabilities registered", name, registered_count
            )
            record_mcp_event(
                "connect",
                "ok",
                transport=metrics_transport,
                protocol=metrics_protocol,
            )
            return name, server_stack

        except Exception as e:
            record_mcp_event(
                "connect",
                "error",
                transport=metrics_transport,
                protocol=metrics_protocol,
            )
            hint = ""
            text = str(e).lower()
            if any(
                marker in text
                for marker in (
                    "parse error",
                    "invalid json",
                    "unexpected token",
                    "jsonrpc",
                    "content-length",
                )
            ):
                hint = (
                    " Hint: this looks like stdio protocol pollution. Make sure the MCP server writes "
                    "only JSON-RPC to stdout and sends logs/debug output to stderr instead."
                )
            logger.exception("MCP server '{}': failed to connect: {}", name, hint)
            with suppress(Exception):
                await server_stack.aclose()
            return name, None

    async def connect_single_server(name: str, cfg) -> tuple[str, MCPConnection | None]:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[bool] = loop.create_future()
        close_requested = asyncio.Event()

        async def own_connection() -> None:
            stack: AsyncExitStack | None = None
            try:
                _, stack = await open_single_server(name, cfg)
                if not ready.done():
                    ready.set_result(stack is not None)
                if stack is not None:
                    await close_requested.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                raise
            finally:
                if stack is not None:
                    await stack.aclose()

        owner = asyncio.create_task(own_connection(), name=f"mcp:{name}")
        connection = _OwnedMCPConnection(owner, close_requested)
        try:
            connected = await ready
        except BaseException:
            close_requested.set()
            owner.cancel()
            with suppress(BaseException):
                await asyncio.shield(owner)
            raise
        if not connected:
            await connection.aclose()
            return name, None
        return name, connection

    server_stacks: dict[str, MCPConnection] = {}
    connect_limit = asyncio.Semaphore(4)

    async def bounded_connect(name: str, cfg: Any) -> tuple[str, MCPConnection | None]:
        async with connect_limit:
            return await connect_single_server(name, cfg)

    results = await asyncio.gather(
        *(bounded_connect(name, cfg) for name, cfg in mcp_servers.items()),
        return_exceptions=True,
    )
    for (name, _cfg), result in zip(mcp_servers.items(), results, strict=True):
        try:
            if isinstance(result, BaseException):
                raise result
            connected_name, connection = result
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.exception("MCP server '{}' connection failed: {}", name, exc)
            continue
        if connection is not None:
            server_stacks[connected_name] = connection

    return server_stacks


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return persisted session kwargs for MCP preset attachments."""
    mcp_presets = metadata.get("mcp_presets") if isinstance(metadata, Mapping) else None
    return {"mcp_presets": mcp_presets} if isinstance(mcp_presets, list) and mcp_presets else {}


async def connect_missing_servers(state: Any, registry: ToolRegistry) -> None:
    """Connect configured MCP servers that are not currently live."""
    async with _reload_lock(state):
        if getattr(state, "_mcp_closing", False):
            return
        missing_servers = {
            name: cfg for name, cfg in state._mcp_servers.items() if name not in state._mcp_stacks
        }
        if state._mcp_connecting or not missing_servers:
            return
        state._mcp_connecting = True
        try:
            connected = await connect_mcp_servers(
                missing_servers,
                registry,
                session_store=getattr(state, "sessions", None),
            )
            if getattr(state, "_mcp_closing", False):
                for connection in connected.values():
                    await connection.aclose()
                return
            state._mcp_stacks.update(connected)
            _attach_reconnect_handlers(state, registry, connected)
            if connected:
                logger.info("MCP connected servers: {}", sorted(connected))
            else:
                logger.warning("No MCP servers connected successfully (will retry next message)")
        except asyncio.CancelledError:
            if task_is_cancelling():
                raise
            logger.warning("MCP connection cancelled (will retry next message)")
        except BaseException as e:
            logger.warning("Failed to connect MCP servers (will retry next message): {}", e)
        finally:
            state._mcp_connecting = False


async def reload_servers(state: Any, registry: ToolRegistry) -> dict[str, Any]:
    """Reconcile live MCP connections with the current config file."""
    async with _reload_lock(state):
        if getattr(state, "_mcp_closing", False):
            return {
                "ok": False,
                "message": "MCP connections are shutting down.",
                "requires_restart": True,
            }
        try:
            from nanobot.config.loader import load_config, resolve_config_env_vars

            config = resolve_config_env_vars(load_config())
            configured_servers = dict(config.tools.mcp_servers)
            next_servers = {
                name: server
                for name, server in configured_servers.items()
                if not getattr(server, "session_scoped", False)
            }
            next_session_servers = {
                name: server
                for name, server in configured_servers.items()
                if getattr(server, "session_scoped", False)
            }
        except Exception as exc:
            logger.warning("MCP hot reload could not read config: {}", exc)
            return {
                "ok": False,
                "message": "Could not reload MCP config. Restart nanobot to pick up changes.",
                "requires_restart": True,
                "error": str(exc),
            }

        current_session_servers = dict(getattr(state, "_session_mcp_servers", {}))
        session_changed = {
            name: _server_signature(server)
            for name, server in current_session_servers.items()
        } != {
            name: _server_signature(server)
            for name, server in next_session_servers.items()
        }
        if session_changed:
            state._session_mcp_servers = next_session_servers

        current_servers = dict(state._mcp_servers)
        current_names = set(current_servers)
        next_names = set(next_servers)
        removed = sorted(current_names - next_names)
        added = sorted(next_names - current_names)
        changed = sorted(
            name
            for name in current_names & next_names
            if _server_signature(current_servers[name]) != _server_signature(next_servers[name])
        )

        tools_removed = 0
        for name in [*removed, *changed]:
            tools_removed += _unregister_server_tools(state, registry, name)
            await _close_server(state, name)

        state._mcp_servers = next_servers
        retry_missing = sorted(
            name
            for name in next_names
            if name not in state._mcp_stacks and name not in set(added) | set(changed)
        )
        to_connect_names = sorted(set(added) | set(changed) | set(retry_missing))
        to_connect = {name: next_servers[name] for name in to_connect_names}
        connected: dict[str, MCPConnection] = {}
        if to_connect:
            connected = await connect_mcp_servers(
                to_connect,
                registry,
                session_store=getattr(state, "sessions", None),
            )
            if getattr(state, "_mcp_closing", False):
                for connection in connected.values():
                    await connection.aclose()
                return {
                    "ok": False,
                    "message": "MCP connections are shutting down.",
                    "requires_restart": True,
                }
            state._mcp_stacks.update(connected)
            _attach_reconnect_handlers(state, registry, connected)

        # A session registry is a point-in-time clone of the global registry.
        # Recreate it after either static or session-scoped MCP configuration
        # changes so existing conversations never keep stale wrappers/schemas.
        if session_changed or removed or added or changed or connected:
            for connections in getattr(state, "_session_mcp_stacks", {}).values():
                for connection in connections.values():
                    await connection.aclose()
            getattr(state, "_session_mcp_stacks", {}).clear()
            getattr(state, "_session_mcp_tools", {}).clear()
            getattr(state, "_session_mcp_locks", {}).clear()

        failed = sorted(set(to_connect) - set(connected))
        unchanged = not removed and not added and not changed and not retry_missing and not session_changed
        ok = not failed
        if failed:
            message = "MCP config reloaded, but some servers did not connect: " + ", ".join(failed)
        elif unchanged:
            message = "MCP config is already live."
        elif retry_missing and not added and not changed and not removed:
            message = "MCP connections refreshed without restarting nanobot."
        else:
            message = "MCP config reloaded without restarting nanobot."

        logger.info(
            "MCP hot reload: added={} changed={} removed={} retried={} connected={} failed={} tools_removed={}",
            added,
            changed,
            removed,
            retry_missing,
            sorted(connected),
            failed,
            tools_removed,
        )
        return {
            "ok": ok,
            "message": message,
            "added": added,
            "changed": changed,
            "removed": removed,
            "retried": retry_missing,
            "connected": sorted(state._mcp_stacks),
            "configured": sorted(state._mcp_servers),
            "session_scoped": sorted(getattr(state, "_session_mcp_servers", {})),
            "failed": failed,
            "tools_removed": tools_removed,
            "requires_restart": False,
        }


async def request_mcp_reload(bus: Any, *, timeout: float = 15.0) -> dict[str, Any]:
    """Ask the running agent loop to reconcile live MCP connections."""
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-settings",
            chat_id="runtime",
            content=RUNTIME_CONTROL_MCP_RELOAD,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MCP_RELOAD,
                RUNTIME_CONTROL_ACK: ack,
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "message": "MCP hot reload timed out. Restart nanobot to pick up changes.",
            "requires_restart": True,
        }
    return (
        result
        if isinstance(result, dict)
        else {
            "ok": False,
            "message": "MCP hot reload returned an unexpected response.",
            "requires_restart": True,
        }
    )


async def handle_runtime_control(state: Any, msg: InboundMessage, registry: ToolRegistry) -> bool:
    metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
    control = metadata.get(INBOUND_META_RUNTIME_CONTROL)
    if control != RUNTIME_CONTROL_MCP_RELOAD:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)
    try:
        result = await reload_servers(state, registry)
    except Exception as exc:
        logger.exception("MCP hot reload failed")
        result = {
            "ok": False,
            "message": "MCP hot reload failed. Restart nanobot to pick up changes.",
            "requires_restart": True,
            "error": str(exc),
        }
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(result)
    return True


def _reload_lock(state: Any) -> asyncio.Lock:
    try:
        return _RELOAD_LOCKS[state]
    except KeyError:
        lock = asyncio.Lock()
        _RELOAD_LOCKS[state] = lock
        return lock


def _attach_reconnect_handlers(
    state: Any,
    registry: ToolRegistry,
    server_names: Mapping[str, Any] | set[str] | list[str] | tuple[str, ...],
) -> None:
    async def reconnect(server_name: str, tool_name: str, stale_tool: Tool) -> Tool | None:
        return await _refresh_terminated_server(
            state,
            registry,
            server_name,
            tool_name,
            stale_tool,
        )

    for server_name in server_names:
        for tool_name in list(registry.tool_names):
            tool = registry.get(tool_name)
            if not _tool_belongs_to_server(tool, tool_name, server_name):
                continue
            if isinstance(tool, _MCPWrapperBase):
                tool.set_reconnect_handler(reconnect)


async def _refresh_terminated_server(
    state: Any,
    registry: ToolRegistry,
    server_name: str,
    tool_name: str,
    stale_tool: Tool,
) -> Tool | None:
    async with _reload_lock(state):
        if getattr(state, "_mcp_closing", False):
            return None
        cfg = state._mcp_servers.get(server_name)
        if cfg is None:
            logger.warning(
                "MCP server '{}' session terminated but is no longer configured",
                server_name,
            )
            return None

        current_tool = registry.get(tool_name)
        if (
            current_tool is not None
            and current_tool is not stale_tool
            and server_name in state._mcp_stacks
        ):
            return current_tool

        logger.warning("MCP server '{}' session terminated; refreshing connection", server_name)
        _unregister_server_tools(state, registry, server_name)
        await _close_server(state, server_name)

        connected = await connect_mcp_servers(
            {server_name: cfg},
            registry,
            session_store=getattr(state, "sessions", None),
        )
        if getattr(state, "_mcp_closing", False):
            for connection in connected.values():
                await connection.aclose()
            return None
        state._mcp_stacks.update(connected)
        _attach_reconnect_handlers(state, registry, connected)
        if server_name not in connected:
            logger.warning(
                "MCP server '{}' reconnect failed after session termination", server_name
            )
            return None
        return registry.get(tool_name)


def _server_signature(cfg: Any) -> Any:
    if hasattr(cfg, "model_dump"):
        return cfg.model_dump(mode="json")
    return cfg


def _tool_prefix(server_name: str) -> str:
    return _sanitize_name(f"mcp_{server_name}_")


def _tool_belongs_to_server(tool: Tool | None, tool_name: str, server_name: str) -> bool:
    if isinstance(tool, _MCPWrapperBase):
        return getattr(tool, "_server_name", None) == server_name
    return tool_name.startswith(_tool_prefix(server_name))


def _unregister_server_tools(state: Any, registry: ToolRegistry, server_name: str) -> int:
    removed = 0
    for tool_name in list(registry.tool_names):
        tool = registry.get(tool_name)
        if _tool_belongs_to_server(tool, tool_name, server_name):
            registry.unregister(tool_name)
            removed += 1
    return removed


async def _close_server(state: Any, server_name: str) -> None:
    stack = state._mcp_stacks.pop(server_name, None)
    if stack is None:
        return
    try:
        await stack.aclose()
    except asyncio.CancelledError:
        if task_is_cancelling():
            raise
        logger.debug("MCP server '{}' cleanup error (can be ignored)", server_name)
    except (RuntimeError, BaseExceptionGroup):
        logger.debug("MCP server '{}' cleanup error (can be ignored)", server_name)


async def close_mcp_servers(state: Any) -> None:
    """Close every MCP connection while excluding reconnect and hot reload."""
    state._mcp_closing = True
    async with _reload_lock(state):
        connections = list(state._mcp_stacks.items())
        state._mcp_stacks.clear()
        for name, connection in connections:
            try:
                await connection.aclose()
            except asyncio.CancelledError:
                if task_is_cancelling():
                    raise
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
