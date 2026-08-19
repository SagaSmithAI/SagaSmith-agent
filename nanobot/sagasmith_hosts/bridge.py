"""Per-requester stdio MCP bridge that injects signed SagaSmith identity metadata."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from nanobot.agent.auth_context import AUTH_CONTEXT_META_KEY, sign_auth_context
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


def _result_payload(result: types.CallToolResult) -> Mapping[str, Any]:
    if isinstance(result.structuredContent, Mapping):
        return result.structuredContent
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
        self.downstream: ClientSession | None = None
        self.tool_definitions: dict[str, types.Tool] = {}
        self.authorization_epoch = 0
        self._upstream_sessions: dict[int, Any] = {}
        self._refresh_pending: set[str] = set()
        self._refresh_lock = asyncio.Lock()
        self.server = Server(
            "sagasmith-auth-bridge",
            version="1",
            instructions=(
                "SagaSmith identity is injected by the trusted bridge. "
                "Caller-supplied principal fields are ignored."
            ),
        )
        self._register_handlers()

    def _remember_upstream(self) -> None:
        session = self.server.request_context.session
        self._upstream_sessions[id(session)] = session

    async def _notify_upstream(self, kind: str) -> None:
        stale: list[int] = []
        for key, session in self._upstream_sessions.items():
            try:
                if kind == "tools":
                    await session.send_tool_list_changed()
                elif kind == "resources":
                    await session.send_resource_list_changed()
                elif kind == "prompts":
                    await session.send_prompt_list_changed()
            except Exception:
                stale.append(key)
        for key in stale:
            self._upstream_sessions.pop(key, None)

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
        # A notification can arrive outside a tool call. Forward it immediately;
        # the next list request remains authoritative and refreshes the cache.
        await self._notify_upstream(kind)

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
        properties = tool.inputSchema.get("properties", {})
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
        meta = self._meta(name, trusted, tool) if self._principal_argument(tool) else None
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

    def _register_handlers(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[types.Tool]:
            self._remember_upstream()
            return await self._list_tools()

        @self.server.call_tool(validate_input=True)
        async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            self._remember_upstream()
            return await self._call_tool(name, arguments)

        @self.server.list_resources()
        async def list_resources() -> types.ListResourcesResult:
            self._remember_upstream()
            if self.downstream is None:
                raise RuntimeError("downstream MCP session is unavailable")
            return await self.downstream.list_resources()

        @self.server.read_resource()
        async def read_resource(uri: Any) -> list[ReadResourceContents]:
            self._remember_upstream()
            if self.downstream is None:
                raise RuntimeError("downstream MCP session is unavailable")
            result = await self.downstream.read_resource(uri)
            contents: list[ReadResourceContents] = []
            for item in result.contents:
                if isinstance(item, types.TextResourceContents):
                    contents.append(
                        ReadResourceContents(
                            content=item.text,
                            mime_type=item.mimeType,
                            meta=item.meta,
                        )
                    )
                elif isinstance(item, types.BlobResourceContents):
                    contents.append(
                        ReadResourceContents(
                            content=item.blob.encode("ascii"),
                            mime_type=item.mimeType,
                            meta=item.meta,
                        )
                    )
            return contents

        @self.server.list_prompts()
        async def list_prompts() -> types.ListPromptsResult:
            self._remember_upstream()
            if self.downstream is None:
                raise RuntimeError("downstream MCP session is unavailable")
            return await self.downstream.list_prompts()

        @self.server.get_prompt()
        async def get_prompt(
            name: str, arguments: dict[str, str] | None
        ) -> types.GetPromptResult:
            self._remember_upstream()
            if self.downstream is None:
                raise RuntimeError("downstream MCP session is unavailable")
            return await self.downstream.get_prompt(name, arguments)

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
            read, write = await stack.enter_async_context(stdio_client(params))
        elif transport in {"streamablehttp", "streamable-http", "http"}:
            url = str(self.server_config.get("url") or "").strip()
            if not url:
                raise ValueError("downstream MCP URL is required")
            headers = self.server_config.get("headers") or {}
            if not isinstance(headers, Mapping):
                raise ValueError("downstream MCP headers must be an object")
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={str(key): str(value) for key, value in headers.items()},
                    timeout=httpx.Timeout(900, connect=10),
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(url, http_client=client)
            )
        else:
            raise ValueError(f"unsupported downstream MCP transport: {transport}")
        self.downstream = await stack.enter_async_context(
            ClientSession(read, write, message_handler=self._server_message)
        )
        await self.downstream.initialize()
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
