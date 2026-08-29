from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mcp import types
from mcp.client import ClientSession
from mcp.client.extension import ClaimContext
from mcp.shared.exceptions import MCPError

from nanobot.agent.mcp_tasks import (
    TASKS_EXTENSION_ID,
    CancelTaskRequest,
    CreateTaskResult,
    DetailedTaskResult,
    GetTaskRequest,
    TaskAcknowledgement,
    TaskAuthorizationContext,
    TasksExtension,
    TaskTimeoutControl,
    UpdateTaskRequest,
    resolve_task_result,
    task_authorization_context,
    task_timeout_context,
)
from nanobot.config.schema import MCPServerConfig


def _task(**overrides):
    payload = {
        "resultType": "task",
        "taskId": "task-1",
        "status": "working",
        "createdAt": "2026-08-29T00:00:00Z",
        "lastUpdatedAt": "2026-08-29T00:00:00Z",
        "ttlMs": 60_000,
        "pollIntervalMs": 0,
    }
    payload.update(overrides)
    return CreateTaskResult.model_validate(payload)


def _detailed(status: str, **overrides):
    payload = {
        "resultType": "complete",
        "taskId": "task-1",
        "status": status,
        "createdAt": "2026-08-29T00:00:00Z",
        "lastUpdatedAt": "2026-08-29T00:00:01Z",
        "ttlMs": 60_000,
        "pollIntervalMs": 0,
    }
    payload.update(overrides)
    return DetailedTaskResult.model_validate(payload)


class _Session:
    def __init__(self, responses):
        self.server_capabilities = types.ServerCapabilities(
            extensions={TASKS_EXTENSION_ID: {}}
        )
        self.responses = list(responses)
        self.requests = []

    async def send_request(self, request, result_type, request_read_timeout_seconds=None):
        self.requests.append((request, result_type, request_read_timeout_seconds))
        return self.responses.pop(0)


def _ctx(session):
    return ClaimContext(session=session, tool_name="render_grid", read_timeout_seconds=3)


