from types import SimpleNamespace

import pytest

from nanobot.agent.local_mcp import LocalOperationState
from nanobot.agent.local_trace import trace_call, trace_recovery, trace_turn
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.session.manager import SessionManager


@pytest.mark.asyncio
async def test_local_trace_records_real_calls_and_excludes_timeout_narrative(tmp_path):
    store = SessionManager(tmp_path)
    state = LocalOperationState("dnd", store, "system:local")
    spec = SimpleNamespace(tools=SimpleNamespace(_tools={"local": SimpleNamespace(
        _local_operations=state,
    )}))

    @trace_call("llm")
    async def model():
        return SimpleNamespace(content="timeout", finish_reason="error", error_kind="timeout")

    @trace_call("tool")
    async def read(tool):
        return ToolResult("ok", structured_content={"local_execution": {
            "database": {"queries": 7, "elapsed_ms": 2.5},
        }})

    with request_context(RequestContext(channel="cli", chat_id="trace")):
        with trace_turn(spec):
            await model()
            await read(SimpleNamespace(read_only=True))
            trace_recovery("retry")
        metrics = store.get_or_create("cli:trace").metadata["local_turn_metrics"]
    assert metrics["llm_calls"] == metrics["tool_calls"] == metrics["read_calls"] == 1
    assert metrics["timeouts"] == metrics["retries"] == 1
    assert metrics["database_queries"] == 7
    assert metrics["database_ms"] == 2.5
    assert metrics["first_complete_narrative_ms"] is None
    assert metrics["total_ms"] >= metrics["llm_ms"]
