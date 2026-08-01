"""Tool-free, non-persistent NPC portrayal runner."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nanobot.agent.npc_turn import NpcTurnError, NpcTurnRunner
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import BooleanSchema, ObjectSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        bundle=ObjectSchema(
            description="Signed purpose=npc_turn bundle returned by a domain context tool",
            additional_properties=True,
        ),
        strict_guardian=BooleanSchema(
            description="Run a second isolated model audit before returning the proposal",
            default=False,
        ),
        required=["bundle"],
    )
)
class PortrayNpcTool(Tool):
    """Generate one isolated NPC proposal without giving the inner model tools."""

    _scopes = {"core"}

    def __init__(self, runner: NpcTurnRunner | None) -> None:
        self._runner = runner

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(runner=ctx.npc_turn_runner)

    @property
    def name(self) -> str:
        return "portray_npc"

    @property
    def description(self) -> str:
        return (
            "Portray one NPC from a signed domain npc_turn context bundle in a fresh, "
            "tool-free, non-persistent model call. Returns a proposal only; the caller "
            "must resolve mechanics and explicitly accept deltas through MCP."
        )

    async def execute(
        self,
        bundle: dict[str, Any],
        strict_guardian: bool = False,
        **kwargs: Any,
    ) -> str:
        if self._runner is None:
            return ToolResult.error("Error: portray_npc is unavailable in this host")
        request = current_request_context()
        if request is None or request.runtime is None:
            return ToolResult.error("Error: portray_npc requires an active model runtime")
        try:
            result = await self._runner.run(
                bundle,
                runtime=request.runtime,
                strict_guardian=bool(strict_guardian),
            )
        except (NpcTurnError, asyncio.TimeoutError) as exc:
            return ToolResult.error(f"Error: isolated NPC portrayal rejected: {exc}")
        return json.dumps(
            {
                "proposal": result.proposal,
                "isolation": {
                    "level": result.isolation_level,
                    "tools_exposed": result.tools_exposed,
                    "session_persisted": result.session_persisted,
                    "generation_attempts": result.generation_attempts,
                    "guardian_checks": result.guardian_checks,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
