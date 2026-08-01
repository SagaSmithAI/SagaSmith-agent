"""Tool-free evaluator for fixed, signed SagaSmith context contracts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nanobot.agent.isolated_evaluation import (
    ISOLATED_EVALUATION_KINDS,
    IsolatedEvaluationError,
    IsolatedEvaluationRunner,
)
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import (
    BooleanSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)


@tool_parameters(
    tool_parameters_schema(
        kind=StringSchema(
            description="Fixed evaluation contract selected by the MCP bundle purpose",
            enum=tuple(sorted(ISOLATED_EVALUATION_KINDS)),
        ),
        bundle=ObjectSchema(
            description="Signed purpose-specific bundle returned by a SagaSmith MCP",
            additional_properties=True,
        ),
        strict_guardian=BooleanSchema(
            description="Run a second fresh zero-tool audit before returning",
            default=False,
        ),
        required=["kind", "bundle"],
    )
)
class IsolatedEvaluateTool(Tool):
    """Evaluate one signed bundle without inheriting host memory or tools."""

    _scopes = {"core"}

    def __init__(self, runner: IsolatedEvaluationRunner | None) -> None:
        self._runner = runner

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(runner=ctx.isolated_evaluation_runner)

    @property
    def name(self) -> str:
        return "isolated_evaluate"

    @property
    def description(self) -> str:
        return (
            "Evaluate one signed SagaSmith actor, audience, faction, source, or ruling "
            "bundle in a fresh, tool-free, non-persistent model call. Returns a proposal "
            "only; authoritative mechanics and writes remain MCP operations."
        )

    async def execute(
        self,
        kind: str,
        bundle: dict[str, Any],
        strict_guardian: bool = False,
        **kwargs: Any,
    ) -> str:
        if self._runner is None:
            return ToolResult.error("Error: isolated_evaluate is unavailable in this host")
        request = current_request_context()
        if request is None or request.runtime is None:
            return ToolResult.error("Error: isolated_evaluate requires an active model runtime")
        try:
            result = await self._runner.run(
                kind,
                bundle,
                runtime=request.runtime,
                strict_guardian=bool(strict_guardian),
            )
        except (IsolatedEvaluationError, asyncio.TimeoutError) as exc:
            return ToolResult.error(f"Error: isolated evaluation rejected: {exc}")
        return json.dumps(
            {
                "kind": result.kind,
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
