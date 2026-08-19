"""Signed Host identity metadata kept outside model-authored MCP arguments."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any

AUTH_CONTEXT_META_KEY = "sagasmith_auth_context"
AUTH_CONTEXT_RECEIPT_META_KEY = "sagasmith_auth_context_receipt"
AUTH_CONTEXT_SCHEMA = "sagasmith.auth-context/v1"


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
