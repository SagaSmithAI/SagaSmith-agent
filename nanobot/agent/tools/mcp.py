"""MCP client: connects to MCP servers and wraps their tools as native nanobot tools."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from typing import Any, Mapping, Protocol
from weakref import WeakKeyDictionary

import httpx
from loguru import logger

from nanobot.agent.domain_context import (
    DomainContextBinding,
    bind_session_context,
    binding_from_metadata,
    principal_fingerprint,
)
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import RequestContext, current_request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MCP_RELOAD,
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
_MCP_CONTEXT_TOOL_LIMIT = 48
_SESSION_MCP_TOOL_PROFILES: dict[tuple[str, str], str] = {}


def _first_string_field(value: Any, names: frozenset[str]) -> str | None:
    """Read identity fields only from protocol-owned envelope containers."""

    if isinstance(value, Mapping):
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for key in ("authority", "branch", "payload", "result"):
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


def _profile_session_key(ctx: RequestContext | None) -> str | None:
    if ctx is None:
        return None
    return ctx.session_key or f"{ctx.channel}:{ctx.chat_id}"


def _profile_from_rendered_result(rendered: str) -> str | None:
    try:
        payload = json.loads(rendered)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    if isinstance(payload.get("result"), dict):
        candidates.append(payload["result"])
    for candidate in candidates:
        profile = candidate.get("tool_profile")
        if isinstance(profile, str) and profile.strip():
            return profile.strip().lower()
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
        mime = getattr(block, "mimeType", None) or "image/png"
        return f"data:{mime};base64,{block.data}"

    embedded_cls = getattr(types, "EmbeddedResource", None)
    blob_cls = getattr(types, "BlobResourceContents", None)
    if embedded_cls is not None and isinstance(block, embedded_cls):
        resource = getattr(block, "resource", None)
        if blob_cls is not None and isinstance(resource, blob_cls):
            mime = getattr(resource, "mimeType", None) or ""
            if isinstance(mime, str) and mime.startswith("image/"):
                return f"data:{mime};base64,{resource.blob}"
    return None


def _mcp_image_tool_result(text_parts: list[str], artifacts: list[dict[str, Any]]) -> str:
    """Build the compact tool result for an MCP call that returned image(s).

    The base64 stays out of the model context entirely — only artifact paths and
    metadata are returned, so the result is small and the channel can deliver the
    saved file via the message tool.
    """
    payload: dict[str, Any] = {
        "artifacts": artifacts,
        "next_step": (
            "These images were returned by an MCP tool and saved as local artifacts. "
            "Call the message tool with the artifact 'path' values in the media "
            "parameter to deliver the images to the user. Do not paste base64 or raw "
            "paths into your reply unless the user asks for debug details."
        ),
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
        *,
        inject_principal: bool = False,
        default_tool_profile: str | None = None,
        session_store: Any | None = None,
    ):
        self._set_mcp_connection(session, server_name)
        self._original_name = tool_def.name
        self._name = _sanitize_mcp_tool_name(f"mcp_{server_name}_{tool_def.name}")
        self._description = tool_def.description or tool_def.name
        raw_schema = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._parameters = _normalize_schema_for_openai(raw_schema)
        self._tool_timeout = tool_timeout
        self._inject_principal = inject_principal
        self._session_store = session_store
        meta = getattr(tool_def, "meta", None)
        if not isinstance(meta, dict):
            meta = {}
        profiles = meta.get("sagasmith_tool_profiles") or []
        self._tool_profiles = frozenset(
            str(profile).strip().lower() for profile in profiles if str(profile).strip()
        )
        self._default_tool_profile = (
            str(default_tool_profile).strip().lower() if default_tool_profile else None
        )
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

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    def is_available(self, ctx: RequestContext | None) -> bool:
        """Apply MCP-provided phase metadata independently for each chat session."""
        if not self._tool_profiles or not self._default_tool_profile:
            return True
        session_key = _profile_session_key(ctx)
        profile = (
            _SESSION_MCP_TOOL_PROFILES.get((session_key, self._server_name))
            if session_key is not None
            else None
        )
        return (profile or self._default_tool_profile) in self._tool_profiles

    def _trusted_principal(self) -> str:
        # Wrappers are shared across sessions, so identity must remain task-local.
        ctx = current_request_context()
        if ctx is None or not ctx.sender_id:
            # Local cron/CLI calls have no user identity. They remain explicitly
            # distinguishable from an inbound platform user and cannot be spoofed
            # by a model parameter. The capability server owns the local identity;
            # the generic Agent adapter must not invent its own principal name.
            if self._local_principal_default is None:
                raise RuntimeError("MCP tool does not advertise a local principal default")
            return self._local_principal_default
        channel = re.sub(r"[^a-zA-Z0-9_.:-]", "_", ctx.channel or "unknown")
        sender = re.sub(r"[^a-zA-Z0-9_.:-]", "_", ctx.sender_id)
        return f"{channel}:{sender}"

    async def execute(self, **kwargs: Any) -> str:
        trusted_principal: str | None = None
        if self._inject_principal and self._principal_argument is not None:
            trusted_principal = self._trusted_principal()
            kwargs = {**kwargs, self._principal_argument: trusted_principal}
        retried_transient = False
        refreshed_session = False
        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(self._original_name, arguments=kwargs),
                    timeout=self._tool_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
                return ToolResult.error(f"(MCP tool call timed out after {self._tool_timeout}s)")
            except asyncio.CancelledError:
                # MCP SDK's anyio cancel scopes can leak CancelledError on timeout/failure.
                # Re-raise only if our task was externally cancelled (e.g. /stop).
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
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
                    return ToolResult.error(
                        f"(MCP tool call failed after retry: {type(exc).__name__})"
                    )
                logger.exception(
                    "MCP tool '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return ToolResult.error(f"(MCP tool call failed: {type(exc).__name__})")
            else:
                # Success — extract text and persist any image content as artifacts.
                try:
                    rendered = self._render_call_result(result.content, kwargs)
                    if getattr(result, "isError", False):
                        return ToolResult.error(rendered)
                    profile = _profile_from_rendered_result(rendered)
                    session_key = _profile_session_key(current_request_context())
                    if profile and session_key is not None:
                        _SESSION_MCP_TOOL_PROFILES[(session_key, self._server_name)] = profile
                    context_changed = self._persist_domain_context(
                        rendered,
                        kwargs,
                        trusted_principal=trusted_principal,
                    )
                    return ToolResult(
                        rendered,
                        context_barrier=context_changed,
                    )
                except Exception as exc:
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
    ) -> bool:
        if self._domain_context is None or self._session_store is None:
            return False
        request = current_request_context()
        session_key = _profile_session_key(request)
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
            campaign_id = campaign_id or (previous.campaign_id if previous else "")
            if not campaign_id:
                return False
            if not trusted_fingerprint:
                return False
            branch_id = _first_string_field(
                payload,
                frozenset({"active_branch_id", "branch_id"}),
            )
            requested_audience = _first_string_field(
                arguments,
                frozenset({"audience"}),
            )
            fallback_audience = previous.audience if previous else ""
            if previous and not requested_audience:
                if previous.role in {"owner", "dm"}:
                    fallback_audience = "dm"
                elif previous.role == "player":
                    fallback_audience = "player"
            binding = DomainContextBinding(
                domain=self._domain_context,
                campaign_id=campaign_id,
                principal_fingerprint=trusted_fingerprint,
                role=previous.role if previous else "",
                audience=requested_audience or fallback_audience,
                branch_id=branch_id or (previous.branch_id if previous else ""),
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

    def _render_call_result(self, content: Any, arguments: Mapping[str, Any]) -> str:
        """Turn MCP content blocks into a tool result string.

        Text is concatenated as before. Image blocks are decoded and saved as
        local artifacts (mirroring the built-in image generation tool) so the
        model can deliver them via the message tool instead of trying to forward
        base64 — which would be truncated and bloat the context window.
        """
        from mcp import types

        text_parts: list[str] = []
        artifacts: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, types.TextContent):
                text_parts.append(block.text)
                continue
            data_url = _image_block_data_url(block, types)
            if data_url is not None:
                stored = self._store_image_block(data_url, arguments)
                if stored is not None:
                    artifacts.append(stored)
                else:
                    text_parts.append("(MCP tool returned an image that could not be stored)")
                continue
            text_parts.append(str(block))

        if artifacts:
            return _mcp_image_tool_result(text_parts, artifacts)
        return "\n".join(text_parts) or "(no output)"

    def _store_image_block(
        self, data_url: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Persist one image data URL as an artifact; return its metadata or None."""
        from nanobot.utils.artifacts import ArtifactError, store_generated_image_artifact

        try:
            return store_generated_image_artifact(
                data_url,
                prompt=str(arguments.get("prompt") or ""),
                model=str(arguments.get("model") or ""),
                save_dir="generated",
                provider=f"mcp:{self._server_name}",
            )
        except (ArtifactError, OSError) as exc:
            logger.warning(
                "MCP tool '{}' returned an image that could not be stored: {}",
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
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
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
        from mcp.shared.exceptions import McpError

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
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP prompt '{}' was cancelled by server/SDK", self._name)
                return "(MCP prompt call was cancelled)"
            except McpError as exc:
                if await self._refresh_session_after_termination(
                    exc,
                    refreshed_session,
                    "prompt",
                ):
                    refreshed_session = True
                    continue
                logger.exception(
                    "MCP prompt '{}' failed: code={} message={}",
                    self._name,
                    exc.error.code,
                    exc.error.message,
                )
                return f"(MCP prompt call failed: {exc.error.message} [code {exc.error.code}])"
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
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    async def open_single_server(name: str, cfg) -> tuple[str, AsyncExitStack | None]:
        server_stack = AsyncExitStack()
        await server_stack.__aenter__()

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
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                    cwd=cfg.cwd or None,
                )
                read, write = await server_stack.enter_async_context(stdio_client(params))
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

                read, write = await server_stack.enter_async_context(
                    sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
                )
            elif transport_type == "streamableHttp":
                if not await _probe_http_url(cfg.url):
                    logger.warning(
                        "MCP server '{}': {} unreachable, skipping", name, _redact_url(cfg.url)
                    )
                    await server_stack.aclose()
                    return name, None

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
                read, write, _ = await server_stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': unknown transport type '{}'", name, transport_type)
                await server_stack.aclose()
                return name, None

            read = _filter_malformed_mcp_progress_notifications(read, name)
            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            enabled_tools = set(cfg.enabled_tools)
            allow_all_tools = "*" in enabled_tools
            registered_count = 0
            matched_enabled_tools: set[str] = set()
            available_raw_names = [tool_def.name for tool_def in tools.tools]
            available_wrapped_names = [
                _sanitize_mcp_tool_name(f"mcp_{name}_{tool_def.name}") for tool_def in tools.tools
            ]
            for tool_def in tools.tools:
                wrapped_name = _sanitize_mcp_tool_name(f"mcp_{name}_{tool_def.name}")
                if (
                    not allow_all_tools
                    and tool_def.name not in enabled_tools
                    and wrapped_name not in enabled_tools
                ):
                    logger.debug(
                        "MCP: skipping tool '{}' from server '{}' (not in enabledTools)",
                        wrapped_name,
                        name,
                    )
                    continue
                wrapper = MCPToolWrapper(
                    session,
                    name,
                    tool_def,
                    tool_timeout=cfg.tool_timeout,
                    inject_principal=cfg.inject_principal,
                    default_tool_profile=cfg.default_tool_profile,
                    session_store=session_store,
                )
                registry.register(wrapper)
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)
                registered_count += 1
                if enabled_tools:
                    if tool_def.name in enabled_tools:
                        matched_enabled_tools.add(tool_def.name)
                    if wrapped_name in enabled_tools:
                        matched_enabled_tools.add(wrapped_name)

            # SagaSmith v2 keeps activation transport out of tools/list. The
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
                        default_tool_profile=cfg.default_tool_profile,
                        session_store=session_store,
                        host_token=host_token,
                    )
                    registry.register(private_wrapper)
                    registered_count += 1

            if enabled_tools and not allow_all_tools:
                unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
                if unmatched_enabled_tools:
                    logger.warning(
                        "MCP server '{}': enabledTools entries not found: {}. Available raw names: {}. "
                        "Available wrapped names: {}",
                        name,
                        ", ".join(unmatched_enabled_tools),
                        ", ".join(available_raw_names) or "(none)",
                        ", ".join(available_wrapped_names) or "(none)",
                    )

            # Only register resources and prompts when no tool restriction is
            # active.  enabledTools is a per-*tool* allowlist; resources and
            # prompts have no equivalent name filter, so they must be skipped
            # whenever the operator specified a tool subset.  An empty list
            # (deny-all) or a list of specific tool names both indicate that
            # the operator intended to restrict capabilities — registering
            # unrestricted resource/prompt wrappers would violate that intent.
            # The default ["*"] (allow-all) means no restriction was intended.
            register_extras = allow_all_tools
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
                    "(enabledTools does not include '*' — only tools allowed)",
                    name,
                )

            logger.info(
                "MCP server '{}': connected, {} capabilities registered", name, registered_count
            )
            return name, server_stack

        except Exception as e:
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

    for name, cfg in mcp_servers.items():
        try:
            result = await connect_single_server(name, cfg)
        except Exception as e:
            logger.exception("MCP server '{}' connection failed: {}", name, e)
            continue
        if result is not None and result[1] is not None:
            server_stacks[result[0]] = result[1]

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
            next_servers = dict(config.tools.mcp_servers)
        except Exception as exc:
            logger.warning("MCP hot reload could not read config: {}", exc)
            return {
                "ok": False,
                "message": "Could not reload MCP config. Restart nanobot to pick up changes.",
                "requires_restart": True,
                "error": str(exc),
            }

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

        failed = sorted(set(to_connect) - set(connected))
        unchanged = not removed and not added and not changed and not retry_missing
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
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
