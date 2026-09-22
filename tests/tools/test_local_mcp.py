from types import SimpleNamespace

import pytest

from nanobot.agent.local_mcp import LocalOperationState
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import MCPToolWrapper
from nanobot.config.schema import MCPServerConfig
from nanobot.session.manager import SessionManager


def definition():
    return SimpleNamespace(
        name="combat_resolve_attack", description="Attack", annotations=SimpleNamespace(
            read_only_hint=False), meta={"sagasmith_local_authority": True},
        input_schema={"type": "object", "properties": {
            "principal_id": {"type": "string", "default": "system:local"},
            "campaign_id": {"type": "string"}, "actor_id": {"type": "string"},
            "expected_revision": {"type": "integer"},
            "idempotency_key": {"type": "string"},
        }, "required": ["campaign_id", "actor_id", "expected_revision", "idempotency_key"]},
    )


@pytest.mark.asyncio
async def test_local_host_persists_unknown_operation_and_recovers_same_key(tmp_path):
    store = SessionManager(tmp_path)
    context = RequestContext(channel="cli", chat_id="one", session_key="cli:one")
    calls = []

    def wrapper(state):
        return MCPToolWrapper(None, "dnd", definition(), local_operations=state)

    async def unknown(args, **kwargs):
        calls.append(args)
        return ToolResult("response lost", is_error=True, dispatch_unknown=True)

    async def success(args, **kwargs):
        calls.append(args)
        return ToolResult("committed")

    with request_context(context):
        first = wrapper(LocalOperationState("dnd", store, "system:local"))
        assert set(first.parameters["required"]) == {"campaign_id", "actor_id"}
        assert "principal_id" not in first.parameters["properties"]
        first._execute_call = unknown
        await first.execute(campaign_id="campaign", actor_id="hero")
        second = wrapper(LocalOperationState("dnd", SessionManager(tmp_path), "system:local"))
        second._execute_call = success
        with pytest.raises(RuntimeError, match="unknown result"):
            await second.execute(campaign_id="campaign", actor_id="different")
        await second.execute(campaign_id="campaign", actor_id="hero")
        assert calls[0] == calls[1]
        await second.execute(campaign_id="campaign", actor_id="hero")
        assert calls[2]["idempotency_key"] != calls[0]["idempotency_key"]


@pytest.mark.asyncio
async def test_local_subagent_cannot_commit():
    wrapper = MCPToolWrapper(None, "dnd", definition(), local_operations=LocalOperationState(
        "dnd", None, "system:local"))
    with request_context(RequestContext(channel="cli", chat_id="one", metadata={"subagent": True})):
        result = await wrapper.execute(campaign_id="campaign", actor_id="hero")
    assert result.is_error
    assert "cannot commit" in result


def test_local_configuration_rejects_shared_identity():
    with pytest.raises(ValueError, match="stdio"):
        MCPServerConfig(type="streamableHttp", local_authority=True, bound_principal_id="owner")
    config = MCPServerConfig(type="stdio", local_authority=True, bound_principal_id="owner")
    assert config.read_timeout < config.write_timeout < config.task_timeout
