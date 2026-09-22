"""Per-turn local latency evidence; no prompts, arguments or identities are logged."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

from loguru import logger

_TRACE = ContextVar("local_turn_trace", default=None)
_SPAN = ContextVar("local_trace_span", default=None)


@contextmanager
def trace_turn(spec):
    states = [getattr(tool, "_local_operations", None)
              for tool in getattr(spec.tools, "_tools", {}).values()]
    state = next((item for item in states if item is not None), None)
    trace = {"llm_calls": 0, "llm_ms": 0.0, "tool_calls": 0, "tool_ms": 0.0,
             "read_calls": 0, "retries": 0, "timeouts": 0,
             "database_queries": 0, "database_ms": 0.0,
             "first_complete_narrative_ms": None, "started": perf_counter()} if state else None
    token = _TRACE.set(trace)
    try:
        yield
    finally:
        _TRACE.reset(token)
        if trace is not None:
            trace["total_ms"] = (perf_counter() - trace.pop("started")) * 1000
            try:
                _, session = state._state()
                if session is not None:
                    session.metadata["local_turn_metrics"] = trace
                    state.session_store.save(session)
            except Exception:
                logger.warning("Could not persist local turn metrics")
            logger.info("Local turn metrics: {}", trace)


def trace_call(kind):
    def decorate(function):
        @wraps(function)
        async def measured(*args, **kwargs):
            trace = _TRACE.get()
            if trace is None:
                return await function(*args, **kwargs)
            started = perf_counter()
            parent_kind = _SPAN.get()
            span_token = _SPAN.set(kind)
            trace[kind + "_calls"] += 1
            if kind == "llm" and parent_kind == "llm":
                trace["retries"] += 1
            if kind == "tool" and getattr(args[0], "read_only", False):
                trace["read_calls"] += 1
            try:
                result = await function(*args, **kwargs)
                if kind == "llm" and getattr(result, "error_kind", None) == "timeout":
                    trace["timeouts"] += 1
                if kind == "tool":
                    payload = getattr(result, "structured_content", None)
                    if isinstance(payload, dict):
                        database = payload.get("local_execution", {}).get("database", {})
                        trace["database_queries"] += database.get("queries", 0)
                        trace["database_ms"] += database.get("elapsed_ms", 0.0)
                if (kind == "llm" and getattr(result, "content", None)
                        and getattr(result, "finish_reason", None) != "error"
                        and not getattr(result, "tool_calls", None)
                        and trace["first_complete_narrative_ms"] is None):
                    trace["first_complete_narrative_ms"] = (perf_counter() - trace["started"]) * 1000
                return result
            finally:
                _SPAN.reset(span_token)
                if parent_kind != kind:
                    trace[kind + "_ms"] += (perf_counter() - started) * 1000
        return measured
    return decorate


def trace_recovery(outcome):
    trace = _TRACE.get()
    if trace is not None and outcome in {"retry", "timeout"}:
        trace["retries" if outcome == "retry" else "timeouts"] += 1


@trace_call("tool")
async def trace_tool_call(tool, awaitable):
    """Measure built-in and MCP tools at the same Host execution boundary."""
    return await awaitable
