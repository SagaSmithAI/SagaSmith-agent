"""SEP-2663 MCP Tasks extension support for modern Host connections."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from mcp import types
from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.client.session import ClientRequestContext
from mcp.shared.exceptions import MCPError
from pydantic import Field, model_validator

from nanobot.agent.auth_context import (
    AUTH_CONTEXT_DELEGATION_SCHEMA,
    AUTH_CONTEXT_META_KEY,
    sign_delegated_auth_context,
)
from nanobot.agent.mcp_observability import record_mcp_event

TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
MODERN_TASKS_PROTOCOLS = frozenset({"2026-07-28"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_DEFAULT_POLL_INTERVAL_MS = 1_000
_MIN_POLL_INTERVAL_MS = 50
_MAX_POLL_INTERVAL_MS = 30_000
_CANCEL_TIMEOUT_SECONDS = 2.0
_FOLLOWUP_DELEGATION_LIFETIME = timedelta(minutes=5)
_TRACE_META_KEYS = frozenset({"traceparent", "tracestate", "baggage"})

TaskStatus = Literal["working", "input_required", "completed", "cancelled", "failed"]


@dataclass(frozen=True)
class TaskAuthorizationContext:
    """Trusted facts retained by the Host while resolving one opaque task handle."""

    secret: str
    issuer: str
    target_service: str
    caller_principal: str
    workload_identity: str
    requester_principal: str
    resource_owner_principal: str
    acting_host_principal: str
    acting_character_id: str
    authorized_audience: str
    conversation_principal: str
    tenant_id: str
    campaign_id: str
    room_turn_id: str
    base_revision: int
    hard_expires_at: datetime | None
    trace_meta: Mapping[str, str]

    def fresh_meta(self, operation: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        if self.hard_expires_at is not None and self.hard_expires_at <= now:
            raise PermissionError("trusted Host delegation expired while polling MCP task")
        expires_at = now + _FOLLOWUP_DELEGATION_LIFETIME
        if self.hard_expires_at is not None:
            expires_at = min(expires_at, self.hard_expires_at)
        return {
            AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
                secret=self.secret,
                issuer=self.issuer,
                target_service=self.target_service,
                caller_principal=self.caller_principal,
                workload_identity=self.workload_identity,
                requester_principal=self.requester_principal,
                resource_owner_principal=self.resource_owner_principal,
                acting_host_principal=self.acting_host_principal,
                acting_character_id=self.acting_character_id,
                authorized_audience=self.authorized_audience,
                allowed_operations=(operation,),
                conversation_principal=self.conversation_principal,
                tenant_id=self.tenant_id,
                campaign_id=self.campaign_id,
                room_turn_id=self.room_turn_id,
                base_revision=self.base_revision,
                issued_at=now,
                expires_at=expires_at,
            ),
            **self.trace_meta,
        }


@dataclass
class TaskTimeoutControl:
    """Lets a claimed task extend the short synchronous tool deadline exactly once."""

    timeout: asyncio.Timeout
    task_timeout_seconds: int
    claimed: bool = False

    def claim(self) -> None:
        if self.claimed:
            return
        self.claimed = True
        self.timeout.reschedule(
            asyncio.get_running_loop().time() + self.task_timeout_seconds
        )


_CURRENT_TASK_AUTHORIZATION: ContextVar[TaskAuthorizationContext | None] = ContextVar(
    "mcp_task_authorization",
    default=None,
)
_CURRENT_TASK_TIMEOUT: ContextVar[TaskTimeoutControl | None] = ContextVar(
    "mcp_task_timeout",
    default=None,
)


@contextmanager
def task_authorization_context(
    authorization: TaskAuthorizationContext | None,
) -> Iterator[None]:
    token = _CURRENT_TASK_AUTHORIZATION.set(authorization)
    try:
        yield
    finally:
        _CURRENT_TASK_AUTHORIZATION.reset(token)


@contextmanager
def task_timeout_context(control: TaskTimeoutControl) -> Iterator[None]:
    token = _CURRENT_TASK_TIMEOUT.set(control)
    try:
        yield
    finally:
        _CURRENT_TASK_TIMEOUT.reset(token)


def task_authorization_from_meta(
    *,
    secret: str,
    meta: Mapping[str, Any] | None,
    hard_expires_at: str | None,
) -> TaskAuthorizationContext | None:
    """Retain only verified Host facts; never retain or reuse the original signature."""

    auth = meta.get(AUTH_CONTEXT_META_KEY) if isinstance(meta, Mapping) else None
    if not isinstance(auth, Mapping) or auth.get("schema") != AUTH_CONTEXT_DELEGATION_SCHEMA:
        return None
    expiry = None
    if hard_expires_at:
        expiry = datetime.fromisoformat(hard_expires_at.replace("Z", "+00:00")).astimezone(UTC)
    return TaskAuthorizationContext(
        secret=secret,
        issuer=str(auth["issuer"]),
        target_service=str(auth["target_service"]),
        caller_principal=str(auth["caller_principal"]),
        workload_identity=str(auth["workload_identity"]),
        requester_principal=str(auth["requester_principal"]),
        resource_owner_principal=str(auth["resource_owner_principal"]),
        acting_host_principal=str(auth["acting_host_principal"]),
        acting_character_id=str(auth.get("acting_character_id") or ""),
        authorized_audience=str(auth["authorized_audience"]),
        conversation_principal=str(auth["conversation_principal"]),
        tenant_id=str(auth.get("tenant_id") or ""),
        campaign_id=str(auth["campaign_id"]),
        room_turn_id=str(auth["room_turn_id"]),
        base_revision=int(auth["base_revision"]),
        hard_expires_at=expiry,
        trace_meta={
            key: str(meta[key])
            for key in _TRACE_META_KEYS
            if isinstance(meta.get(key), str) and str(meta[key]).strip()
        },
    )


class CreateTaskResult(types.Result):
    """Server-directed task handle returned instead of a normal tools/call result."""

    result_type: Literal["task"] = "task"
    task_id: str = Field(min_length=1, max_length=512)
    status: TaskStatus
    status_message: str | None = None
    created_at: str
    last_updated_at: str
    ttl_ms: int | None = Field(default=None, ge=0)
    poll_interval_ms: int | None = Field(default=None, ge=0)


class DetailedTaskResult(types.Result):
    """SEP-2663 tasks/get response, including inlined terminal/input payloads."""

    result_type: Literal["complete"] = "complete"
    task_id: str = Field(min_length=1, max_length=512)
    status: TaskStatus
    status_message: str | None = None
    created_at: str
    last_updated_at: str
    ttl_ms: int | None = Field(default=None, ge=0)
    poll_interval_ms: int | None = Field(default=None, ge=0)
    input_requests: types.InputRequests | None = None
    result: dict[str, Any] | None = None
    error: types.ErrorData | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "DetailedTaskResult":
        if self.status == "completed" and self.result is None:
            raise ValueError("completed task is missing its result")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed task is missing its JSON-RPC error")
        if self.status == "input_required" and self.input_requests is None:
            raise ValueError("input_required task is missing inputRequests")
        return self


class _TaskIdParams(types.RequestParams):
    task_id: str = Field(min_length=1, max_length=512)


class GetTaskRequest(types.Request[_TaskIdParams, Literal["tasks/get"]]):
    name_param = "taskId"
    method: Literal["tasks/get"] = "tasks/get"
    params: _TaskIdParams


class CancelTaskRequest(types.Request[_TaskIdParams, Literal["tasks/cancel"]]):
    name_param = "taskId"
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: _TaskIdParams


class _UpdateTaskParams(_TaskIdParams):
    input_responses: types.InputResponses


class UpdateTaskRequest(types.Request[_UpdateTaskParams, Literal["tasks/update"]]):
    name_param = "taskId"
    method: Literal["tasks/update"] = "tasks/update"
    params: _UpdateTaskParams


class TaskAcknowledgement(types.Result):
    result_type: Literal["complete"] = "complete"


def _poll_delay_seconds(value: int | None) -> float:
    interval_ms = value if value is not None else _DEFAULT_POLL_INTERVAL_MS
    return min(max(interval_ms, _MIN_POLL_INTERVAL_MS), _MAX_POLL_INTERVAL_MS) / 1_000


def _server_advertises_tasks(session: Any) -> bool:
    capabilities = getattr(session, "server_capabilities", None)
    extensions = getattr(capabilities, "extensions", None)
    return isinstance(extensions, Mapping) and TASKS_EXTENSION_ID in extensions


async def _cancel_task(session: Any, task_id: str, read_timeout_seconds: float | None) -> None:
    authorization = _CURRENT_TASK_AUTHORIZATION.get()
    if authorization is None:
        raise PermissionError("MCP task follow-up has no trusted Host authorization context")
    await session.send_request(
        CancelTaskRequest(
            params=_TaskIdParams(
                taskId=task_id,
                _meta=authorization.fresh_meta("tasks/cancel"),
            )
        ),
        TaskAcknowledgement,
        request_read_timeout_seconds=read_timeout_seconds,
    )


async def _dispatch_task_inputs(
    task: DetailedTaskResult,
    ctx: ClaimContext,
    responded_input_keys: set[str],
) -> None:
    authorization = _CURRENT_TASK_AUTHORIZATION.get()
    if authorization is None:
        raise PermissionError("MCP task follow-up has no trusted Host authorization context")
    responses: types.InputResponses = {}
    for key, request in (task.input_requests or {}).items():
        if key in responded_input_keys:
            continue
        request_ctx = ClientRequestContext(
            session=ctx.session,
            request_id=key,
            meta=request.params.meta if request.params else None,
        )
        response = await ctx.session.dispatch_input_request(request_ctx, request)
        if isinstance(response, types.ErrorData):
            raise MCPError.from_error_data(response)
        responses[key] = response
    if not responses:
        return
    await ctx.session.send_request(
        UpdateTaskRequest(
            params=_UpdateTaskParams(
                taskId=task.task_id,
                inputResponses=responses,
                _meta=authorization.fresh_meta("tasks/update"),
            )
        ),
        TaskAcknowledgement,
        request_read_timeout_seconds=ctx.read_timeout_seconds,
    )
    responded_input_keys.update(responses)


def _cancelled_result(task: DetailedTaskResult) -> types.CallToolResult:
    message = task.status_message or "MCP task was cancelled"
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=message)],
        structuredContent={
            "error": {
                "code": "task_cancelled",
                "message": message,
                "retryable": False,
                "task_id": task.task_id,
            }
        },
    )


async def resolve_task_result(task: CreateTaskResult, ctx: ClaimContext) -> types.CallToolResult:
    """Drive one server-created task until completion while preserving cancellation."""

    if not _server_advertises_tasks(ctx.session):
        raise RuntimeError(
            "MCP server returned a task without advertising io.modelcontextprotocol/tasks"
        )
    authorization = _CURRENT_TASK_AUTHORIZATION.get()
    if authorization is None:
        raise PermissionError("MCP task follow-up has no trusted Host authorization context")
    timeout_control = _CURRENT_TASK_TIMEOUT.get()
    if timeout_control is not None:
        timeout_control.claim()
    task_id = task.task_id
    poll_interval_ms = task.poll_interval_ms
    responded_input_keys: set[str] = set()
    try:
        while True:
            await asyncio.sleep(_poll_delay_seconds(poll_interval_ms))
            current = await ctx.session.send_request(
                GetTaskRequest(
                    params=_TaskIdParams(
                        taskId=task_id,
                        _meta=authorization.fresh_meta("tasks/get"),
                    )
                ),
                DetailedTaskResult,
                request_read_timeout_seconds=ctx.read_timeout_seconds,
            )
            poll_interval_ms = current.poll_interval_ms
            if current.status == "completed":
                assert current.result is not None
                record_mcp_event("task", "ok", protocol="2026-07-28")
                return types.CallToolResult.model_validate(current.result)
            if current.status == "failed":
                assert current.error is not None
                record_mcp_event("task", "error", protocol="2026-07-28")
                raise MCPError.from_error_data(current.error)
            if current.status == "cancelled":
                record_mcp_event("task", "cancelled", protocol="2026-07-28")
                return _cancelled_result(current)
            if current.status == "input_required":
                await _dispatch_task_inputs(current, ctx, responded_input_keys)
            elif current.status not in _TERMINAL_STATUSES and current.status != "working":
                raise RuntimeError(f"Unsupported MCP task status: {current.status}")
    except asyncio.CancelledError:
        record_mcp_event("task", "cancelled", protocol="2026-07-28")
        cancel = asyncio.create_task(
            _cancel_task(ctx.session, task_id, ctx.read_timeout_seconds),
            name=f"mcp-task-cancel:{task_id[:32]}",
        )
        with suppress(Exception, asyncio.CancelledError):
            async with asyncio.timeout(_CANCEL_TIMEOUT_SECONDS):
                await asyncio.shield(cancel)
        raise


class TasksExtension(ClientExtension):
    """Public SDK extension claim for SEP-2663 server-directed tool tasks."""

    identifier = TASKS_EXTENSION_ID

    def claims(self) -> tuple[ResultClaim[CreateTaskResult], ...]:
        return (
            ResultClaim(
                result_type="task",
                model=CreateTaskResult,
                resolve=resolve_task_result,
                protocol_versions=MODERN_TASKS_PROTOCOLS,
            ),
        )
