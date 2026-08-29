"""Per-requester stdio MCP bridge that injects signed SagaSmith identity metadata."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client, StdioServerParameters, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from nanobot.agent.auth_context import (
    AUTH_CONTEXT_META_KEY,
    sign_auth_context,
    sign_delegated_auth_context,
)
from nanobot.agent.mcp_tasks import (
    TasksExtension,
    TaskTimeoutControl,
    task_authorization_context,
    task_authorization_from_meta,
    task_timeout_context,
)
from nanobot.sagasmith_hosts.contract import TrustedHostContext


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _read_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read auth secret: {path}") from exc
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("auth secret must contain at least 32 bytes")
    return secret


def _context(value: Mapping[str, Any]) -> TrustedHostContext:
    allowed = {
        "host",
        "channel",
        "actor_principal",
        "conversation_principal",
        "session_id",
        "tenant_id",
        "requester_principal",
        "resource_owner_principal",
        "acting_host_principal",
    }
    if not set(value) <= allowed:
        raise ValueError("Host context contains unsupported fields")
    return TrustedHostContext(
        host=str(value.get("host") or ""),
        channel=str(value.get("channel") or ""),
        actor_principal=str(value.get("actor_principal") or ""),
        conversation_principal=str(value.get("conversation_principal") or ""),
        session_id=str(value.get("session_id") or ""),
        tenant_id=str(value.get("tenant_id") or ""),
        requester_principal=str(value.get("requester_principal") or ""),
        resource_owner_principal=str(value.get("resource_owner_principal") or ""),
        acting_host_principal=str(value.get("acting_host_principal") or ""),
    )


def _first_text(value: Any, field: str) -> str:
    if isinstance(value, Mapping):
        candidate = value.get(field)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        for key in ("authority", "branch", "payload", "data", "result"):
            nested = value.get(key)
            if isinstance(nested, Mapping) and (found := _first_text(nested, field)):
                return found
    return ""


def _first_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, Mapping):
        candidate = value.get(field)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            return candidate
        for key in ("authority", "branch", "payload", "data", "result"):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                found = _first_nonnegative_int(nested, field)
                if found >= 0:
                    return found
    return -1


def _result_payload(result: types.CallToolResult) -> Mapping[str, Any]:
    if isinstance(result.structured_content, Mapping):
        return result.structured_content
    for block in result.content:
        if not isinstance(block, types.TextContent):
            continue
        try:
            value = json.loads(block.text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            return value
    return {}


def _epoch_from_result(tool_name: str, payload: Mapping[str, Any], fallback: int) -> int:
    binding = payload.get("host_context_binding")
    if isinstance(binding, Mapping):
        value = binding.get("authorization_epoch")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    if tool_name == "exposure":
        current = payload.get("result", payload)
        if isinstance(current, Mapping):
            value = current.get("revision")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    return fallback


class AuthBridge:
    """Mirror one downstream MCP session for one immutable trusted Host context."""

    def __init__(
        self,
        *,
        server_config: Mapping[str, Any],
        context: TrustedHostContext,
        secret: str,
    ) -> None:
        self.server_config = dict(server_config)
        self.context = context
        self.secret = secret
        self.downstream: Client | None = None
        self.tool_definitions: dict[str, types.Tool] = {}
        self.authorization_epoch = 0
        self.bridge_run_id = secrets.token_urlsafe(12)
        self._refresh_pending: set[str] = set()
        self._refresh_lock = asyncio.Lock()
        self.server = Server(
            "sagasmith-auth-bridge",
            version="2",
            instructions=(
                "SagaSmith identity is injected by the trusted bridge. "
                "Caller-supplied principal fields are ignored."
            ),
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
            on_list_resources=self._handle_list_resources,
            on_read_resource=self._handle_read_resource,
            on_list_prompts=self._handle_list_prompts,
            on_get_prompt=self._handle_get_prompt,
        )

    async def _server_message(self, message: Any) -> None:
        payload = getattr(message, "root", message)
        method = str(getattr(payload, "method", ""))
        kind = {
            "notifications/tools/list_changed": "tools",
            "notifications/resources/list_changed": "resources",
            "notifications/prompts/list_changed": "prompts",
        }.get(method)
        if kind is None:
            return
        self._refresh_pending.add(kind)

    async def _list_tools(self) -> list[types.Tool]:
        if self.downstream is None:
            raise RuntimeError("downstream MCP session is unavailable")
        async with self._refresh_lock:
            result = await self.downstream.list_tools()
            self.tool_definitions = {tool.name: tool for tool in result.tools}
            self._refresh_pending.discard("tools")
            return result.tools

    @staticmethod
    def _principal_argument(tool: types.Tool) -> str | None:
        properties = tool.input_schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return None
        for name in ("auth_principal_id", "by_principal_id", "principal_id"):
            if name in properties:
                return name
        return None

    def _trusted_arguments(self, tool: types.Tool, arguments: Mapping[str, Any]) -> dict[str, Any]:
        trusted = dict(arguments)
        if principal_argument := self._principal_argument(tool):
            trusted[principal_argument] = self.context.actor_principal
        return trusted

    def _meta(self, tool_name: str, arguments: Mapping[str, Any], tool: types.Tool) -> dict[str, Any]:
        if str(self.server_config.get("protocolMode") or "legacy") == "2026-07-28":
            target_service = str(self.server_config.get("targetService") or "").strip()
            if not target_service:
                raise ValueError("modern downstream MCP requires targetService")
            authorized_audience = str(
                self.server_config.get("authorizationAudience") or ""
            ).strip()
            if not authorized_audience:
                raise ValueError("modern downstream MCP requires authorizationAudience")
            campaign_id = _first_text(arguments, "campaign_id") or (
                f"local:{self.context.conversation_principal}"
            )
            base_revision = _first_nonnegative_int(arguments, "base_revision")
            if base_revision < 0:
                base_revision = _first_nonnegative_int(arguments, "expected_revision")
            return {
                AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
                    secret=self.secret,
                    issuer="sagasmith-agent-auth-bridge",
                    target_service=target_service,
                    caller_principal=f"host:{self.context.host}",
                    workload_identity="sagasmith-auth-bridge",
                    requester_principal=self.context.requester_principal,
                    resource_owner_principal=self.context.resource_owner_principal,
                    acting_host_principal=self.context.acting_host_principal,
                    authorized_audience=authorized_audience,
                    allowed_operations=(tool_name,),
                    conversation_principal=self.context.conversation_principal,
                    tenant_id=self.context.tenant_id,
                    campaign_id=campaign_id,
                    room_turn_id=f"{self.context.session_id}:{self.bridge_run_id}",
                    base_revision=max(base_revision, 0),
                )
            }
        epoch = self.authorization_epoch
        if tool_name == "exposure" and arguments.get("action") == "open":
            epoch = 0
        return {
            AUTH_CONTEXT_META_KEY: sign_auth_context(
                secret=self.secret,
                host=self.context.host,
                channel=self.context.channel,
                actor_principal=self.context.actor_principal,
                conversation_principal=self.context.conversation_principal,
                tenant_id=self.context.tenant_id,
                campaign_id=_first_text(arguments, "campaign_id"),
                session_id=self.context.session_id,
                authorization_epoch=epoch,
            )
        }

    async def _call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> types.CallToolResult:
        if self.downstream is None:
            raise RuntimeError("downstream MCP session is unavailable")
        tool = self.tool_definitions.get(name)
        if tool is None:
            await self._list_tools()
            tool = self.tool_definitions.get(name)
        if tool is None:
            raise ValueError(f"unknown downstream tool: {name}")
        trusted = self._trusted_arguments(tool, arguments)
        modern = str(self.server_config.get("protocolMode") or "legacy") == "2026-07-28"
        meta = (
            self._meta(name, trusted, tool)
            if modern or self._principal_argument(tool)
            else None
        )
        task_authorization = task_authorization_from_meta(
            secret=self.secret,
            meta=meta,
            hard_expires_at=(
                str(meta[AUTH_CONTEXT_META_KEY].get("expires_at") or "")
                if isinstance(meta, Mapping)
                and isinstance(meta.get(AUTH_CONTEXT_META_KEY), Mapping)
                else None
            ),
        )
        tool_timeout = int(self.server_config.get("toolTimeout") or 60)
        task_timeout = int(self.server_config.get("taskTimeout") or 900)
        async with asyncio.timeout(tool_timeout) as call_timeout:
            timeout_control = TaskTimeoutControl(call_timeout, task_timeout)
            with (
                task_authorization_context(task_authorization),
                task_timeout_context(timeout_control),
            ):
                result = await self.downstream.call_tool(
                    name,
                    arguments=trusted,
                    **({"meta": meta} if meta is not None else {}),
                )
        self.authorization_epoch = _epoch_from_result(
            name, _result_payload(result), self.authorization_epoch
        )
        if "tools" in self._refresh_pending:
            await self._list_tools()
        return result

    async def _handle_list_tools(
        self,
        _context: Any,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=sorted(await self._list_tools(), key=lambda tool: tool.name),
            ttlMs=5_000,
            cacheScope="private",
        )

    async def _handle_call_tool(
        self,
        _context: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        return await self._call_tool(params.name, params.arguments or {})

    async def _handle_list_resources(
        self,
        _context: Any,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListResourcesResult:
        if self.downstream is None:
            raise RuntimeError("downstream MCP session is unavailable")
        return await self.downstream.list_resources()

    async def _handle_read_resource(
        self,
        _context: Any,
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult:
        if self.downstream is None:
            raise RuntimeError("downstream MCP session is unavailable")
        return await self.downstream.read_resource(params.uri)

    async def _handle_list_prompts(
        self,
        _context: Any,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListPromptsResult:
        if self.downstream is None:
            raise RuntimeError("downstream MCP session is unavailable")
        return await self.downstream.list_prompts()

    async def _handle_get_prompt(
        self,
        _context: Any,
        params: types.GetPromptRequestParams,
    ) -> types.GetPromptResult:
        if self.downstream is None:
            raise RuntimeError("downstream MCP session is unavailable")
        return await self.downstream.get_prompt(params.name, params.arguments)

    async def _connect(self, stack: AsyncExitStack) -> None:
        transport = str(self.server_config.get("type") or "").casefold()
        if not transport:
            transport = "stdio" if self.server_config.get("command") else "streamablehttp"
        if transport == "stdio":
            environment = dict(os.environ)
            raw_env = self.server_config.get("env") or {}
            if not isinstance(raw_env, Mapping):
                raise ValueError("downstream stdio env must be an object")
            environment.update({str(key): str(value) for key, value in raw_env.items()})
            raw_args = self.server_config.get("args") or []
            if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
                raise ValueError("downstream stdio args must be strings")
            params = StdioServerParameters(
                command=str(self.server_config.get("command") or ""),
                args=raw_args,
                cwd=str(self.server_config.get("cwd") or "") or None,
                env=environment,
            )
            client_transport: Any = params
        elif transport in {"streamablehttp", "streamable-http", "http"}:
            url = str(self.server_config.get("url") or "").strip()
            if not url:
                raise ValueError("downstream MCP URL is required")
            headers = self.server_config.get("headers") or {}
            if not isinstance(headers, Mapping):
                raise ValueError("downstream MCP headers must be an object")
            client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers={str(key): str(value) for key, value in headers.items()},
                    follow_redirects=False,
                    timeout=httpx2.Timeout(900, connect=10),
                )
            )
            client_transport = streamable_http_client(url, http_client=client)
        else:
            raise ValueError(f"unsupported downstream MCP transport: {transport}")
        configured_mode = str(self.server_config.get("protocolMode") or "legacy")
        # Pinned-modern SDK mode synthesizes discovery and consequently drops
        # extension capabilities. Negotiate with the peer, then fail closed if
        # the required modern version is unavailable.
        mode = "auto" if configured_mode == "2026-07-28" else configured_mode
        self.downstream = await stack.enter_async_context(
            Client(
                client_transport,
                mode=mode,
                message_handler=self._server_message,
                extensions=(
                    [TasksExtension()] if configured_mode == "2026-07-28" else None
                ),
            )
        )
        if (
            configured_mode == "2026-07-28"
            and str(self.downstream.session.protocol_version) != "2026-07-28"
        ):
            raise RuntimeError("downstream MCP does not support required protocol 2026-07-28")
        await self._list_tools()

    async def run(self) -> None:
        async with AsyncExitStack() as stack:
            await self._connect(stack)
            async with stdio_server() as (read, write):
                await self.server.run(
                    read,
                    write,
                    self.server.create_initialization_options(
                        notification_options=NotificationOptions(
                            tools_changed=True,
                            resources_changed=True,
                            prompts_changed=True,
                        )
                    ),
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    arguments = parser.parse_args()
    bridge = AuthBridge(
        server_config=_read_json_object(arguments.config.resolve(), "bridge config"),
        context=_context(_read_json_object(arguments.context.resolve(), "Host context")),
        secret=_read_secret(arguments.secret_file.resolve()),
    )
    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
