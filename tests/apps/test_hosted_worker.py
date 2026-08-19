from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from nanobot.apps.hosted_worker import create_worker_app


class FakeRegistry:
    def __init__(self) -> None:
        self.tools = {}

    def has(self, name: str) -> bool:
        return name in self.tools

    def register(self, tool) -> None:
        self.tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)

    def get(self, name: str):
        return self.tools.get(name)


class FakeLoop:
    def __init__(self) -> None:
        self.calls = []
        self._last_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        self.registry = FakeRegistry()
        self.structured_submission = None
        self.structured_tool_results = []
        self.auth_context_receipt = None

    async def _connect_mcp(self) -> None:
        return None

    async def close_mcp(self) -> None:
        return None

    async def _tools_for_session(self, _session_key: str):
        return self.registry

    async def process_direct(self, **arguments):
        self.calls.append(arguments)
        if self.structured_submission is not None:
            tool = arguments["tools"].get("submit_room_turn")
            await tool.execute(**self.structured_submission)
        for index, structured_content in enumerate(self.structured_tool_results):
            for hook in arguments.get("hooks") or []:
                await hook.after_execute_tool(
                    None,
                    SimpleNamespace(name="mcp_resolution"),
                    None,
                    None,
                    SimpleNamespace(
                        structured_content=structured_content,
                        audit_receipt=(
                            self.auth_context_receipt
                            if index == len(self.structured_tool_results) - 1
                            else None
                        ),
                    ),
                )
        return SimpleNamespace(content="ok")


def test_hosted_worker_injects_authenticated_principal_as_sender() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model")) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "session_id": "campaign:user:conversation",
                "principal_id": "user:account-id",
            },
        )
    assert response.status_code == 200
    assert loop.calls[0]["channel"] == "user"
    assert loop.calls[0]["sender_id"] == "account-id"
    assert loop.calls[0]["actor_principal"] == "user:account-id"
    assert loop.calls[0]["conversation_principal"] == (
        "service:session:campaign:user:conversation"
    )
    assert response.json()["usage"]["total_tokens"] == 5


def test_hosted_worker_rejects_untrusted_principal_shape() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model")) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "session_id": "conversation",
                "principal_id": "service:spoofed",
            },
        )
    assert response.status_code == 422


def test_hosted_worker_injects_agent_identity_principal() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model")) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "host this scene"}],
                "session_id": "campaign:agent:identity:conversation",
                "principal_id": "agent:identity-id",
            },
        )
    assert response.status_code == 200
    assert loop.calls[0]["channel"] == "agent"
    assert loop.calls[0]["sender_id"] == "identity-id"


def test_hosted_worker_captures_auth_receipt_without_response_contract() -> None:
    loop = FakeLoop()
    loop.auth_context_receipt = {
        "actor_principal": "user:account-id",
        "conversation_principal": "service:session:campaign:user:conversation",
        "campaign_id": "campaign",
        "session_id": "service:campaign:user:conversation",
        "tool": "actor_query",
        "revision": 3,
    }
    loop.structured_tool_results = [None]
    with TestClient(create_worker_app(loop, "test-model")) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "query actors"}],
                "session_id": "campaign:user:conversation",
                "principal_id": "user:account-id",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["tool_receipts"] == [
        {
            "tool": "mcp_resolution",
            "auth_context_receipt": loop.auth_context_receipt,
        }
    ]


def test_hosted_worker_captures_receipts_and_removes_structured_output_tool() -> None:
    loop = FakeLoop()
    loop.structured_submission = {"schema": "test/v1", "messages": []}
    loop.auth_context_receipt = {
        "actor_principal": "user:account-id",
        "conversation_principal": "service:session:campaign:user:conversation",
        "campaign_id": "campaign",
        "session_id": "service:campaign:user:conversation",
        "tool": "resolution",
        "revision": 7,
    }
    presentation = {
        "schema": "sagasmith.resolution-presentation/v1",
        "resolution_id": "resolution-1",
        "thread_id": "thread-1",
        "event_sequence": 1,
        "system_id": "dnd5e",
        "campaign_id": "campaign",
        "branch_id": None,
        "operation": "attack",
        "status": "settled",
        "audience": {
            "scope": "actors",
            "actor_refs": ["hero"],
            "disclosure": "private",
        },
        "actor_refs": ["hero"],
        "rolls": [],
        "outcome": {"hit": True},
        "pending_choice": None,
        "campaign_revision": 4,
        "random_stream_receipt": {"draw_count": 1},
    }
    loop.structured_tool_results = [
        {"private": "raw MCP receipt"},
        presentation,
    ]
    with TestClient(create_worker_app(loop, "test-model")) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "host this turn"}],
                "session_id": "campaign:user:conversation",
                "principal_id": "user:account-id",
                "response_contract": {
                    "name": "submit_room_turn",
                    "description": "Submit.",
                    "parameters": {"type": "object"},
                },
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["structured_output"] == loop.structured_submission
    assert response.json()["tool_receipts"] == [
        {
            "tool": "mcp_resolution",
            "structured_content": presentation,
            "auth_context_receipt": loop.auth_context_receipt,
        }
    ]
    assert loop.registry.get("submit_room_turn") is None
