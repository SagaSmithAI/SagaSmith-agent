from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from nanobot.agent.mcp_observability import record_mcp_event
from nanobot.agent.tools.base import ToolResult
from nanobot.apps.hosted_worker import create_worker_app
from nanobot.bus.events import HostMediaEnvelope

TOKEN = "hosted-worker-test-token-at-least-32-bytes"


def trusted_context(**overrides):
    value = {
        "caller_principal": "workload:web:room-worker",
        "workload_identity": "spiffe://sagasmith/web/room-worker",
        "requester_principal": "discord:user:account-id",
        "resource_owner_principal": "discord:user:owner",
        "acting_host_principal": "campaign:gm",
        "acting_character_id": "hero",
        "authorized_audience": "player",
        "allowed_operations": ["actor_query", "resolution"],
        "room_turn_id": "turn-1",
        "campaign_id": "campaign",
        "system_id": "dnd5e",
        "base_revision": 3,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "idempotency_key": "turn-1:operation-1",
        "conversation_principal": "discord:group:table-1",
    }
    value.update(overrides)
    return value


def request_json(**overrides):
    value = {
        "messages": [{"role": "user", "content": "hello"}],
        "session_id": "campaign:user:conversation",
        "trusted_context": trusted_context(),
    }
    value.update(overrides)
    return value


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

    @property
    def tool_names(self):
        return list(self.tools)

    def clone(self):
        cloned = FakeRegistry()
        cloned.tools = dict(self.tools)
        return cloned

    def live_clone(self):
        return self.clone()


class FakeLoop:
    def __init__(self) -> None:
        self.calls = []
        self._last_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        self.registry = FakeRegistry()
        for operation in ("actor_query", "resolution"):
            self.registry.register(
                SimpleNamespace(
                    name=f"mcp_dnd_{operation}",
                    _server_name="sagasmith_dnd",
                    _original_name=operation,
                    _model_visible=True,
                )
            )
        self.structured_submission = None
        self.structured_tool_results = []
        self.auth_context_receipt = None

    async def _connect_mcp(self) -> None:
        return None

    async def close_mcp(self) -> None:
        return None

    async def _tools_for_session(self, _session_key: str, *, system_id: str | None = None):
        self.selected_system = system_id
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
                        mcp_result={"content": [{"type": "text", "text": "ok"}]},
                        media_envelopes=(),
                    ),
                )
        return SimpleNamespace(
            content="ok",
            metadata={"_agent_usage": self._last_usage},
        )


def test_hosted_worker_exports_only_low_cardinality_mcp_metrics() -> None:
    record_mcp_event("tool", "ok", transport="http", protocol="2026-07-28")
    app = create_worker_app(FakeLoop(), "test-model", service_token=TOKEN)

    with TestClient(app) as client:
        response = client.get("/metrics/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "sagasmith.host-mcp-metrics/v1"
    counter = next(
        item
        for item in body["counters"]
        if item["phase"] == "tool"
        and item["outcome"] == "ok"
        and item["transport"] == "http"
        and item["protocol"] == "2026-07-28"
    )
    assert counter["count"] >= 1
    assert set(counter) == {"phase", "outcome", "transport", "protocol", "count"}


def test_hosted_worker_injects_authenticated_principal_as_sender() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(),
        )
    assert response.status_code == 200
    assert loop.calls[0]["channel"] == "service"
    assert loop.calls[0]["sender_id"] == "discord:user:account-id"
    assert loop.calls[0]["actor_principal"] == "discord:user:account-id"
    assert loop.calls[0]["conversation_principal"] == "discord:group:table-1"
    assert loop.calls[0]["trusted_metadata"]["room_turn_id"] == "turn-1"
    assert loop.selected_system == "dnd5e"
    assert response.json()["usage"]["total_tokens"] == 5


def test_hosted_worker_selects_only_authorized_mcp_operations_for_model() -> None:
    loop = FakeLoop()
    loop.registry.register(SimpleNamespace(name="builtin", _model_visible=True))
    loop.registry.register(
        SimpleNamespace(
            name="mcp_dnd_actor_query",
            _server_name="sagasmith_dnd",
            _original_name="actor_query",
            _model_visible=True,
        )
    )
    loop.registry.register(
        SimpleNamespace(
            name="mcp_dnd_campaign_delete",
            _server_name="sagasmith_dnd",
            _original_name="campaign_delete",
            _model_visible=True,
        )
    )
    app = create_worker_app(loop, "test-model", service_token=TOKEN)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(
                trusted_context=trusted_context(allowed_operations=["actor_query"])
            ),
        )
        metrics = client.get("/metrics/mcp").json()

    assert response.status_code == 200
    selected = loop.calls[0]["tools"]
    assert selected.has("builtin")
    assert selected.has("mcp_dnd_actor_query")
    # The live catalog stays intact for legacy list_changed updates; request-context
    # availability keeps the unselected operation out of model definitions and calls.
    assert selected.has("mcp_dnd_campaign_delete")
    assert any(
        item["candidate_bucket"] == "1-7"
        and item["selected_bucket"] == "1-7"
        and item["count"] >= 1
        for item in metrics["catalog_selections"]
    )


