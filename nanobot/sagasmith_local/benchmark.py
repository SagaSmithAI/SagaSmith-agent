"""Hermetic, LLM-free benchmarks for the SagaSmith Local Agent Kit."""

from __future__ import annotations

import asyncio
import ctypes
import math
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .configuration import coc_environment, dnd_environment, narrative_environment
from .model import InstallMode, McpTransport, StackLayout
from .runtime import StackError, _venv_python


@dataclass(frozen=True)
class BenchmarkSpec:
    """One transient loopback MCP process used by the benchmark."""

    domain: str
    command: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    url: str


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _build_spec(layout: StackLayout, mode: InstallMode, scratch: Path) -> BenchmarkSpec:
    port = _reserve_loopback_port()
    if mode == InstallMode.DND:
        repo = layout.repo("sagasmith-dnd")
        environment = dnd_environment(layout, transport=McpTransport.STREAMABLE_HTTP)
        environment.update(
            {
                "SAGASMITH_DND_MCP_HOME": str(scratch / "dnd"),
                "SAGASMITH_DND_MCP_HTTP_PORT": str(port),
                "DND5E_EMBEDDING_CACHE_DIR": str(scratch / "dnd" / "embedding-cache"),
            }
        )
        module = "sagasmith_dnd_mcp.server"
    elif mode == InstallMode.COC:
        repo = layout.repo("sagasmith-coc")
        environment = coc_environment(layout, transport=McpTransport.STREAMABLE_HTTP)
        environment.update(
            {
                "SAGASMITH_COC_MCP_HOME": str(scratch / "coc"),
                "SAGASMITH_COC_MCP_HTTP_PORT": str(port),
                "COC7_EMBEDDING_CACHE_DIR": str(scratch / "coc" / "embedding-cache"),
            }
        )
        module = "sagasmith_coc_mcp.server"
    else:
        repo = layout.repo("sagasmith-narrative")
        environment = narrative_environment(layout, transport=McpTransport.STREAMABLE_HTTP)
        environment.update(
            {
                "SAGASMITH_NARRATIVE_MCP_HOME": str(scratch / "narrative"),
                "SAGASMITH_NARRATIVE_MCP_HTTP_PORT": str(port),
            }
        )
        module = "sagasmith_narrative_mcp.server"
    return BenchmarkSpec(
        domain=mode.value,
        command=(str(_venv_python(repo)), "-m", module),
        cwd=repo,
        environment=environment,
        url=f"http://127.0.0.1:{port}/mcp",
    )


def _wait_for_port(process: subprocess.Popen[bytes], port: int, timeout: float) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise StackError(f"benchmark MCP exited before startup (exit {return_code})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise StackError(f"benchmark MCP did not open 127.0.0.1:{port} within {timeout:g}s")


async def _measure_session(
    url: str,
    *,
    started_at: float,
    iterations: int,
) -> tuple[float, list[float]]:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            first = await session.call_tool("server_capabilities", {})
            if first.isError:
                raise StackError("server_capabilities failed during cold-start benchmark")
            cold_start = time.perf_counter() - started_at
            warm: list[float] = []
            for _ in range(iterations):
                before = time.perf_counter()
                result = await session.call_tool("server_capabilities", {})
                warm.append(time.perf_counter() - before)
                if result.isError:
                    raise StackError("server_capabilities failed during warm benchmark")
    return cold_start, warm


def _windows_rss(pid: int) -> int:
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    process_query_information = 0x0400
    process_vm_read = 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    handle = kernel32.OpenProcess(process_query_information | process_vm_read, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _windows_processes() -> list[tuple[int, int]]:
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry),
    ]
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    rows: list[tuple[int, int]] = []
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise OSError(ctypes.get_last_error(), "Process32FirstW failed")
        while True:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def _process_tree(pid: int) -> list[int]:
    if os.name == "nt":
        rows = _windows_processes()
    else:
        result = subprocess.run(
            ["ps", "-e", "-o", "pid=", "-o", "ppid="],
            check=True,
            capture_output=True,
            text=True,
        )
        pairs = (line.split() for line in result.stdout.splitlines() if line.strip())
        rows = [(int(pid_text), int(ppid_text)) for pid_text, ppid_text in pairs]
    descendants = [pid]
    while True:
        added = [
            child
            for child, parent in rows
            if parent in descendants and child not in descendants
        ]
        if not added:
            return descendants
        descendants.extend(added)


def _resident_set_size(pid: int) -> int:
    if os.name == "nt":
        return _windows_rss(pid)
    result = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip()) * 1024


def _process_tree_rss(pid: int) -> int:
    total = 0
    for process_id in _process_tree(pid):
        try:
            total += _resident_set_size(process_id)
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    if total <= 0:
        raise OSError(f"could not read RSS for process tree {pid}")
    return total


def _terminate_windows_pid(pid: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x0001, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        for pid in reversed(_process_tree(process.pid)):
            _terminate_windows_pid(pid)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            for pid in reversed(_process_tree(process.pid)):
                _terminate_windows_pid(pid)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _benchmark_domain(
    spec: BenchmarkSpec,
    *,
    iterations: int,
    timeout: float,
    idle_delay: float,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update(spec.environment)
    started_at = time.perf_counter()
    process = subprocess.Popen(
        list(spec.command),
        cwd=spec.cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )
    try:
        port = int(spec.url.split(":")[-1].split("/", 1)[0])
        _wait_for_port(process, port, timeout)
        cold, warm = asyncio.run(
            asyncio.wait_for(
                _measure_session(spec.url, started_at=started_at, iterations=iterations),
                timeout=timeout,
            )
        )
        time.sleep(idle_delay)
        rss = _process_tree_rss(process.pid)
        return {
            "domain": spec.domain,
            "cold_start_seconds": cold,
            "warm_mcp_tool_seconds": _summary(warm),
            "idle_rss_bytes": rss,
        }
    except TimeoutError as exc:
        raise StackError(f"benchmark timed out for {spec.domain}") from exc
    except StackError:
        raise
    except Exception as exc:
        raise StackError(f"benchmark failed for {spec.domain}: {exc}") from exc
    finally:
        _stop_process(process)


def _cleanup_scratch(scratch: Path) -> None:
    resolved = scratch.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if resolved.parent != temporary_root or not resolved.name.startswith(
        "sagasmith-local-benchmark-"
    ):
        raise StackError(f"refusing to clean unexpected benchmark directory: {resolved}")
    for attempt in range(10):
        try:
            shutil.rmtree(resolved)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def benchmark_local_kit(
    layout: StackLayout,
    *,
    modes: tuple[InstallMode, ...] | None = None,
    iterations: int = 5,
    timeout: float = 30.0,
    idle_delay: float = 0.2,
) -> dict[str, Any]:
    """Benchmark transient domain MCPs without an LLM or authoritative user data."""
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    selected = modes or layout.load_state().selected_modes
    if not selected:
        raise StackError("install or select at least one SagaSmith domain first")
    scratch = Path(tempfile.mkdtemp(prefix="sagasmith-local-benchmark-"))
    try:
        metrics = [
            _benchmark_domain(
                _build_spec(layout, mode, scratch),
                iterations=iterations,
                timeout=timeout,
                idle_delay=idle_delay,
            )
            for mode in selected
        ]
    finally:
        _cleanup_scratch(scratch)
    return {
        "schema": "sagasmith.local-benchmark/v1",
        "ok": True,
        "llm_used": False,
        "authoritative_data_used": False,
        "platform": sys.platform,
        "iterations": iterations,
        "metrics": metrics,
    }
