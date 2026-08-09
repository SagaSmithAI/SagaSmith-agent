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
    ArraySchema,
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
            nullable=True,
        ),
        bundle=ObjectSchema(
            description="Signed purpose-specific bundle returned by a SagaSmith MCP",
            additional_properties=True,
            nullable=True,
        ),
        jobs=ArraySchema(
            ObjectSchema(
                kind=StringSchema(
                    description="Fixed evaluation contract",
                    enum=tuple(sorted(ISOLATED_EVALUATION_KINDS)),
                ),
                bundle=ObjectSchema(additional_properties=True),
                strict_guardian=BooleanSchema(default=False),
                required=["kind", "bundle"],
                additional_properties=False,
            ),
            description="Independent signed bundles to evaluate concurrently",
            min_items=1,
            max_items=16,
            nullable=True,
        ),
        strict_guardian=BooleanSchema(
            description="Run a second fresh zero-tool audit before returning",
            default=False,
        ),
        required=[],
    )
)
class IsolatedEvaluateTool(Tool):
    """Evaluate one or more signed bundles without inheriting host memory or tools."""

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
            "bundle, or a bounded batch of independent bundles, in fresh tool-free "
            "non-persistent model calls. Returns proposals only; authoritative mechanics "
            "and writes remain serial MCP operations."
        )

    async def execute(
        self,
        kind: str | None = None,
        bundle: dict[str, Any] | None = None,
        jobs: list[dict[str, Any]] | None = None,
        strict_guardian: bool = False,
        **kwargs: Any,
    ) -> str:
        if self._runner is None:
            return ToolResult.error("Error: isolated_evaluate is unavailable in this host")
        request = current_request_context()
        if request is None or request.runtime is None:
            return ToolResult.error("Error: isolated_evaluate requires an active model runtime")
        single_selected = kind is not None or bundle is not None
        batch_selected = jobs is not None
        if single_selected == batch_selected:
            return ToolResult.error("Error: provide exactly one of kind+bundle or jobs")
        if single_selected and (kind is None or bundle is None):
            return ToolResult.error("Error: kind and bundle must be provided together")

        async def run_one(
            job_kind: str,
            job_bundle: dict[str, Any],
            guardian: bool,
        ) -> dict[str, Any]:
            result = await self._runner.run(
                job_kind,
                job_bundle,
                runtime=request.runtime,
                strict_guardian=guardian,
            )
            return {
                "kind": result.kind,
                "proposal": result.proposal,
                "isolation": {
                    "level": result.isolation_level,
                    "tools_exposed": result.tools_exposed,
                    "session_persisted": result.session_persisted,
                    "generation_attempts": result.generation_attempts,
                    "guardian_checks": result.guardian_checks,
                },
            }

        if single_selected:
            assert kind is not None and bundle is not None
            try:
                value = await run_one(kind, bundle, bool(strict_guardian))
            except (IsolatedEvaluationError, asyncio.TimeoutError) as exc:
                return ToolResult.error(f"Error: isolated evaluation rejected: {exc}")
        else:
            assert jobs is not None
            outcomes = await asyncio.gather(
                *(
                    run_one(
                        str(job["kind"]),
                        dict(job["bundle"]),
                        bool(job.get("strict_guardian", False)),
                    )
                    for job in jobs
                ),
                return_exceptions=True,
            )
            results: list[dict[str, Any]] = []
            for index, outcome in enumerate(outcomes):
                if isinstance(outcome, BaseException):
                    results.append(
                        {
                            "index": index,
                            "kind": str(jobs[index].get("kind") or ""),
                            "error": str(outcome),
                        }
                    )
                else:
                    results.append({"index": index, **outcome})
            value = {"results": results}
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
