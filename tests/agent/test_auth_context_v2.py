from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from nanobot.agent.auth_context import (
    AUTH_CONTEXT_DELEGATION_SCHEMA,
    sign_delegated_auth_context,
)

SECRET = "delegation-test-secret-at-least-32-bytes"
NOW = datetime(2026, 8, 29, tzinfo=UTC)


def test_delegated_auth_context_matches_core_v2_wire_contract() -> None:
    envelope = sign_delegated_auth_context(
        secret=SECRET,
        issuer="sagasmith-web",
        target_service="sagasmith-dnd-mcp",
        caller_principal="workload:web:room-worker",
        workload_identity="spiffe://sagasmith/web/room-worker",
        requester_principal="discord:user:alice",
        resource_owner_principal="discord:user:owner",
        acting_host_principal="campaign:dm",
        acting_character_id="hero",
        authorized_audience="player",
        allowed_operations=("actor_query", "combat_action"),
        conversation_principal="discord:group:table-1",
        tenant_id="tenant-1",
        campaign_id="campaign-1",
        room_turn_id="turn-4",
        base_revision=3,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        nonce="nonce-1",
    )

    assert envelope["schema"] == AUTH_CONTEXT_DELEGATION_SCHEMA
    assert set(envelope) == {
        "schema",
        "issuer",
        "target_service",
        "caller_principal",
        "workload_identity",
        "requester_principal",
        "resource_owner_principal",
        "acting_host_principal",
        "acting_character_id",
        "authorized_audience",
        "allowed_operations",
        "conversation_principal",
        "tenant_id",
        "campaign_id",
        "room_turn_id",
        "base_revision",
        "principal_source",
        "issued_at",
        "expires_at",
        "nonce",
        "signature",
    }
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(envelope["signature"], expected)


@pytest.mark.parametrize("operations", [(), ("*",), ("read", "read")])
def test_delegated_auth_context_rejects_unsafe_operation_sets(operations) -> None:
    with pytest.raises(ValueError):
        sign_delegated_auth_context(
            secret=SECRET,
            issuer="web",
            target_service="mcp",
            caller_principal="caller",
            workload_identity="workload",
            requester_principal="requester",
            resource_owner_principal="owner",
            acting_host_principal="host",
            authorized_audience="player",
            allowed_operations=operations,
            conversation_principal="room",
            campaign_id="campaign",
            room_turn_id="turn",
            base_revision=0,
        )
