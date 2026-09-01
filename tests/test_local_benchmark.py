from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.sagasmith_local import benchmark
from nanobot.sagasmith_local.model import InstallMode, StackLayout


def _layout(tmp_path: Path) -> StackLayout:
    workspace = tmp_path / "workspace"
    agent = workspace / "SagaSmith-agent"
    agent.mkdir(parents=True)
    return StackLayout(
        workspace_root=workspace,
        agent_root=agent,
        state_root=tmp_path / "state",
        config_path=tmp_path / "config.json",
    )


def test_summary_reports_stable_warm_call_statistics() -> None:
    result = benchmark._summary([0.3, 0.1, 0.2, 0.4])
    assert result == {
        "count": 4,
        "min": 0.1,
        "median": 0.25,
        "p95": 0.4,
        "max": 0.4,
    }


def test_benchmark_default_allows_cold_windows_domain_startup() -> None:
    assert benchmark.DEFAULT_BENCHMARK_TIMEOUT == 120.0


def test_measure_session_uses_sdk_v2_call_result_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    @asynccontextmanager
    async def fake_transport(_url: str):
        yield object(), object()

    class FakeSession:
        def __init__(self, _read: object, _write: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            calls.append("initialize")

        async def call_tool(self, name: str, _arguments: dict[str, object]):
            calls.append(name)
            return SimpleNamespace(is_error=False)

    monkeypatch.setattr(benchmark, "streamable_http_client", fake_transport)
    monkeypatch.setattr(benchmark, "ClientSession", FakeSession)

    cold, warm = asyncio.run(
        benchmark._measure_session(
            "http://127.0.0.1:1/mcp",
            started_at=time.perf_counter(),
            iterations=2,
        )
    )

    assert cold >= 0
    assert len(warm) == 2
    assert calls == ["initialize", "server_capabilities", "server_capabilities", "server_capabilities"]


def test_measure_session_reports_tool_error_after_transport_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_closed = False

    @asynccontextmanager
    async def fake_transport(_url: str):
        nonlocal transport_closed
        try:
            yield object(), object()
        finally:
            transport_closed = True

    class FakeSession:
        def __init__(self, _read: object, _write: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def call_tool(self, _name: str, _arguments: dict[str, object]):
            return SimpleNamespace(
                is_error=True,
                structured_content={
                    "error": {
                        "code": "authorization_denied",
                        "message": "signed auth context is required",
                    }
                },
            )

    monkeypatch.setattr(benchmark, "streamable_http_client", fake_transport)
    monkeypatch.setattr(benchmark, "ClientSession", FakeSession)

    with pytest.raises(benchmark.StackError, match="authorization_denied"):
        asyncio.run(
            benchmark._measure_session(
                "http://127.0.0.1:1/mcp",
                started_at=time.perf_counter(),
                iterations=1,
            )
        )
    assert transport_closed is True


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows RSS implementation")
def test_windows_rss_reads_current_process() -> None:
    assert benchmark._resident_set_size(os.getpid()) > 0


def test_benchmark_uses_isolated_scratch_and_never_reports_llm_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    domain = layout.repo("sagasmith-dnd")
    (domain / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    python = domain / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_run(spec: benchmark.BenchmarkSpec, **kwargs: object) -> dict[str, object]:
        seen["home"] = spec.environment["SAGASMITH_DND_MCP_HOME"]
        seen["cache"] = spec.environment["DND5E_EMBEDDING_CACHE_DIR"]
        seen["iterations"] = kwargs["iterations"]
        return {
            "domain": "dnd",
            "cold_start_seconds": 0.1,
            "warm_mcp_tool_seconds": benchmark._summary([0.01, 0.02]),
            "idle_rss_bytes": 1024,
        }

    monkeypatch.setattr(benchmark, "_benchmark_domain", fake_run)
    result = benchmark.benchmark_local_kit(
        layout,
        modes=(InstallMode.DND,),
        iterations=2,
    )

    assert result["llm_used"] is False
    assert result["authoritative_data_used"] is False
    assert result["metrics"][0]["idle_rss_bytes"] == 1024
    assert seen["iterations"] == 2
    assert "sagasmith-local-benchmark-" in str(seen["home"])
    assert "sagasmith-local-benchmark-" in str(seen["cache"])
    assert not Path(str(seen["home"])).exists()


def test_benchmark_does_not_inherit_installed_auth_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    domain = layout.repo("sagasmith-dnd")
    (domain / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    python = domain / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.write_text("", encoding="utf-8")
    layout.config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "mcpServers": {
                        "sagasmith_dnd": {"authContextSecret": "s" * 32}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SAGASMITH_AUTH_CONTEXT_SECRET", "parent-secret")
    seen: dict[str, str] = {}

    def fake_popen(*_args: object, **kwargs: object):
        seen.update(kwargs["env"])
        raise RuntimeError("stop after environment capture")

    spec = benchmark._build_spec(layout, InstallMode.DND, tmp_path / "scratch")
    assert "SAGASMITH_AUTH_CONTEXT_SECRET" not in spec.environment
    monkeypatch.setattr(benchmark.subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError, match="environment capture"):
        benchmark._benchmark_domain(spec, iterations=1, timeout=1.0, idle_delay=0.0)
    assert "SAGASMITH_AUTH_CONTEXT_SECRET" not in seen


@pytest.mark.skipif(os.name != "nt", reason="exercises Windows process-tree shutdown")
def test_stop_process_waits_for_child_file_handle(tmp_path: Path) -> None:
    locked = tmp_path / "locked.txt"
    child = (
        "import sys,time; "
        "handle=open(sys.argv[1],'w'); handle.write('ready'); handle.flush(); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r},sys.argv[1]]); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", parent, str(locked)])
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not locked.exists():
            time.sleep(0.05)
        assert locked.exists()
    finally:
        benchmark._stop_process(process)

    locked.unlink()
    assert not locked.exists()


def test_benchmark_requires_at_least_one_iteration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        benchmark.benchmark_local_kit(
            _layout(tmp_path),
            modes=(InstallMode.NARRATIVE,),
            iterations=0,
        )


def test_current_process_rss_is_available_on_supported_platforms() -> None:
    if os.name == "nt" or sys.platform.startswith(("linux", "darwin")):
        assert benchmark._resident_set_size(os.getpid()) > 0
        assert os.getpid() in benchmark._process_tree(os.getpid())
        assert benchmark._process_tree_rss(os.getpid()) > 0
