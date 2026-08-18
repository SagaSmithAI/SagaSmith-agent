"""Caller-defined terminal structured output for hosted agent turns."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StructuredOutputTool(Tool):
    """Capture one schema-validated payload without giving it product semantics.

    The caller owns the JSON schema and consumes :attr:`submission` after the
    turn.  The tool deliberately has no external side effect: it is a typed
    Agent-to-host delivery channel, not an authoritative domain mutation.
    """

    # The host constructs this tool from a caller-supplied schema.  It cannot be
    # instantiated by the zero-argument built-in/plugin discovery path.
    _plugin_discoverable = False

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        if not _TOOL_NAME.fullmatch(name):
            raise ValueError("structured output tool name is invalid")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("structured output parameters must be an object JSON schema")
        self._name = name
        self._description = description.strip()
        self._parameters = deepcopy(parameters)
        self._submission: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return deepcopy(self._parameters)

    @property
    def exclusive(self) -> bool:
        # A terminal presentation must never race an authoritative mechanic.
        return True

    @property
    def submission(self) -> dict[str, Any] | None:
        return deepcopy(self._submission)

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self._submission is not None:
            return ToolResult.error("Structured output was already submitted for this turn.")
        self._submission = deepcopy(kwargs)
        return ToolResult(
            "Structured output accepted. End the turn now without repeating the payload."
        )
