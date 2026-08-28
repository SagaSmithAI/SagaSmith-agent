"""Signed Host identity metadata kept outside model-authored MCP arguments."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

AUTH_CONTEXT_META_KEY = "sagasmith_auth_context"
AUTH_CONTEXT_RECEIPT_META_KEY = "sagasmith_auth_context_receipt"
AUTH_CONTEXT_SCHEMA = "sagasmith.auth-context/v1"
AUTH_CONTEXT_DELEGATION_SCHEMA = "sagasmith.auth-context/v2"


def sign_auth_context(
    *,
    secret: str,
    host: str,
    channel: str,
    actor_principal: str,
    conversation_principal: str,
    session_id: str,
    tenant_id: str = "",
    campaign_id: str = "",
    authorization_epoch: int = 0,
    issued_at: datetime | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Build the canonical v1 envelope verified by SagaSmith Core/MCP."""

    secret_bytes = secret.encode("utf-8")
    if len(secret_bytes) < 32:
        raise ValueError("auth context secret must contain at least 32 bytes")
    payload = {
        "schema": AUTH_CONTEXT_SCHEMA,
        "host": host.strip(),
        "channel": channel.strip(),
        "actor_principal": actor_principal.strip(),
        "conversation_principal": conversation_principal.strip(),
        "tenant_id": tenant_id.strip(),
        "campaign_id": campaign_id.strip(),
        "session_id": session_id.strip(),
        "principal_source": "trusted-host",
        "authorization_epoch": int(authorization_epoch),
        "issued_at": (issued_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "nonce": nonce or secrets.token_urlsafe(24),
    }
    if not all(
        payload[field]
        for field in ("host", "channel", "actor_principal", "conversation_principal", "session_id")
    ):
        raise ValueError("auth context identity fields are required")
    if payload["authorization_epoch"] < 0:
        raise ValueError("authorization_epoch must be non-negative")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {**payload, "signature": signature}


def sign_delegated_auth_context(
    *,
    secret: str,
    issuer: str,
    target_service: str,
    caller_principal: str,
    workload_identity: str,
    requester_principal: str,
    resource_owner_principal: str,
    acting_host_principal: str,
    authorized_audience: str,
    allowed_operations: tuple[str, ...],
    conversation_principal: str,
    campaign_id: str,
    room_turn_id: str,
    base_revision: int,
    acting_character_id: str = "",
    tenant_id: str = "",
    expires_at: datetime | None = None,
    issued_at: datetime | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Build the Core-compatible v2 delegation for one MCP service and turn."""

    secret_bytes = secret.encode("utf-8")
    if len(secret_bytes) < 32:
        raise ValueError("delegation secret must contain at least 32 bytes")
    now = (issued_at or datetime.now(UTC)).astimezone(UTC)
    expiry = (expires_at or now + timedelta(minutes=5)).astimezone(UTC)
    if expiry <= now:
        raise ValueError("delegation expiry must be after issued_at")
    if expiry - now > timedelta(minutes=15):
        raise ValueError("delegation lifetime must not exceed 15 minutes")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
        raise ValueError("base_revision must be a non-negative integer")

    def required(value: str, field: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"{field} is required")
        if len(result) > 300:
            raise ValueError(f"{field} is too long")
        return result

    operations = sorted(required(item, "allowed_operation") for item in allowed_operations)
    if not operations:
        raise ValueError("at least one allowed operation is required")
    if len(operations) > 100 or len(set(operations)) != len(operations):
        raise ValueError("allowed_operations must be unique and contain at most 100 entries")
    if "*" in operations:
        raise ValueError("allowed_operations must enumerate concrete operations")
    payload: dict[str, Any] = {
        "schema": AUTH_CONTEXT_DELEGATION_SCHEMA,
        "issuer": required(issuer, "issuer"),
        "target_service": required(target_service, "target_service"),
        "caller_principal": required(caller_principal, "caller_principal"),
        "workload_identity": required(workload_identity, "workload_identity"),
        "requester_principal": required(requester_principal, "requester_principal"),
        "resource_owner_principal": required(
            resource_owner_principal, "resource_owner_principal"
        ),
        "acting_host_principal": required(acting_host_principal, "acting_host_principal"),
        "acting_character_id": str(acting_character_id or "").strip(),
        "authorized_audience": required(authorized_audience, "authorized_audience"),
        "allowed_operations": operations,
        "conversation_principal": required(conversation_principal, "conversation_principal"),
        "tenant_id": str(tenant_id or "").strip(),
        "campaign_id": required(campaign_id, "campaign_id"),
        "room_turn_id": required(room_turn_id, "room_turn_id"),
        "base_revision": base_revision,
        "principal_source": "trusted-host",
        "issued_at": now.isoformat(),
        "expires_at": expiry.isoformat(),
        "nonce": nonce or secrets.token_urlsafe(24),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {**payload, "signature": signature}
