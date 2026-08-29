"""Service-owned HTTP application around the shared SagaSmith Agent core."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nanobot.agent.mcp_observability import (
    mcp_catalog_selection_snapshot,
    mcp_metrics_snapshot,
    record_mcp_catalog_selection,
)
from nanobot.apps.hosted_workspace import (
    HostedWorkspaceLease,
    HostedWorkspacePolicy,
    derive_workspace_owner,
)


class WorkerTrustedContext(BaseModel):
    """Web-issued authority data, kept structurally separate from player text."""

    model_config = ConfigDict(extra="forbid")
    caller_principal: str = Field(min_length=1, max_length=300)
    workload_identity: str = Field(min_length=1, max_length=300)
    requester_principal: str = Field(min_length=1, max_length=300)
    resource_owner_principal: str = Field(min_length=1, max_length=300)
    acting_host_principal: str = Field(min_length=1, max_length=300)
    acting_character_id: str = Field(default="", max_length=300)
    authorized_audience: str = Field(min_length=1, max_length=300)
    allowed_operations: list[str] = Field(min_length=1, max_length=100)
    room_turn_id: str = Field(min_length=1, max_length=300)
    campaign_id: str = Field(min_length=1, max_length=300)
    system_id: str = Field(min_length=1, max_length=100)
    base_revision: int = Field(ge=0)
    expires_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=300)
    conversation_principal: str = Field(min_length=1, max_length=300)
    tenant_id: str = Field(default="", max_length=300)
    traceparent: str = Field(default="", max_length=512)
    tracestate: str = Field(default="", max_length=2048)
    baggage: str = Field(default="", max_length=8192)

    @field_validator("allowed_operations")
    @classmethod
    def concrete_operations(cls, value: list[str]) -> list[str]:
        operations = [item.strip() for item in value]
        if any(not item or item == "*" for item in operations):
            raise ValueError("allowed_operations must enumerate concrete operations")
        if len(set(operations)) != len(operations):
            raise ValueError("allowed_operations must not contain duplicates")
        return sorted(operations)


class WorkerCompletionRequest(BaseModel):
    """A player message plus a separately authenticated Host authority envelope."""

    model_config = ConfigDict(extra="forbid")
    messages: list[dict[str, Any]]
    session_id: str = Field(min_length=1, max_length=300)
    trusted_context: WorkerTrustedContext
    stream: bool = False
    response_contract: dict[str, Any] | None = None
    terminal: bool = False


def _validate_trusted_context(context: WorkerTrustedContext) -> None:
    now = datetime.now(UTC)
    expiry = context.expires_at.astimezone(UTC)
    if expiry <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "trusted context has expired")
    if expiry - now > timedelta(minutes=15):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "trusted context lifetime exceeds 15 minutes",
        )
    if context.requester_principal == context.workload_identity:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "requester and workload identities must remain distinct",
        )


def _trusted_metadata(context: WorkerTrustedContext) -> dict[str, Any]:
    return {
        "caller_principal": context.caller_principal,
        "workload_identity": context.workload_identity,
        "requester_principal": context.requester_principal,
        "resource_owner_principal": context.resource_owner_principal,
        "acting_host_principal": context.acting_host_principal,
        "acting_character_ref": context.acting_character_id,
        "authorized_audience": context.authorized_audience,
        "allowed_operations": list(context.allowed_operations),
        "room_turn_id": context.room_turn_id,
        "campaign_id": context.campaign_id,
        "system_id": context.system_id,
        "base_revision": context.base_revision,
        "delegation_expires_at": context.expires_at.astimezone(UTC).isoformat(),
        "idempotency_key": context.idempotency_key,
        "tenant_id": context.tenant_id,
        "traceparent": context.traceparent,
        "tracestate": context.tracestate,
        "baggage": context.baggage,
        "principal_source": "trusted-host",
    }


def create_worker_app(
    agent_loop: Any,
    model_name: str,
    *,
    service_token: str,
    workspace_lease: HostedWorkspaceLease | None = None,
) -> FastAPI:
    """Create a worker whose MCP connections and turn state are system/session scoped."""

    if len(service_token.encode("utf-8")) < 32:
        raise ValueError("Hosted Worker service token must contain at least 32 bytes")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http_client = httpx.AsyncClient(timeout=10)
        if workspace_lease is not None:
            workspace_lease.register()
            HostedWorkspaceLease.cleanup_registered(workspace_lease.policy)
        try:
            yield
        finally:
            await agent_loop.close_mcp()
            await app.state.http_client.aclose()
            if workspace_lease is not None:
                workspace_lease.enforce_capacity()

    app = FastAPI(title="SagaSmith Hosted Agent Worker", docs_url=None, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics/mcp")
    def mcp_metrics() -> dict[str, Any]:
        """Expose bounded Host counters without request or domain identifiers."""

        return {
            "schema": "sagasmith.host-mcp-metrics/v1",
            "counters": mcp_metrics_snapshot(),
            "catalog_selections": mcp_catalog_selection_snapshot(),
        }

    @app.post("/v1/chat/completions")
    async def complete(
        payload: WorkerCompletionRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        scheme, _, credential = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not hmac.compare_digest(credential, service_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid worker credential")
        if payload.stream:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "streaming is unsupported")
        if len(payload.messages) != 1 or payload.messages[0].get("role") != "user":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "one user message required")
        content = payload.messages[0].get("content")
        if not isinstance(content, str):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "text content required")
        trusted = payload.trusted_context
        _validate_trusted_context(trusted)
        if workspace_lease is not None:
            workspace_lease.touch()
            workspace_lease.enforce_capacity()

        from nanobot.agent.hook import AgentHook
        from nanobot.agent.resolution_presentation import normalize_resolution_presentation

        class ReceiptCaptureHook(AgentHook):
            def __init__(self) -> None:
                super().__init__()
                self.receipts: list[dict[str, Any]] = []
                self.mcp_results: list[dict[str, Any]] = []
                self.media: list[dict[str, Any]] = []
                self.bytes = 0

            async def after_execute_tool(
                self,
                _context: Any,
                tool_call: Any,
                _tool: Any,
                _params: Any,
                result: Any,
            ) -> None:
                tool_name = str(getattr(tool_call, "name", ""))[:160]
                entry: dict[str, Any] = {"tool": tool_name}
                audit_receipt = getattr(result, "audit_receipt", None)
                if isinstance(audit_receipt, dict):
                    entry["auth_context_receipt"] = dict(audit_receipt)
                try:
                    structured = normalize_resolution_presentation(
                        getattr(result, "structured_content", None)
                    )
                except ValueError:
                    structured = None
                if structured is not None:
                    entry["structured_content"] = structured
                standard_result = getattr(result, "mcp_result", None)
                if isinstance(standard_result, dict) and len(self.mcp_results) < 32:
                    self.mcp_results.append({"tool": tool_name, "result": standard_result})
                for envelope in getattr(result, "media_envelopes", ()):
                    if len(self.media) < 32:
                        self.media.append(asdict(envelope))
                if len(entry) == 1 or len(self.receipts) >= 32:
                    return
                try:
                    size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
                except (TypeError, ValueError):
                    return
                if size > 131_072 or self.bytes + size > 262_144:
                    return
                self.bytes += size
                self.receipts.append(entry)

        def build_activity_tool(activity_contract: dict[str, Any], callback: dict[str, Any]):
            from nanobot.agent.tools.base import Tool, ToolResult

            class RoomActivityTool(Tool):
                def __init__(self) -> None:
                    self._name = str(activity_contract["name"])
                    self._description = str(activity_contract["description"])
                    self._parameters = dict(activity_contract["parameters"])

                @property
                def name(self) -> str:
                    return self._name

                @property
                def description(self) -> str:
                    return self._description

                @property
                def parameters(self) -> dict[str, Any]:
                    return dict(self._parameters)

                @property
                def exclusive(self) -> bool:
                    return True

                async def execute(self, **kwargs: Any) -> ToolResult:
                    response = await app.state.http_client.post(
                        str(callback["url"]),
                        headers={"Authorization": f"Bearer {callback['token']}"},
                        json=kwargs,
                    )
                    if response.status_code >= 400:
                        return ToolResult.error("Room activity was rejected by the host.")
                    return ToolResult("Room activity accepted.")

            return RoomActivityTool()

        session_key = f"service:{payload.session_id}"
        base_tools = await agent_loop._tools_for_session(
            session_key,
            system_id=trusted.system_id,
        )
        turn_tools = base_tools.live_clone()
        allowed_operations = frozenset(trusted.allowed_operations)
        mcp_candidates = 0
        mcp_selected = 0
        catalog_operations: set[str] = set()
        for tool_name in list(turn_tools.tool_names):
            tool = turn_tools.get(tool_name)
            if tool is None or not getattr(tool, "_server_name", None):
                continue
            if not getattr(tool, "_model_visible", True):
                continue
            mcp_candidates += 1
            operation = getattr(tool, "_original_name", None)
            if isinstance(operation, str):
                catalog_operations.add(operation)
            if operation in allowed_operations:
                mcp_selected += 1
        unknown_operations = sorted(allowed_operations - catalog_operations)
        if unknown_operations:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "allowed_operations contains tool IDs absent from the authorized MCP catalog: "
                + ", ".join(unknown_operations),
            )
        if mcp_selected == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "allowed_operations selected no model-visible MCP tools",
            )
        record_mcp_catalog_selection(mcp_candidates, mcp_selected)
        structured_tool = None
        receipt_hook = ReceiptCaptureHook()
        if payload.response_contract is not None:
            contract = payload.response_contract
            try:
                from nanobot.agent.tools.structured_output import StructuredOutputTool

                terminal_contract = dict(contract.get("terminal") or contract)
                activity_contract = contract.get("activity")
                callback = dict(contract.get("activity_callback") or {})
                structured_tool = StructuredOutputTool(
                    name=str(terminal_contract["name"]),
                    description=str(terminal_contract["description"]),
                    parameters=dict(terminal_contract["parameters"]),
                )
                if turn_tools.has(structured_tool.name):
                    raise ValueError("structured response tool name is already registered")
                turn_tools.register(structured_tool)
                if activity_contract is not None:
                    if not callback.get("url") or not callback.get("token"):
                        raise ValueError("activity callback is incomplete")
                    activity_tool = build_activity_tool(activity_contract, callback)
                    if turn_tools.has(activity_tool.name):
                        raise ValueError("activity tool name is already registered")
                    turn_tools.register(activity_tool)
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "invalid structured response contract",
                ) from exc

        response = await agent_loop.process_direct(
            content=content,
            session_key=session_key,
            channel="service",
            sender_id=trusted.requester_principal,
            actor_principal=trusted.requester_principal,
            conversation_principal=trusted.conversation_principal,
            tools=turn_tools,
            hooks=[receipt_hook],
            trusted_metadata=_trusted_metadata(trusted),
        )
        if structured_tool is not None and structured_tool.submission is None:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "agent did not submit the required structured response",
            )
        if workspace_lease is not None:
            if payload.terminal:
                workspace_lease.terminate()
            else:
                workspace_lease.touch()
            workspace_lease.enforce_capacity()

        response_metadata = getattr(response, "metadata", {}) or {}
        usage = response_metadata.get("_agent_usage") or {}
        response_text = str(getattr(response, "content", response) or "")
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
            "structured_output": (
                structured_tool.submission if structured_tool is not None else None
            ),
            "tool_receipts": receipt_hook.receipts,
            "mcp_results": receipt_hook.mcp_results,
            "host_media": receipt_hook.media,
        }

    return app


def _parse_worker_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--workspace-id",
        required=True,
        help="stable opaque workspace identity issued by the trusted Host supervisor",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--workspace-ttl-seconds", type=int, default=86_400)
    parser.add_argument("--workspace-max-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--workspace-max-count", type=int, default=128)
    return parser.parse_args(arguments)


def _workspace_lease_from_arguments(
    workspace: Path, arguments: argparse.Namespace
) -> HostedWorkspaceLease:
    policy = HostedWorkspacePolicy(
        root=workspace.parent,
        ttl_seconds=arguments.workspace_ttl_seconds,
        max_bytes=arguments.workspace_max_bytes,
        max_workspaces=arguments.workspace_max_count,
    )
    return HostedWorkspaceLease(
        policy,
        workspace,
        owner=derive_workspace_owner(workspace, arguments.workspace_id),
    )


def main() -> None:
    arguments = _parse_worker_arguments()

    from nanobot.agent.hooks import create_file_edit_activity_hook
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import load_config, resolve_config_env_vars, set_config_path
    from nanobot.providers.image_generation import image_gen_provider_configs
    from nanobot.session.manager import SessionManager
    from nanobot.utils.helpers import sync_workspace_templates

    config_path = Path(arguments.config).resolve()
    workspace = Path(arguments.workspace).resolve()
    set_config_path(config_path)
    config = resolve_config_env_vars(load_config(config_path))
    if config.tools.distribution != "hosted":
        raise RuntimeError("Hosted Worker requires tools.distribution=hosted")
    config.agents.defaults.workspace = str(workspace)
    sync_workspace_templates(config.workspace_path)
    loop = AgentLoop.from_config(
        config,
        MessageBus(),
        session_manager=SessionManager(config.workspace_path),
        image_generation_provider_configs=image_gen_provider_configs(config),
        hook_factories=[create_file_edit_activity_hook],
    )
    lease = _workspace_lease_from_arguments(workspace, arguments)
    service_token = os.environ.get("SAGASMITH_WORKER_SERVICE_TOKEN", "")
    if len(service_token.encode("utf-8")) < 32:
        raise RuntimeError("SAGASMITH_WORKER_SERVICE_TOKEN must contain at least 32 bytes")
    model_name = config.resolve_preset().model
    uvicorn.run(
        create_worker_app(
            loop,
            model_name,
            service_token=service_token,
            workspace_lease=lease,
        ),
        host=arguments.host,
        port=arguments.port,
    )


if __name__ == "__main__":
    main()
