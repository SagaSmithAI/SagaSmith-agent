from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client, types

from nanobot.sagasmith_hosts.bridge import AuthBridge
from nanobot.sagasmith_hosts.contract import (
    BridgeLaunch,
    TrustedHostContext,
    adapt_claude_code,
    adapt_codex,
    adapt_hermes,
    adapt_nanobot,
    adapt_openclaw,
    adapt_sagasmith_agent,
    adapt_service_worker,
)

SECRET = "host-conformance-auth-secret-at-least-32-bytes"


def _social_contexts(sender: str) -> list[TrustedHostContext]:
    return [
        adapt_sagasmith_agent(
            {
                "channel": "discord",
                "sender_id": sender,
                "chat_id": "table-1",
                "actor_principal": f"user:{sender}",
                "conversation_principal": "group:table-1",
                "session_key": "discord:table-1",
            }
        ),
        adapt_nanobot(
            {
                "channel": "discord",
                "sender_id": sender,
                "chat_id": "table-1",
                "metadata": {"chat_type": "group"},
            }
        ),
        adapt_openclaw(
            {
                "messageChannel": "discord",
                "requesterSenderId": sender,
                "conversationId": "table-1",
                "chatType": "group",
                "sessionId": "discord:table-1",
            }
        ),
        adapt_hermes(
            {
                "platform": "discord",
                "user_id": sender,
                "chat_id": "table-1",
                "chat_type": "group",
                "session_id": "discord:table-1",
            }
        ),
    ]


def test_all_multi_user_adapters_separate_actor_from_shared_conversation() -> None:
    alice = _social_contexts("alice")
    bob = _social_contexts("bob")

    for alice_context, bob_context in zip(alice, bob, strict=True):
        assert alice_context.actor_principal == "discord:user:alice"
        assert bob_context.actor_principal == "discord:user:bob"
        assert alice_context.conversation_principal == "discord:group:table-1"
        assert bob_context.conversation_principal == alice_context.conversation_principal
        assert alice_context.session_id == bob_context.session_id


def test_local_and_hosted_adapters_have_stable_non_group_identity() -> None:
    codex = adapt_codex(profile_id="local-1", project_id="campaign-tools")
    claude = adapt_claude_code(profile_id="local-1", project_id="campaign-tools")
    worker = adapt_service_worker(principal_id="user:42", conversation_id="conversation-9")

    assert codex.actor_principal == "codex:user:local-1"
    assert codex.conversation_principal == "codex:project:campaign-tools"
    assert claude.actor_principal == "claude-code:user:local-1"
    assert worker.actor_principal == "user:42"
    assert worker.conversation_principal == "service:session:conversation-9"


@pytest.mark.parametrize("host", ["codex", "claude-code", "nanobot", "openclaw", "hermes"])
def test_bridge_launch_generates_each_official_host_shape(host: str, tmp_path: Path) -> None:
    launch = BridgeLaunch(
        config_path=tmp_path / "mcp.json",
        context_path=tmp_path / "context.json",
        secret_path=tmp_path / "secret",
    )
    config = launch.for_host(host)

    assert config["command"] == "sagasmith-auth-bridge"
    assert str(tmp_path / "context.json") in config["args"]
    assert str(tmp_path / "secret") in config["args"]


class _FakeDownstream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(
            tools=[
                types.Tool(
                    name="exposure",
                    description="exposure",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "campaign_id": {"type": "string"},
                            "principal_id": {"type": "string"},
                        },
                    },
                ),
                types.Tool(
                    name="campaign_query",
                    description="campaign",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "campaign_id": {"type": "string"},
                            "principal_id": {"type": "string"},
                        },
                    },
                ),
            ]
        )

    async def call_tool(
        self, name: str, *, arguments: dict, meta: dict
    ) -> types.CallToolResult:
        self.calls.append((name, dict(arguments), dict(meta)))
        revision = 7 if name == "exposure" else 11
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")],
            structuredContent={"revision": revision},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_auth_bridge_has_legacy_and_modern_equivalent_contract(mode: str) -> None:
    context = adapt_codex(profile_id="local-1", project_id="campaign-tools")
    bridge = AuthBridge(
        server_config={
            "protocolMode": mode,
            "targetService": "sagasmith-dnd-mcp",
            "authorizationAudience": "sagasmith-dnd-mcp",
        },
        context=context,
        secret=SECRET,
    )
    downstream = _FakeDownstream()
    bridge.downstream = downstream  # type: ignore[assignment]

    async with Client(bridge.server, mode=mode) as client:
        listed = await client.list_tools()
        result = await client.call_tool(
            "exposure",
            {"action": "open", "campaign_id": "campaign-1"},
        )

    assert [tool.name for tool in listed.tools] == ["campaign_query", "exposure"]
    assert result.is_error is False
    auth = downstream.calls[0][2]["sagasmith_auth_context"]
    assert auth["schema"] == f"sagasmith.auth-context/{'v2' if mode == '2026-07-28' else 'v1'}"
    if mode == "2026-07-28":
        assert auth["target_service"] == "sagasmith-dnd-mcp"
        assert auth["allowed_operations"] == ["exposure"]
        assert auth["requester_principal"] == context.actor_principal


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["dnd", "coc", "narrative"])
@pytest.mark.parametrize("context", _social_contexts("alice"), ids=lambda item: item.host)
async def test_every_multi_user_host_uses_same_signed_three_domain_call_contract(
    domain: str, context: TrustedHostContext
) -> None:
    downstream = _FakeDownstream()
    bridge = AuthBridge(server_config={}, context=context, secret=SECRET)
    bridge.downstream = downstream  # type: ignore[assignment]
    await bridge._list_tools()

    await bridge._call_tool(
        "exposure",
        {
            "action": "open",
            "campaign_id": f"{domain}-campaign",
            "principal_id": "model:forged-owner",
        },
    )
    await bridge._call_tool(
        "campaign_query",
        {
            "campaign_id": f"{domain}-campaign",
            "principal_id": "model:forged-owner",
        },
    )

    _, open_arguments, open_meta = downstream.calls[0]
    _, query_arguments, query_meta = downstream.calls[1]
    assert open_arguments["principal_id"] == context.actor_principal
    assert query_arguments["principal_id"] == context.actor_principal
    assert open_meta["sagasmith_auth_context"]["authorization_epoch"] == 0
    assert query_meta["sagasmith_auth_context"]["authorization_epoch"] == 7
    assert query_meta["sagasmith_auth_context"]["campaign_id"] == f"{domain}-campaign"
    assert query_meta["sagasmith_auth_context"]["conversation_principal"] == (
        context.conversation_principal
    )


def test_adapter_rejects_missing_trusted_sender() -> None:
    with pytest.raises(ValueError, match="user_id is required"):
        adapt_hermes(
            {
                "platform": "discord",
                "chat_id": "table-1",
                "chat_type": "group",
            }
        )
