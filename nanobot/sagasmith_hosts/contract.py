"""One strict identity contract for every supported SagaSmith MCP Host."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_SAFE = re.compile(r"[^a-zA-Z0-9_.:@/-]")
_KINDS = frozenset({"user", "dm", "group", "channel", "thread", "project", "session"})


def _required(value: Any, field: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str) or not (result := value.strip()):
        raise ValueError(f"{field} is required")
    result = _SAFE.sub("_", result)
    if len(result) > maximum:
        raise ValueError(f"{field} is too long")
    return result


def _optional(value: Any, *, maximum: int = 300) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("optional identity values must be strings")
    result = _SAFE.sub("_", value.strip())
    if len(result) > maximum:
        raise ValueError("optional identity value is too long")
    return result


def _channel(value: Any) -> str:
    return _required(value, "channel", maximum=80).casefold()


def _principal(channel: str, kind: str, identifier: Any) -> str:
    if kind not in _KINDS:
        raise ValueError(f"unsupported principal kind: {kind}")
    return f"{channel}:{kind}:{_required(identifier, f'{kind}_id')}"


@dataclass(frozen=True)
class TrustedHostContext:
    """Model-invisible actor and conversation facts asserted by a Host adapter."""

    host: str
    channel: str
    actor_principal: str
    conversation_principal: str
    session_id: str
    tenant_id: str = ""

    def __post_init__(self) -> None:
        for field in (
            "host",
            "channel",
            "actor_principal",
            "conversation_principal",
            "session_id",
        ):
            _required(getattr(self, field), field)

    def to_dict(self) -> dict[str, str]:
        return {
            "host": self.host,
            "channel": self.channel,
            "actor_principal": self.actor_principal,
            "conversation_principal": self.conversation_principal,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
        }


@dataclass(frozen=True)
class BridgeLaunch:
    """Safe process definition; identity and signing secret are file-backed, not model args."""

    config_path: Path
    context_path: Path
    secret_path: Path
    executable: str = "sagasmith-auth-bridge"

    def stdio_config(self) -> dict[str, Any]:
        return {
            "command": self.executable,
            "args": [
                "--config",
                str(self.config_path),
                "--context",
                str(self.context_path),
                "--secret-file",
                str(self.secret_path),
            ],
        }

    def for_host(self, host: str) -> dict[str, Any]:
        base = self.stdio_config()
        normalized = host.casefold().replace("-", "_")
        if normalized == "codex":
            return {
                "command": base["command"],
                "args": base["args"],
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 900,
            }
        if normalized in {"claude", "claude_code"}:
            return {"type": "stdio", **base}
        if normalized == "nanobot":
            return {**base, "toolTimeout": 900, "enabledTools": ["*"]}
        if normalized == "openclaw":
            return {
                "transport": "stdio",
                **base,
                "timeout": 900,
                "toolFilter": {"include": ["*"]},
            }
        if normalized == "hermes":
            return {
                **base,
                "timeout": 900,
                "tools": {"include": ["*"], "resources": True, "prompts": True},
            }
        raise ValueError(f"unsupported Host configuration: {host}")


def _social_context(
    *,
    host: str,
    channel: Any,
    sender_id: Any,
    chat_id: Any,
    chat_type: Any = "group",
    thread_id: Any = None,
    session_id: Any = None,
    tenant_id: Any = None,
) -> TrustedHostContext:
    normalized_channel = _channel(channel)
    kind = str(chat_type or "group").strip().casefold()
    if kind not in {"dm", "group", "channel", "thread"}:
        raise ValueError(f"unsupported chat type: {kind}")
    conversation_id = thread_id if thread_id else chat_id
    conversation_kind = "thread" if thread_id else kind
    conversation = _principal(normalized_channel, conversation_kind, conversation_id)
    actor = _principal(normalized_channel, "user", sender_id)
    return TrustedHostContext(
        host=_required(host, "host", maximum=80),
        channel=normalized_channel,
        actor_principal=actor,
        conversation_principal=conversation,
        session_id=_optional(session_id) or f"{host}:{conversation}",
        tenant_id=_optional(tenant_id),
    )


def adapt_sagasmith_agent(message: Mapping[str, Any]) -> TrustedHostContext:
    channel = _channel(message.get("channel"))
    actor = _optional(message.get("actor_principal"))
    conversation = _optional(message.get("conversation_principal"))
    if actor and not actor.startswith(f"{channel}:"):
        actor = f"{channel}:{actor}"
    if conversation and not conversation.startswith(f"{channel}:"):
        conversation = f"{channel}:{conversation}"
    return TrustedHostContext(
        host="sagasmith-agent",
        channel=channel,
        actor_principal=actor or _principal(channel, "user", message.get("sender_id")),
        conversation_principal=conversation
        or _principal(channel, "session", message.get("chat_id")),
        session_id=_optional(message.get("session_key"))
        or f"{channel}:{_required(message.get('chat_id'), 'chat_id')}",
        tenant_id=_optional(message.get("tenant_id")),
    )


def adapt_nanobot(message: Mapping[str, Any]) -> TrustedHostContext:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), Mapping) else {}
    return _social_context(
        host="nanobot",
        channel=message.get("channel"),
        sender_id=message.get("sender_id"),
        chat_id=message.get("chat_id"),
        chat_type=metadata.get("chat_type", "group"),
        thread_id=metadata.get("thread_id"),
        session_id=message.get("session_key") or message.get("session_key_override"),
        tenant_id=metadata.get("tenant_id"),
    )


def adapt_openclaw(context: Mapping[str, Any]) -> TrustedHostContext:
    """Map OpenClaw's trusted connection-resolver context, never transcript text."""

    return _social_context(
        host="openclaw",
        channel=context.get("messageChannel") or context.get("channel"),
        sender_id=context.get("requesterSenderId"),
        chat_id=context.get("conversationId") or context.get("chatId"),
        chat_type=context.get("chatType", "group"),
        thread_id=context.get("threadId"),
        session_id=context.get("sessionId"),
        tenant_id=context.get("agentAccountId") or context.get("tenantId"),
    )


