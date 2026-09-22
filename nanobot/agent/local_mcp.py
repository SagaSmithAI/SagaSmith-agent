"""Host-owned operation identity for explicitly trusted local MCP connections."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import current_request_context
from nanobot.runtime_context import RuntimeContextBlock


class LocalOperationState:
    def __init__(self, server_name, session_store, principal_id):
        self.server_name = server_name
        self.session_store = session_store
        self.principal_id = principal_id
        self.pending = {}
        self.contexts = {}
        self.selected = set()

    def _state(self):
        request = current_request_context()
        if self.session_store is None or request is None:
            return self.pending, None
        key = request.session_key or f"{request.channel}:{request.chat_id}"
        session = self.session_store.get_or_create(key)
        return session.metadata.setdefault("local_mcp_pending", {}), session

    def prepare(self, name, arguments):
        state, session = self._state()
        intent = hashlib.sha256(json.dumps(
            [name, arguments], sort_keys=True, ensure_ascii=False
        ).encode()).hexdigest()
        pending = state.get(self.server_name)
        if pending and pending["intent"] != intent:
            raise RuntimeError(
                "Previous local operation has an unknown result. Recover its original "
                f"call before another write: {pending['name']} {pending['arguments']}"
            )
        if pending is None:
            pending = {"intent": intent, "name": name, "arguments": arguments,
                       "key": "local-" + uuid4().hex}
            state[self.server_name] = pending
            if session is not None:
                self.session_store.save(session)
        return pending["key"]

    def finish(self):
        state, session = self._state()
        state.pop(self.server_name, None)
        if session is not None:
            self.session_store.save(session)

    def context(self):
        _, session = self._state()
        if session is None:
            return self.contexts
        return session.metadata.setdefault("local_mcp_context", {}).get(self.server_name, {})

    def remember(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("local_context"), dict):
            return
        context = payload["local_context"]
        previous = self.context()
        # Keep no actor slice across a changed revision, rules/role/branch/epoch boundary.
        if previous.get("binding") != context.get("binding"):
            self.selected.clear()
        _, session = self._state()
        if session is None:
            self.contexts = context
        else:
            session.metadata.setdefault("local_mcp_context", {})[self.server_name] = context
            self.session_store.save(session)


class LocalCapabilities(Tool):
    """Discover and load supplemental schemas without mutating the server catalog."""

    _plugin_discoverable = False

    def __init__(self, state, registry):
        self.state, self.registry = state, registry

    @property
    def name(self):
        return "mcp_" + self.state.server_name + "_local_capabilities"

    @property
    def description(self):
        return "Find and load supplemental local tools for preparation, management or unusual actions."

    @property
    def parameters(self):
        return {"type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"]}

    @property
    def read_only(self):
        return True

    async def execute(self, query, **kwargs):
        words = query.casefold().split()
        candidates = [tool for tool in self.registry._tools.values()
                      if getattr(tool, "_local_operations", None) is self.state]
        matches = [tool for tool in candidates if words and any(
            word in (tool.name + " " + tool.description).casefold() for word in words
        )][:12]
        self.state.selected.update(tool.name for tool in matches)
        return json.dumps([{"name": tool.name, "description": tool.description} for tool in matches])

    def runtime_context_provider(self):
        async def provide(request):
            context = self.state.context()
            if not context:
                return None
            return RuntimeContextBlock(
                source="local_authority:" + self.state.server_name,
                content=json.dumps(context, ensure_ascii=False),
            )
        return provide
