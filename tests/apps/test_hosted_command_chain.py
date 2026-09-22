import asyncio
from types import SimpleNamespace

import pytest
from mcp import types

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import MCPToolWrapper
from nanobot.apps.hosted_operations import JournaledTool


def context():
    return RequestContext(channel="service", chat_id="room", campaign_id="campaign",
        room_turn_id="turn", base_revision=4, requester_principal="user:a",
        resource_owner_principal="user:a", acting_host_principal="campaign:dm",
        allowed_operations=("combat_common_action", "combat_end_turn", "combat_query"))


def wrapper(session, name, read_only=False):
    return MCPToolWrapper(session, "sagasmith-dnd-mcp", types.Tool(name=name,
        input_schema={"type": "object", "properties": {
            "campaign_id": {"type": "string"}, "expected_revision": {"type": "integer"},
            "idempotency_key": {"type": "string"}}},
        annotations=types.ToolAnnotations(read_only_hint=read_only),
        meta={"sagasmith_campaign_revision_argument": "expected_revision"}),
        inject_principal=True, delegation_secret="test-secret-at-least-thirty-two-characters",
        target_service="sagasmith-dnd-mcp", protocol="2026-07-28")


def journal(tool, reports):
    async def post(*args, **kwargs):
        reports.append(kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None)
    return JournaledTool(tool, SimpleNamespace(post=post), {"url": "http://unused", "token": "test"})


@pytest.mark.asyncio
async def test_confirmed_receipts_advance_only_this_turn_and_override_model_revision():
    calls, reports = [], []

    class Session:
        async def call_tool(self, name, *, arguments, meta):
            auth = meta["sagasmith_auth_context"]
            calls.append((arguments, auth))
            receipt = {**auth, "tool": name, "revision": 999,
                       "campaign_revision": arguments["expected_revision"] + 1}
            return types.CallToolResult(content=[types.TextContent(type="text", text="ok",
                meta={"sagasmith_auth_context_receipt": receipt})],
                structured_content={"campaign_revision": receipt["campaign_revision"]})

    ctx = context()
    with request_context(ctx):
        for name in ("combat_common_action", "combat_end_turn"):
            tool = wrapper(Session(), name)
            assert "expected_revision" not in tool.parameters["properties"]
            await journal(tool, reports).execute(expected_revision=999)
    assert [args["expected_revision"] for args, _ in calls] == [4, 5]
    assert [auth["base_revision"] for _, auth in calls] == [4, 5]
    assert ctx.command_progress["revision"] == 6
    assert context().command_progress == {}


@pytest.mark.asyncio
async def test_lost_reply_keeps_fence_and_blocks_new_idempotency_keys():
    reports, keys = [], []

    class Session:
        async def call_tool(self, name, *, arguments, meta):
            keys.append(arguments["idempotency_key"])
            raise asyncio.TimeoutError()

    tool = journal(wrapper(Session(), "combat_common_action"), reports)
    with request_context(context()):
        first = await tool.execute()
        second = await tool.execute()
    assert first.dispatch_unknown and second.dispatch_unknown
    assert len(keys) == 1
    assert [r["state"] for r in reports] == ["dispatched"]


def test_mcp_read_annotations_are_consumed_conservatively():
    assert wrapper(None, "combat_query", True).read_only
    assert not wrapper(None, "combat_common_action").read_only
    assert "expected_revision" in wrapper(None, "combat_common_action").parameters["properties"]
