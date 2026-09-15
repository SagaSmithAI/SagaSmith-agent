"""Persist an execution fence before Hosted MCP dispatch, independent of LLM output."""
from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import current_request_context, request_context


class JournaledTool(Tool):
    def __init__(self, tool: Tool, client: Any, callback: dict[str, str]) -> None:
        self._delegate = tool
        self._client = client
        self._callback = callback

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def description(self) -> str:
        return self._delegate.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._delegate.parameters

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def read_only(self) -> bool:
        return self._delegate.read_only

    def runtime_context_provider(self) -> Any:
        return self._delegate.runtime_context_provider()

    async def _record(self, payload: dict[str, Any]) -> None:
        response = await self._client.post(
            self._callback["url"],
            headers={"Authorization": f"Bearer {self._callback['token']}"},
            json=payload,
        )
        response.raise_for_status()

    async def execute(self, **kwargs: Any) -> Any:
        call_id = str(uuid.uuid4())
        identity = {"call_id": call_id, "tool": self._delegate._original_name}
        # A callback failure propagates before the domain can perform any write.
        await self._record({**identity, "state": "dispatched"})
        context = current_request_context()
        scope = request_context(replace(context, metadata={
            **context.metadata, "idempotency_key": f"room-operation:{call_id}",
        })) if context is not None else nullcontext()
        with scope:
            result = await self._delegate.execute(**kwargs)
        # Do not catch timeout/cancellation: the durable fence remains uncertain.
        await self._record({
            **identity, "state": "returned",
            "result": {
                "structured_content": getattr(result, "structured_content", None),
                "auth_context_receipt": getattr(result, "audit_receipt", None),
                "is_error": bool(getattr(result, "is_error", False)),
            },
        })
        return result
