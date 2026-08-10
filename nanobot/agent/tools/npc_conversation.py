"""Host-side bridge from public MCP activations to isolated NPC workers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nanobot.agent.npc_conversation import (
    NpcConversationWorkerError,
    NpcConversationWorkerPool,
)
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.schema import ObjectSchema, StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            description="activate one NPC worker, release a conversation, or inspect worker status",
            enum=("activate", "release", "status"),
        ),
        campaign_id=StringSchema(description="MCP campaign id", max_length=100),
        conversation_id=StringSchema(description="MCP conversation id", max_length=100),
        activation=ObjectSchema(
            description="Public activation descriptor returned by the conversation MCP",
            additional_properties=True,
            nullable=True,
        ),
        mcp_server=StringSchema(
            description="Optional configured MCP server name when more than one server matches",
            max_length=100,
            nullable=True,
        ),
        required=["action", "conversation_id"],
    )
)
class NpcConversationWorkerTool(Tool):
    """Run a private NPC model context without exposing its capsule to the Director model."""

    _scopes = {"core"}

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._pool = NpcConversationWorkerPool()

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return isinstance(ctx.tool_registry, ToolRegistry)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(ctx.tool_registry)

    @property
    def name(self) -> str:
        return "npc_conversation_worker"

    @property
    def description(self) -> str:
        return (
            "Dispatch a public SagaSmith NPC activation to a persistent, zero-tool, actor-isolated "
            "model worker. The host privately checks out the actor capsule, submits the proposal "
            "to MCP, and returns only the validated publication. Use release after closing or "
            "aborting the conversation."
        )

    def _mcp_tool(self, original_name: str, server_name: str | None = None) -> Tool:
        matches = []
        for name in self._registry.tool_names:
            tool = self._registry.get(name)
            if tool is None or getattr(tool, "_original_name", None) != original_name:
                continue
            if server_name and getattr(tool, "_server_name", None) != server_name:
                continue
            matches.append(tool)
        if not matches:
            suffix = f" on MCP server {server_name!r}" if server_name else ""
            raise NpcConversationWorkerError(
                f"{original_name} is not loaded{suffix}; load play.npc_conversation first"
            )
        if len(matches) > 1:
            servers = sorted(str(getattr(item, "_server_name", "")) for item in matches)
            raise NpcConversationWorkerError(
                f"multiple MCP servers expose {original_name}; choose mcp_server from {servers}"
            )
        return matches[0]

    @staticmethod
    def _decode_mcp_result(value: Any, operation: str) -> dict[str, Any]:
        if isinstance(value, ToolResult) and value.is_error:
            raise NpcConversationWorkerError(f"{operation} failed: {value}")
        try:
            decoded = json.loads(str(value))
        except (TypeError, json.JSONDecodeError) as exc:
            raise NpcConversationWorkerError(f"{operation} returned malformed JSON") from exc
        if not isinstance(decoded, dict):
            raise NpcConversationWorkerError(f"{operation} returned a non-object result")
        result = decoded.get("result", decoded)
        if not isinstance(result, dict):
            raise NpcConversationWorkerError(f"{operation} returned a non-object payload")
        return result

    async def _call_mcp(self, tool: Tool, operation: str, **arguments: Any) -> dict[str, Any]:
        result = await tool.execute(**arguments)
        return self._decode_mcp_result(result, operation)

    async def execute(
        self,
        action: str,
        conversation_id: str,
        campaign_id: str = "",
        activation: dict[str, Any] | None = None,
        mcp_server: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "status":
            return json.dumps(
                self._pool.status(conversation_id),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        if action == "release":
            return json.dumps(
                self._pool.release(conversation_id),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        if action != "activate":
            return ToolResult.error(f"Error: unsupported NPC worker action: {action}")
        request = current_request_context()
        if request is None or request.runtime is None:
            return ToolResult.error("Error: NPC conversation worker requires an active runtime")
        if not campaign_id:
            return ToolResult.error("Error: campaign_id is required for NPC activation")
        descriptor = dict(activation or {})
        required = {
            "activation_ref",
            "actor_id",
            "from_cursor",
            "conversation_revision",
        }
        if missing := sorted(required - set(descriptor)):
            return ToolResult.error(f"Error: activation is missing fields: {missing}")
        capsule: dict[str, Any] | None = None
        transport = None
        try:
            transport = self._mcp_tool("npc_conversation_transport", mcp_server)
            activation_ref = str(descriptor["activation_ref"])
            expected_revision = int(descriptor["conversation_revision"])
            capsule = await self._call_mcp(
                transport,
                "npc_conversation_transport.claim_activation",
                campaign_id=campaign_id,
                conversation_id=conversation_id,
                action="claim_activation",
                payload={
                    "activation_ref": activation_ref,
                    "cursor": int(descriptor["from_cursor"]),
                    "include_bootstrap": True,
                    "expected_conversation_revision": expected_revision,
                    "idempotency_key": f"npc-claim:{activation_ref}:{expected_revision}",
                },
            )
            proposal = await self._pool.activate(capsule, runtime=request.runtime)
            submit_revision = int(capsule["conversation_revision"])
            result: dict[str, Any] = {}
            for attempt in range(3):
                result = await self._call_mcp(
                    transport,
                    "npc_conversation_transport.submit_proposal",
                    campaign_id=campaign_id,
                    conversation_id=conversation_id,
                    action="submit_proposal",
                    payload={
                        "activation_ref": activation_ref,
                        "lease_id": str(capsule["lease_id"]),
                        "proposal": proposal,
                        "expected_conversation_revision": submit_revision,
                        "idempotency_key": (
                            f"npc-submit:{activation_ref}:{submit_revision}:{attempt}"
                        ),
                    },
                )
                if result.get("status") != "validation_failed":
                    break
                if attempt == 2:
                    issues = result.get("validation_issues") or []
                    raise NpcConversationWorkerError(
                        f"MCP rejected proposal after repairs: {issues}"
                    )
                proposal = await self._pool.repair_after_mcp_validation(
                    capsule,
                    validation_issues=list(result.get("validation_issues") or []),
                    runtime=request.runtime,
                )
        except (NpcConversationWorkerError, asyncio.TimeoutError) as exc:
            if transport is not None and capsule is not None:
                try:
                    await self._call_mcp(
                        transport,
                        "npc_conversation_transport.cancel_activation",
                        campaign_id=campaign_id,
                        conversation_id=conversation_id,
                        action="cancel_activation",
                        payload={
                            "activation_ref": str(descriptor["activation_ref"]),
                            "lease_id": str(capsule["lease_id"]),
                            "expected_conversation_revision": int(capsule["conversation_revision"]),
                            "idempotency_key": (
                                f"npc-cancel:{descriptor['activation_ref']}:"
                                f"{capsule['conversation_revision']}"
                            ),
                        },
                    )
                except NpcConversationWorkerError:
                    pass
            self._pool.rollback_last_activation(
                conversation_id,
                str((capsule or {}).get("actor_runtime_id") or ""),
            )
            return ToolResult.error(f"Error: isolated NPC activation rejected: {exc}")
        self._pool.confirm_last_activation(conversation_id, str(capsule["actor_runtime_id"]))
        safe_result = {
            key: result.get(key)
            for key in (
                "status",
                "publication",
                "resolution_requests",
                "validation_issues",
                "conversation_revision",
            )
            if key in result
        }
        safe_result["worker"] = self._pool.status(conversation_id)["workers"]
        return json.dumps(safe_result, ensure_ascii=False, separators=(",", ":"))