def adapt_hermes(session_source: Mapping[str, Any]) -> TrustedHostContext:
    """Map Hermes Gateway SessionSource fields supplied by the channel adapter."""

    return _social_context(
        host="hermes",
        channel=session_source.get("platform"),
        sender_id=session_source.get("user_id") or session_source.get("user_id_alt"),
        chat_id=session_source.get("chat_id") or session_source.get("chat_id_alt"),
        chat_type=session_source.get("chat_type", "group"),
        thread_id=session_source.get("thread_id"),
        session_id=session_source.get("session_id") or session_source.get("session_key"),
        tenant_id=session_source.get("scope_id") or session_source.get("guild_id"),
    )


def _local_host(host: str, profile_id: Any, project_id: Any, session_id: Any) -> TrustedHostContext:
    profile = _required(profile_id, "profile_id")
    project = _required(project_id, "project_id")
    return TrustedHostContext(
        host=host,
        channel="local",
        actor_principal=f"{host}:user:{profile}",
        conversation_principal=f"{host}:project:{project}",
        session_id=_optional(session_id) or f"{host}:{project}",
    )


def adapt_codex(*, profile_id: Any, project_id: Any, session_id: Any = None) -> TrustedHostContext:
    return _local_host("codex", profile_id, project_id, session_id)


def adapt_claude_code(
    *, profile_id: Any, project_id: Any, session_id: Any = None
) -> TrustedHostContext:
    return _local_host("claude-code", profile_id, project_id, session_id)


def adapt_service_worker(
    *, principal_id: Any, conversation_id: Any, tenant_id: Any = None
) -> TrustedHostContext:
    principal = _required(principal_id, "principal_id")
    return TrustedHostContext(
        host="sagasmith-service",
        channel="service",
        actor_principal=principal,
        conversation_principal=f"service:session:{_required(conversation_id, 'conversation_id')}",
        session_id=f"service:{_required(conversation_id, 'conversation_id')}",
        tenant_id=_optional(tenant_id),
    )