def test_hosted_worker_rejects_untrusted_principal_shape() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(
                trusted_context=trusted_context(allowed_operations=["*"])
            ),
        )
    assert response.status_code == 422


def test_hosted_worker_rejects_web_policy_groups_instead_of_tool_ids() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(
                trusted_context=trusted_context(
                    allowed_operations=[
                        "campaign.read",
                        "mechanics.resolve",
                        "campaign.write",
                    ]
                )
            ),
        )

    assert response.status_code == 422
    assert "absent from the authorized MCP catalog" in response.json()["detail"]


def test_hosted_worker_accepts_web_envelope_with_exact_facade_tool_ids() -> None:
    loop = FakeLoop()
    loop.registry.register(
        SimpleNamespace(
            name="mcp_dnd_campaign_query",
            _server_name="sagasmith_dnd",
            _original_name="campaign_query",
            _model_visible=True,
        )
    )
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(
                trusted_context=trusted_context(
                    allowed_operations=["actor_query", "campaign_query", "resolution"]
                )
            ),
        )

    assert response.status_code == 200
    assert loop.calls[0]["trusted_metadata"]["allowed_operations"] == [
        "actor_query",
        "campaign_query",
        "resolution",
    ]


def test_hosted_worker_rejects_missing_dedicated_service_credential() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post("/v1/chat/completions", json=request_json())
    assert response.status_code == 401


def test_hosted_worker_injects_agent_identity_principal() -> None:
    loop = FakeLoop()
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(
                messages=[{"role": "user", "content": "host this scene"}],
                trusted_context=trusted_context(
                    requester_principal="service:agent:identity-id"
                ),
            ),
        )
    assert response.status_code == 200
    assert loop.calls[0]["channel"] == "service"
    assert loop.calls[0]["sender_id"] == "service:agent:identity-id"


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
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(messages=[{"role": "user", "content": "query actors"}]),
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
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(
                messages=[{"role": "user", "content": "host this turn"}],
                response_contract={
                    "name": "submit_room_turn",
                    "description": "Submit.",
                    "parameters": {"type": "object"},
                },
            ),
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


@pytest.mark.asyncio
async def test_hosted_worker_allows_independent_sessions_to_run_concurrently() -> None:
    class ConcurrentLoop(FakeLoop):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum_active = 0

        async def process_direct(self, **arguments):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            return SimpleNamespace(content="ok", metadata={"_agent_usage": {}})

    loop = ConcurrentLoop()
    app = create_worker_app(loop, "test-model", service_token=TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://worker.test",
    ) as client:
        first, second = await asyncio.gather(
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json=request_json(session_id="session-a"),
            ),
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json=request_json(session_id="session-b"),
            ),
        )
    assert first.status_code == second.status_code == 200
    assert loop.maximum_active == 2


def test_hosted_worker_returns_standard_mcp_result_and_media_envelope() -> None:
    class MediaLoop(FakeLoop):
        async def process_direct(self, **arguments):
            result = ToolResult(
                "grid",
                mcp_result={
                    "content": [
                        {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"}
                    ],
                    "isError": False,
                },
                media_envelopes=[
                    HostMediaEnvelope(
                        path="D:/worker/artifacts/grid.png",
                        mime_type="image/png",
                        attachment_role="combat_grid",
                        audience_projection="party_public",
                    )
                ],
            )
            for hook in arguments["hooks"]:
                await hook.after_execute_tool(
                    None,
                    SimpleNamespace(name="mcp_dnd_render_grid"),
                    None,
                    None,
                    result,
                )
            return SimpleNamespace(content="ok", metadata={"_agent_usage": {}})

    loop = MediaLoop()
    with TestClient(create_worker_app(loop, "test-model", service_token=TOKEN)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=request_json(),
        )
    payload = response.json()
    assert payload["mcp_results"][0]["result"]["content"][0]["type"] == "image"
    assert payload["host_media"][0]["attachment_role"] == "combat_grid"