def _authorization(**overrides) -> TaskAuthorizationContext:
    values = {
        "secret": "delegation-test-secret-at-least-32-bytes",
        "issuer": "sagasmith-web",
        "target_service": "sagasmith-dnd-mcp",
        "caller_principal": "workload:sagasmith-agent",
        "workload_identity": "sagasmith-agent-hosted-worker",
        "requester_principal": "player:alice",
        "resource_owner_principal": "player:owner",
        "acting_host_principal": "workload:sagasmith-agent",
        "acting_character_id": "hero-1",
        "authorized_audience": "sagasmith-dnd-mcp",
        "conversation_principal": "room:table-1",
        "tenant_id": "tenant-1",
        "campaign_id": "campaign-1",
        "room_turn_id": "turn-1",
        "base_revision": 7,
        "hard_expires_at": datetime.now(UTC) + timedelta(minutes=10),
        "trace_meta": {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    }
    values.update(overrides)
    return TaskAuthorizationContext(**values)


async def _resolve(session, task=None):
    with task_authorization_context(_authorization()):
        return await resolve_task_result(task or _task(), _ctx(session))


def test_tasks_extension_claims_only_modern_task_results() -> None:
    claim = TasksExtension().claims()[0]
    assert claim.result_type == "task"
    assert claim.protocol_versions == frozenset({"2026-07-28"})
    assert claim.model is CreateTaskResult


def test_task_timeout_config_is_bounded_and_uses_public_alias() -> None:
    config = MCPServerConfig.model_validate({"taskTimeout": 1_200})

    assert config.task_timeout == 1_200
    assert config.model_dump(by_alias=True)["taskTimeout"] == 1_200
    with pytest.raises(ValueError):
        MCPServerConfig.model_validate({"taskTimeout": 0})


@pytest.mark.asyncio
async def test_task_polling_returns_standard_call_tool_result() -> None:
    expected = types.CallToolResult(
        content=[types.TextContent(type="text", text="rendered")],
        structuredContent={"artifact_id": "grid-1"},
    )
    session = _Session(
        [
            _detailed("working"),
            _detailed("completed", result=expected.model_dump(by_alias=True, exclude_none=True)),
        ]
    )

    result = await _resolve(session)

    assert result == expected
    assert all(isinstance(item[0], GetTaskRequest) for item in session.requests)
    auths = [item[0].params.meta["sagasmith_auth_context"] for item in session.requests]
    assert all(auth["allowed_operations"] == ["tasks/get"] for auth in auths)
    assert all(auth["authorized_audience"] == "sagasmith-dnd-mcp" for auth in auths)
    assert all(auth["requester_principal"] == "player:alice" for auth in auths)
    assert all(auth["resource_owner_principal"] == "player:owner" for auth in auths)
    assert all(auth["acting_host_principal"] == "workload:sagasmith-agent" for auth in auths)
    assert all(auth["campaign_id"] == "campaign-1" for auth in auths)
    assert all(auth["room_turn_id"] == "turn-1" for auth in auths)
    assert all(auth["base_revision"] == 7 for auth in auths)
    assert auths[0]["nonce"] != auths[1]["nonce"]
    assert auths[0]["signature"] != auths[1]["signature"]


@pytest.mark.asyncio
async def test_claimed_task_replaces_short_tool_deadline() -> None:
    expected = types.CallToolResult(
        content=[types.TextContent(type="text", text="completed after short deadline")]
    )
    session = _Session(
        [_detailed("completed", result=expected.model_dump(by_alias=True, exclude_none=True))]
    )

    async with asyncio.timeout(0.01) as short_timeout:
        control = TaskTimeoutControl(short_timeout, task_timeout_seconds=1)
        with (
            task_authorization_context(_authorization()),
            task_timeout_context(control),
        ):
            result = await resolve_task_result(_task(), _ctx(session))

    assert control.claimed is True
    assert result == expected


@pytest.mark.asyncio
async def test_task_failure_preserves_json_rpc_error() -> None:
    session = _Session(
        [_detailed("failed", error={"code": -32001, "message": "render failed"})]
    )

    with pytest.raises(MCPError, match="render failed"):
        await _resolve(session)


@pytest.mark.asyncio
async def test_task_cancelled_is_structured_tool_error() -> None:
    session = _Session([_detailed("cancelled", statusMessage="cancelled by host")])

    result = await _resolve(session)

    assert result.is_error is True
    assert result.structured_content["error"] == {
        "code": "task_cancelled",
        "message": "cancelled by host",
        "retryable": False,
        "task_id": "task-1",
    }


@pytest.mark.asyncio
async def test_host_cancellation_sends_tasks_cancel() -> None:
    session = _Session([SimpleNamespace()])
    started = asyncio.Event()

    async def blocked_send(request, result_type, request_read_timeout_seconds=None):
        session.requests.append((request, result_type, request_read_timeout_seconds))
        if isinstance(request, GetTaskRequest):
            started.set()
            await asyncio.Event().wait()
        return SimpleNamespace()

    session.send_request = blocked_send
    running = asyncio.create_task(_resolve(session))
    await started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    cancel = next(item[0] for item in session.requests if isinstance(item[0], CancelTaskRequest))
    auth = cancel.params.meta["sagasmith_auth_context"]
    assert auth["allowed_operations"] == ["tasks/cancel"]
    assert auth["target_service"] == "sagasmith-dnd-mcp"


@pytest.mark.asyncio
async def test_input_required_dispatches_and_updates_before_polling() -> None:
    request = types.ListRootsRequest(params=types.RequestParams())
    session = _Session(
        [
            _detailed("input_required", inputRequests={"input-1": request}),
            SimpleNamespace(),
            _detailed("input_required", inputRequests={"input-1": request}),
            _detailed(
                "completed",
                result=types.CallToolResult(
                    content=[types.TextContent(type="text", text="done")]
                ).model_dump(by_alias=True, exclude_none=True),
            ),
        ]
    )

    async def dispatch(_ctx, received):
        assert received == request
        return types.ListRootsResult(roots=[])

    session.dispatch_input_request = dispatch
    result = await _resolve(session)

    assert result.content[0].text == "done"
    update = session.requests[1][0]
    assert isinstance(update, UpdateTaskRequest)
    assert update.params.meta["sagasmith_auth_context"]["allowed_operations"] == [
        "tasks/update"
    ]
    assert sum(isinstance(item[0], UpdateTaskRequest) for item in session.requests) == 1


@pytest.mark.asyncio
async def test_task_claim_rejects_server_without_advertised_capability() -> None:
    session = _Session([])
    session.server_capabilities = types.ServerCapabilities(extensions={})

    with pytest.raises(RuntimeError, match="without advertising"):
        await _resolve(session)


@pytest.mark.asyncio
async def test_task_followup_requires_trusted_host_context() -> None:
    session = _Session([_detailed("working")])

    with pytest.raises(PermissionError, match="no trusted Host authorization"):
        await resolve_task_result(_task(), _ctx(session))


def test_fresh_task_delegation_cannot_outlive_host_expiry() -> None:
    hard_expiry = datetime.now(UTC) + timedelta(seconds=30)
    meta = _authorization(hard_expires_at=hard_expiry).fresh_meta("tasks/get")

    signed_expiry = datetime.fromisoformat(
        meta["sagasmith_auth_context"]["expires_at"].replace("Z", "+00:00")
    )
    assert signed_expiry <= hard_expiry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_request",
    [
        GetTaskRequest(params={"taskId": "task/header value"}),
        UpdateTaskRequest(params={"taskId": "task/header value", "inputResponses": {}}),
        CancelTaskRequest(params={"taskId": "task/header value"}),
    ],
)
async def test_task_requests_emit_task_id_as_mcp_name_header(task_request) -> None:
    class Dispatcher:
        def __init__(self) -> None:
            self.options = None

        async def send_raw_request(self, _method, _params, options):
            self.options = options
            return {"resultType": "complete"}

    dispatcher = Dispatcher()
    session = object.__new__(ClientSession)
    session._dispatcher = dispatcher
    session._session_read_timeout_seconds = None
    session._negotiated_version = "2026-07-28"

    def stamp(_data, options):
        options.setdefault("headers", {})["Mcp-Protocol-Version"] = "2026-07-28"

    session._stamp = stamp
    await session.send_request(task_request, TaskAcknowledgement)

    assert dispatcher.options["headers"]["mcp-name"] == "task/header value"
