"""Install, supervise, diagnose, back up, and upgrade the local stack."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .configuration import (
    agent_webui_url,
    coc_environment,
    configure_agent,
    desired_servers,
    desired_skill_roots,
    dnd_environment,
)
from .model import (
    InstallMode,
    ProcessRecord,
    StackLayout,
    StackState,
    atomic_json_write,
    load_release_revisions,
    selected_components,
)


class StackError(RuntimeError):
    """Actionable local stack failure."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        command,
        cwd=cwd,
        env=merged_env,
        check=True,
        text=True,
        capture_output=capture,
    )


def _which(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise StackError(f"required executable is not on PATH: {name}")
    return executable


def _venv_python(repo: Path) -> Path:
    candidate = repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.exists():
        raise StackError(f"installed Python environment is missing: {candidate}")
    return candidate


def _git_revision(repo: Path) -> str:
    try:
        result = _run(["git", "rev-parse", "HEAD"], cwd=repo, capture=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def validate_workspace(layout: StackLayout, modes: tuple[InstallMode, ...]) -> list[str]:
    problems: list[str] = []
    for component in selected_components(modes):
        root = (
            layout.agent_root
            if component.repository == "SagaSmith-agent"
            else layout.repo(component.repository)
        )
        if not root.is_dir():
            problems.append(f"missing repository: {root}")
            continue
        for relative in component.required_paths:
            if not (root / relative).exists():
                problems.append(f"missing {component.repository}/{relative}")
    return problems


def install_workspace(
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
    *,
    build_ui: bool = True,
    verify_only: bool = False,
    source: str = "workspace",
    release_ref: str = "manifest",
) -> StackState:
    """Install selected source checkouts without importing or activating content Packs."""
    if not verify_only:
        layout.ensure()
    problems = validate_workspace(layout, modes)
    if problems:
        raise StackError("workspace validation failed:\n- " + "\n- ".join(problems))
    uv = _which("uv")
    if build_ui:
        _which("npm")
    selected = set(modes)
    if not verify_only:
        for mode, repository in (
            (InstallMode.DND, "sagasmith-dnd"),
            (InstallMode.COC, "sagasmith-coc"),
            (InstallMode.NARRATIVE, "sagasmith-narrative"),
        ):
            if mode in selected:
                _run(
                    [uv, "sync", "--all-packages", "--all-extras", "--frozen"],
                    cwd=layout.repo(repository),
                )
        _run([uv, "sync", "--all-extras", "--frozen"], cwd=layout.agent_root)
        if build_ui:
            _run(["npm", "ci"], cwd=layout.agent_root / "webui")
            _run(["npm", "run", "build"], cwd=layout.agent_root / "webui")
            if InstallMode.DND in selected:
                root = layout.repo("sagasmith-dnd")
                _run(["npm", "ci"], cwd=root)
                _run(["npm", "run", "build:ui"], cwd=root)
            if InstallMode.COC in selected:
                root = layout.repo("sagasmith-coc")
                _run(["npm", "ci"], cwd=root)
                _run(["npm", "run", "build:ui"], cwd=root)

    if verify_only:
        state = layout.load_state()
        report = doctor(
            layout,
            modes=modes,
            include_runtime=False,
            require_ui=build_ui,
        )
        failed = [item for item in report["checks"] if not item["ok"]]
        if failed:
            raise StackError(
                "installation verification failed:\n- "
                + "\n- ".join(x["detail"] for x in failed)
            )
        return state

    configure_agent(layout, modes)
    state = layout.load_state()
    previous = dict(state.component_revisions)
    revisions = {}
    for component in selected_components(modes):
        root = (
            layout.agent_root
            if component.repository == "SagaSmith-agent"
            else layout.repo(component.repository)
        )
        revisions[component.repository] = _git_revision(root)
    state.modes = [mode.value for mode in modes]
    state.source = source
    state.release_ref = release_ref
    state.workspace_root = str(layout.workspace_root)
    state.config_path = str(layout.config_path)
    state.installed_at = utc_now()
    state.revision += 1
    state.previous_revisions = previous
    state.component_revisions = revisions
    layout.save_state(state)
    report = doctor(
        layout,
        modes=modes,
        include_runtime=False,
        require_ui=build_ui,
    )
    failed = [item for item in report["checks"] if not item["ok"]]
    if failed:
        raise StackError("installation verification failed:\n- " + "\n- ".join(x["detail"] for x in failed))
    return state


def materialize_release(
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
    *,
    ref: str | None = None,
    manifest_path: Path | None = None,
) -> StackLayout:
    """Create/update immutable release checkouts without mutating the developer workspace."""

    git = _which("git")
    revisions = (
        {component.repository: ref for component in selected_components(modes)}
        if ref
        else load_release_revisions(
            manifest_path or layout.agent_root / "sagasmith-stack-lock.json",
            modes,
        )
    )
    release_root = layout.state_root / "releases" / "current"
    release_root.mkdir(parents=True, exist_ok=True)
    for component in selected_components(modes):
        if component.repository == "SagaSmith-agent":
            continue
        target = release_root / component.repository
        url = f"https://github.com/SagaSmithAI/{component.repository}.git"
        if not target.exists():
            _run([git, "clone", "--filter=blob:none", url, str(target)], cwd=release_root)
        dirty = _run([git, "status", "--porcelain"], cwd=target, capture=True).stdout.strip()
        if dirty:
            raise StackError(f"release checkout is dirty: {target}")
        revision = revisions.get(component.repository)
        if not revision:
            raise StackError(f"release lock is missing component: {component.repository}")
        _run([git, "fetch", "origin", revision], cwd=target)
        _run([git, "checkout", "--detach", "FETCH_HEAD"], cwd=target)
    return StackLayout.discover(
        agent_root=layout.agent_root,
        workspace_root=release_root,
        state_root=layout.state_root,
        config_path=layout.config_path,
    )


def _command_specs(
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
) -> list[tuple[str, list[str], Path, dict[str, str], str | None]]:
    selected = set(modes)
    specs: list[tuple[str, list[str], Path, dict[str, str], str | None]] = []
    if InstallMode.DND in selected:
        root = layout.repo("sagasmith-dnd")
        python = str(_venv_python(root))
        env = dnd_environment(layout)
        specs.extend(
            [
                ("dnd_mcp", [python, "-m", "sagasmith_dnd_mcp.server"], root, env, "http://127.0.0.1:8767/mcp"),
                ("dnd_gateway", [python, "-m", "sagasmith_dnd_mcp.gateway"], root, env, "http://127.0.0.1:8766/api/health"),
            ]
        )
    if InstallMode.COC in selected:
        root = layout.repo("sagasmith-coc")
        python = str(_venv_python(root))
        env = coc_environment(layout)
        specs.extend(
            [
                ("coc_mcp", [python, "-m", "sagasmith_coc_mcp.server"], root, env, "http://127.0.0.1:8769/mcp"),
                ("coc_gateway", [python, "-m", "sagasmith_coc_mcp.gateway"], root, env, "http://127.0.0.1:8768/api/health"),
            ]
        )
    agent_python = str(_venv_python(layout.agent_root))
    specs.append(
        (
            "agent",
            [agent_python, "-m", "nanobot", "gateway", "--foreground", "--config", str(layout.config_path)],
            layout.agent_root,
            {},
            agent_webui_url(layout),
        )
    )
    return specs


def _is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, PermissionError):
        return False
    return True


def _wait_ready(url: str, process: subprocess.Popen[bytes], timeout: float = 35.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StackError(f"process exited before becoming ready: {url}")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise StackError(f"service did not become ready: {url}")


def start(layout: StackLayout, *, foreground: bool = False) -> StackState:
    state = layout.load_state()
    modes = state.selected_modes
    if not modes:
        raise StackError("no SagaSmith modes are installed")
    if any(_is_running(record.pid) for record in state.processes):
        raise StackError("SagaSmith local stack is already running")
    layout.ensure()
    records: list[ProcessRecord] = []
    opened: list[BinaryIO] = []
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for name, command, cwd, extra_env, health_url in _command_specs(layout, modes):
            log_path = layout.logs_dir / f"{name}.log"
            stream = log_path.open("ab", buffering=0)
            opened.append(stream)
            env = os.environ.copy()
            env.update(extra_env)
            flags = 0
            if os.name == "nt" and not foreground:
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                start_new_session=os.name != "nt" and not foreground,
            )
            processes.append(process)
            records.append(
                ProcessRecord(name, process.pid, command, str(cwd), str(log_path), utc_now())
            )
            state.processes = records
            layout.save_state(state)
            if health_url:
                _wait_ready(health_url, process)
        atomic_json_write(layout.runtime_file, {"schema": state.schema, "processes": [asdict(x) for x in records]})
        if foreground:
            processes[-1].wait()
            stop(layout)
        return state
    except BaseException:
        for process in reversed(processes):
            _stop_pid(process.pid)
        state.processes = []
        layout.save_state(state)
        layout.runtime_file.unlink(missing_ok=True)
        raise
    finally:
        for stream in opened:
            stream.close()


def _stop_pid(pid: int) -> None:
    if not _is_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        with suppress(OSError):
            os.kill(pid, signal.SIGTERM)


def stop(layout: StackLayout) -> list[str]:
    state = layout.load_state()
    stopped: list[str] = []
    for record in reversed(state.processes):
        if _is_running(record.pid):
            _stop_pid(record.pid)
            stopped.append(record.name)
    state.processes = []
    layout.save_state(state)
    layout.runtime_file.unlink(missing_ok=True)
    return stopped


def status(layout: StackLayout) -> dict[str, Any]:
    state = layout.load_state()
    processes = [
        {**asdict(record), "running": _is_running(record.pid)} for record in state.processes
    ]
    return {
        "schema": state.schema,
        "installed": bool(state.modes),
        "modes": list(state.modes),
        "source": state.source,
        "revision": state.revision,
        "state_root": str(layout.state_root),
        "processes": processes,
        "running": bool(processes) and all(item["running"] for item in processes),
        "workbenches": {
            "agent": agent_webui_url(layout),
            **({"dnd": "http://127.0.0.1:8766/"} if "dnd" in state.modes else {}),
            **({"coc": "http://127.0.0.1:8768/"} if "coc" in state.modes else {}),
        },
    }


def _check_port(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def doctor(
    layout: StackLayout,
    *,
    modes: tuple[InstallMode, ...] | None = None,
    include_runtime: bool = True,
    require_ui: bool = False,
) -> dict[str, Any]:
    state = layout.load_state()
    selected = modes or state.selected_modes
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    for problem in validate_workspace(layout, selected):
        add("workspace", False, problem)
    if not any(item["name"] == "workspace" for item in checks):
        add("workspace", True, "all selected repositories and contracts exist")
    config_ok = layout.config_path.exists()
    config_detail = str(layout.config_path)
    if config_ok:
        try:
            config_value = json.loads(layout.config_path.read_text(encoding="utf-8"))
            actual_servers = dict(config_value.get("tools", {}).get("mcpServers", {}))
            expected_names = set(desired_servers(layout, selected))
            actual_owned = set(actual_servers) & {
                "sagasmith_dnd",
                "sagasmith_coc",
                "sagasmith_narrative",
            }
            skills = config_value.get("agents", {}).get("defaults", {}).get(
                "externalSkillsDirs", []
            )
            config_ok = actual_owned == expected_names and all(
                actual_servers[name].get("sessionScoped") is True
                for name in expected_names
            ) and all(root in skills for root in desired_skill_roots(layout, selected))
            if not config_ok:
                config_detail = "SagaSmith MCP or Skill entries do not match selected modes"
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError):
            config_ok = False
            config_detail = "Agent config is not valid UTF-8 JSON"
    add("agent-config", config_ok, config_detail)
    for mode, repository, module in (
        (InstallMode.DND, "sagasmith-dnd", "sagasmith_dnd_mcp"),
        (InstallMode.COC, "sagasmith-coc", "sagasmith_coc_mcp"),
        (InstallMode.NARRATIVE, "sagasmith-narrative", "sagasmith_narrative_mcp"),
    ):
        if mode not in selected:
            continue
        repo = layout.repo(repository)
        try:
            python = _venv_python(repo)
            _run([str(python), "-c", f"import {module}"], cwd=repo, capture=True)
        except (StackError, subprocess.CalledProcessError) as exc:
            add(f"{mode.value}-runtime", False, str(exc))
        else:
            add(f"{mode.value}-runtime", True, str(python))
    for mode, module_skill in (
        (
            InstallMode.DND,
            layout.repo("sagasmith-dnd") / "skills" / "dnd-module-generator" / "SKILL.md",
        ),
        (
            InstallMode.COC,
            layout.repo("sagasmith-coc") / "skills" / "coc-module-generator" / "SKILL.md",
        ),
        (
            InstallMode.NARRATIVE,
            layout.repo("sagasmith-narrative")
            / "skills"
            / "narrative-project-generator"
            / "SKILL.md",
        ),
    ):
        if mode in selected:
            add(f"{mode.value}-generator-skill", module_skill.exists(), str(module_skill))
    if require_ui:
        add(
            "agent-ui",
            (layout.agent_root / "nanobot" / "web" / "dist" / "index.html").is_file(),
            str(layout.agent_root / "nanobot" / "web" / "dist" / "index.html"),
        )
        if InstallMode.DND in selected:
            path = layout.repo("sagasmith-dnd") / "apps" / "ui" / "dist" / "index.html"
            add("dnd-ui", path.is_file(), str(path))
        if InstallMode.COC in selected:
            path = layout.repo("sagasmith-coc") / "apps" / "ui" / "dist" / "index.html"
            add("coc-ui", path.is_file(), str(path))
    if include_runtime:
        agent_port = int(agent_webui_url(layout).split(":")[-1].rstrip("/"))
        for name, port in (("agent", agent_port), ("dnd-gateway", 8766), ("dnd-mcp", 8767), ("coc-gateway", 8768), ("coc-mcp", 8769)):
            if name.startswith("dnd") and InstallMode.DND not in selected:
                continue
            if name.startswith("coc") and InstallMode.COC not in selected:
                continue
            add(f"port-{name}", _check_port("127.0.0.1", port), f"127.0.0.1:{port}")
    return {"ok": all(item["ok"] for item in checks), "modes": [m.value for m in selected], "checks": checks}


def tail_logs(layout: StackLayout, component: str | None = None, lines: int = 100) -> str:
    names = [component] if component else [record.name for record in layout.load_state().processes]
    output: list[str] = []
    for name in names:
        path = layout.logs_dir / f"{name}.log"
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        output.append(f"== {name} ==")
        output.extend(content[-max(1, lines) :])
    return "\n".join(output)


def backup(layout: StackLayout, destination: Path | None = None) -> Path:
    state = layout.load_state()
    if any(_is_running(record.pid) for record in state.processes):
        raise StackError("stop the local stack before creating a consistent backup")
    layout.ensure()
    target = destination or layout.backups_dir / f"sagasmith-{datetime.now():%Y%m%d-%H%M%S}.zip"
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {"schema": state.schema, "created_at": utc_now(), "modes": state.modes}
        archive.writestr("backup.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for root, prefix in ((layout.data_dir, "data"), (layout.state_file, "stack.json")):
            if isinstance(root, Path) and root.is_file():
                archive.write(root, prefix)
            elif isinstance(root, Path) and root.is_dir():
                for path in root.rglob("*"):
                    if path.is_file():
                        archive.write(path, str(PurePosixPath(prefix, *path.relative_to(root).parts)))
    return target


def verify_backup(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    with zipfile.ZipFile(resolved) as archive:
        names = archive.namelist()
        if "backup.json" not in names:
            raise StackError("backup manifest is missing")
        for name in names:
            item = PurePosixPath(name)
            if item.is_absolute() or ".." in item.parts:
                raise StackError(f"unsafe backup member: {name}")
        manifest = json.loads(archive.read("backup.json"))
        if manifest.get("schema") != StackState().schema:
            raise StackError("unsupported backup schema")
    return {"ok": True, "path": str(resolved), "files": len(names), "manifest": manifest}


def restore(layout: StackLayout, source: Path) -> StackState:
    state = layout.load_state()
    if any(_is_running(record.pid) for record in state.processes):
        raise StackError("stop the local stack before restoring a backup")
    verify_backup(source)
    staging = layout.state_root / ".restore-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    previous_data = layout.state_root / ".restore-previous-data"
    moved_previous = False
    try:
        with zipfile.ZipFile(source.expanduser().resolve()) as archive:
            archive.extractall(staging)
        restored_data = staging / "data"
        if previous_data.exists():
            shutil.rmtree(previous_data)
        if layout.data_dir.exists():
            os.replace(layout.data_dir, previous_data)
            moved_previous = True
        if restored_data.exists():
            os.replace(restored_data, layout.data_dir)
        restored_state = staging / "stack.json"
        if restored_state.exists():
            value = json.loads(restored_state.read_text(encoding="utf-8"))
            state = StackState.from_dict(value)
            state.processes = []
            state.revision += 1
            layout.save_state(state)
        if previous_data.exists():
            shutil.rmtree(previous_data)
            moved_previous = False
    except BaseException:
        if moved_previous and previous_data.exists() and not layout.data_dir.exists():
            os.replace(previous_data, layout.data_dir)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return state


def upgrade(layout: StackLayout, *, build_ui: bool = True) -> StackState:
    state = layout.load_state()
    if state.source not in {"workspace", "release"}:
        raise StackError(f"unsupported install source: {state.source}")
    if any(_is_running(record.pid) for record in state.processes):
        raise StackError("stop the local stack before upgrading")
    modes = state.selected_modes
    previous: dict[str, str] = {}
    for component in selected_components(modes):
        if state.source == "release" and component.repository == "SagaSmith-agent":
            continue
        repo = (
            layout.agent_root
            if component.repository == "SagaSmith-agent"
            else layout.repo(component.repository)
        )
        status_result = _run(["git", "status", "--porcelain"], cwd=repo, capture=True)
        if status_result.stdout.strip():
            raise StackError(f"cannot upgrade dirty repository: {component.repository}")
        previous[component.repository] = _git_revision(repo)
    backup(layout)
    if state.source == "release":
        materialize_release(
            layout,
            modes,
            ref=None if state.release_ref == "manifest" else state.release_ref,
        )
    else:
        for component in selected_components(modes):
            repo = (
                layout.agent_root
                if component.repository == "SagaSmith-agent"
                else layout.repo(component.repository)
            )
            _run(["git", "fetch", "origin"], cwd=repo)
            branch = _run(
                ["git", "branch", "--show-current"], cwd=repo, capture=True
            ).stdout.strip()
            if branch:
                _run(["git", "merge", "--ff-only", f"origin/{branch}"], cwd=repo)
    result = install_workspace(
        layout,
        modes,
        build_ui=build_ui,
        source=state.source,
        release_ref=state.release_ref,
    )
    result.previous_revisions = previous
    layout.save_state(result)
    return result


def rollback(layout: StackLayout, *, build_ui: bool = True) -> StackState:
    state = layout.load_state()
    if not state.previous_revisions:
        raise StackError("no previous component revisions are recorded")
    if any(_is_running(record.pid) for record in state.processes):
        raise StackError("stop the local stack before rolling back")
    for repository, revision in state.previous_revisions.items():
        repo = layout.agent_root if repository == "SagaSmith-agent" else layout.repo(repository)
        dirty = _run(["git", "status", "--porcelain"], cwd=repo, capture=True).stdout.strip()
        if dirty:
            raise StackError(f"cannot roll back dirty repository: {repository}")
        _run(["git", "checkout", "--detach", revision], cwd=repo)
    return install_workspace(
        layout,
        state.selected_modes,
        build_ui=build_ui,
        source=state.source,
        release_ref=state.release_ref,
    )


def uninstall(layout: StackLayout, *, purge_data: bool = False) -> StackState:
    stop(layout)
    configure_agent(layout, ())
    state = layout.load_state()
    state.modes = []
    state.processes = []
    state.revision += 1
    layout.save_state(state)
    if purge_data and layout.data_dir.exists():
        shutil.rmtree(layout.data_dir)
    return state
