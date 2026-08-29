from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mcp import types

from nanobot.agent.auth_context import AUTH_CONTEXT_DELEGATION_SCHEMA
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import MCPToolWrapper
from nanobot.agent.tools.registry import ToolRegistry


class CapturingSession:
    def __init__(self) -> None:
        self.arguments = None
        self.meta = None

    async def call_tool(self, _name, *, arguments, meta=None):
        self.arguments = arguments
        self.meta = meta
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")],
            structuredContent={"revision": 8},
            isError=False,
        )


def test_trusted_operations_filter_model_catalog_without_mutating_stable_catalog() -> None:
    session = CapturingSession()
    registry = ToolRegistry()
    for operation in ("campaign_query", "campaign_delete"):
        registry.register(
            MCPToolWrapper(
                session,
                "sagasmith-dnd-mcp",
                types.Tool(
                    name=operation,
                    description=operation,
                    inputSchema={"type": "object", "properties": {}},
                ),
            )
        )
    context = RequestContext(
        channel="service",
        chat_id="table-1",
        allowed_operations=("campaign_query",),
    )

    with request_context(context):
        assert registry.definition_names() == ["mcp_sagasmith-dnd-mcp_campaign_query"]
        assert registry.prepare_call("mcp_sagasmith-dnd-mcp_campaign_delete", {})[2] is not None

    assert sorted(registry.tool_names) == [
        "mcp_sagasmith-dnd-mcp_campaign_delete",
        "mcp_sagasmith-dnd-mcp_campaign_query",
    ]


@pytest.mark.asyncio
async def test_v2_delegation_and_turn_fields_are_model_invisible_and_server_bound() -> None:
    session = CapturingSession()
    tool = types.Tool(
        name="combat_action",
        description="Resolve one action.",
        inputSchema={
            "type": "object",
            "required": [
                "principal_id",
                "campaign_id",
                "room_turn_id",
                "base_revision",
                "idempotency_key",
            ],
            "properties": {
                "principal_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "room_turn_id": {"type": "string"},
                "base_revision": {"type": "integer"},
                "idempotency_key": {"type": "string"},
                "move": {"type": "string"},
            },
        },
    )
    wrapper = MCPToolWrapper(
        session,
        "sagasmith-dnd-mcp",
        tool,
        inject_principal=True,
        delegation_secret="delegation-test-secret-at-least-32-bytes",
        authorization_audience="player",
    )
    assert set(wrapper.parameters["properties"]) == {"move"}

    expiry = datetime.now(UTC) + timedelta(minutes=5)
    context = RequestContext(
        channel="service",
        chat_id="table-1",
        session_key="service:table-1",
        sender_id="discord:user:alice",
        actor_principal="discord:user:alice",
        conversation_principal="discord:group:table-1",
        room_turn_id="turn-7",
        campaign_id="campaign-1",
        system_id="dnd5e",
        base_revision=7,
        delegation_expires_at=expiry.isoformat(),
        allowed_operations=("combat_action",),
        requester_principal="discord:user:alice",
        resource_owner_principal="discord:user:owner",
        acting_host_principal="campaign:dm",
        acting_character_ref="hero",
        metadata={
            "caller_principal": "workload:web:room-worker",
            "workload_identity": "spiffe://sagasmith/web/room-worker",
            "idempotency_key": "turn-7:combat-1",
            "traceparent": "00-00000000000000000000000000000001-0000000000000001-01",
        },
    )
    with request_context(context):
        result = await wrapper.execute(move="north")

    assert session.arguments == {
        "move": "north",
        "principal_id": "discord:user:alice",
        "campaign_id": "campaign-1",
        "room_turn_id": "turn-7",
        "base_revision": 7,
        "idempotency_key": "turn-7:combat-1",
    }
    auth = session.meta["sagasmith_auth_context"]
    assert auth["schema"] == AUTH_CONTEXT_DELEGATION_SCHEMA
    assert auth["target_service"] == "sagasmith-dnd-mcp"
    assert auth["allowed_operations"] == ["combat_action"]
    assert auth["room_turn_id"] == "turn-7"
    assert session.meta["traceparent"].startswith("00-")
    assert result.mcp_result == {
        "content": [{"type": "text", "text": "ok"}],
        "structuredContent": {"revision": 8},
        "isError": False,
        "resultType": "complete",
    }


@pytest.mark.asyncio
async def test_v2_delegation_denies_an_operation_not_granted_by_host() -> None:
    session = CapturingSession()
    wrapper = MCPToolWrapper(
        session,
        "sagasmith-dnd-mcp",
        types.Tool(
            name="combat_action",
            inputSchema={"type": "object", "properties": {"principal_id": {"type": "string"}}},
        ),
        inject_principal=True,
        delegation_secret="delegation-test-secret-at-least-32-bytes",
    )
    context = RequestContext(
        channel="service",
        chat_id="table-1",
        sender_id="alice",
        actor_principal="alice",
        conversation_principal="room",
        room_turn_id="turn",
        campaign_id="campaign",
        base_revision=0,
        allowed_operations=("actor_query",),
        requester_principal="alice",
        resource_owner_principal="owner",
        acting_host_principal="host",
    )
    with request_context(context):
        result = await wrapper.execute()
    assert result.is_error is True
    assert session.arguments is None
