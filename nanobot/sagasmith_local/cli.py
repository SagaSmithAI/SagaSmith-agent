"""Typer command surface for the SagaSmith local stack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .configuration import configure_agent
from .model import InstallMode, StackLayout, normalize_modes
from .runtime import (
    StackError,
    backup,
    doctor,
    install_workspace,
    materialize_release,
    restore,
    rollback,
    start,
    status,
    stop,
    tail_logs,
    uninstall,
    upgrade,
    verify_backup,
)

app = typer.Typer(
    help="Install and operate optional D&D, CoC, and Narrative local services.",
    no_args_is_help=True,
)
console = Console()
ModeOption = Annotated[
    list[str] | None,
    typer.Option(
        "--mode",
        "-m",
        help="Mode to include (dnd, coc, narrative). Repeat the option; omitted means all three.",
    ),
]


def _layout(
    workspace_root: Path | None,
    state_root: Path | None,
    config: Path | None,
) -> StackLayout:
    return StackLayout.discover(
        workspace_root=workspace_root,
        state_root=state_root,
        config_path=config,
    )


def _modes(values: list[str] | None) -> tuple[InstallMode, ...]:
    try:
        return normalize_modes(values)
    except ValueError as exc:
        raise typer.BadParameter("mode must be dnd, coc, or narrative") from exc


def _emit(value: object, *, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(value, ensure_ascii=False, default=str))
    else:
        console.print(value)


def _fail(exc: BaseException) -> None:
    console.print(f"[red]{exc}[/red]")
    raise typer.Exit(1) from exc


@app.command("install")
def install_command(
    mode: ModeOption = None,
    workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    build_ui: bool = typer.Option(True, "--build-ui/--skip-ui"),
    verify_only: bool = typer.Option(False, "--verify-only"),
    source: str = typer.Option("workspace", "--source", help="workspace or release"),
    release_ref: str | None = typer.Option(
        None,
        "--release-ref",
        help="Override the per-component release lock with one coordinated tag or ref.",
    ),
    release_manifest: Path | None = typer.Option(
        None,
        "--release-manifest",
        help="Immutable per-component release lock; defaults to sagasmith-stack-lock.json.",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Install any selected combination; omission of --mode selects all three."""
    layout = _layout(workspace_root, state_root, config)
    try:
        selected = _modes(mode)
        if source not in {"workspace", "release"}:
            raise ValueError("source must be workspace or release")
        if source == "release":
            layout = materialize_release(
                layout,
                selected,
                ref=release_ref,
                manifest_path=release_manifest,
            )
        state = install_workspace(
            layout,
            selected,
            build_ui=build_ui,
            verify_only=verify_only,
            source=source,
            release_ref=release_ref or "manifest",
        )
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit(
        {
            "ok": True,
            "modes": state.modes,
            "state_root": str(layout.state_root),
            "config": str(layout.config_path),
            "next": "nanobot sagasmith start",
        },
        as_json=as_json,
    )


@app.command("configure")
def configure_command(
    mode: ModeOption = None,
    workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Reconcile only SagaSmith-owned Agent config fields."""
    layout = _layout(workspace_root, state_root, config)
    try:
        selected = _modes(mode)
        changed = configure_agent(layout, selected)
        state = layout.load_state()
        state.modes = [item.value for item in selected]
        state.workspace_root = str(layout.workspace_root)
        state.config_path = str(layout.config_path)
        state.revision += 1
        layout.save_state(state)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit({"ok": True, "changed": changed, "modes": state.modes}, as_json=as_json)


@app.command("start")
def start_command(
    foreground: bool = typer.Option(False, "--foreground"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Start the selected authoritative services, Workbenches, and Agent."""
    layout = _layout(None, state_root, config)
    try:
        start(layout, foreground=foreground)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit(status(layout), as_json=as_json)


@app.command("stop")
def stop_command(
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Stop only processes recorded as part of this local stack."""
    layout = _layout(None, state_root, None)
    try:
        stopped = stop(layout)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit({"ok": True, "stopped": stopped}, as_json=as_json)


@app.command("status")
def status_command(
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show selected modes, exact child processes, and Workbench URLs."""
    layout = _layout(None, state_root, None)
    payload = status(layout)
    if as_json:
        _emit(payload, as_json=True)
        return
    table = Table(title="SagaSmith Local Stack")
    table.add_column("Mode")
    table.add_column("Installed")
    selected = set(payload["modes"])
    for item in InstallMode:
        table.add_row(item.value, "yes" if item.value in selected else "no")
    console.print(table)
    console.print(f"Running: {payload['running']}")
    for name, url in payload["workbenches"].items():
        console.print(f"{name}: {url}")


@app.command("doctor")
def doctor_command(
    mode: ModeOption = None,
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Validate paths, imports, configuration, Skills, and live ports."""
    layout = _layout(None, state_root, None)
    try:
        selected = _modes(mode) if mode else None
        payload = doctor(layout, modes=selected)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    if as_json:
        _emit(payload, as_json=True)
    else:
        for item in payload["checks"]:
            marker = "[green]OK[/green]" if item["ok"] else "[red]FAIL[/red]"
            console.print(f"{marker} {item['name']}: {item['detail']}")
    if not payload["ok"]:
        raise typer.Exit(1)


@app.command("logs")
def logs_command(
    component: str | None = typer.Argument(None),
    lines: int = typer.Option(100, "--lines", min=1, max=10000),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """Print recent logs for one component or every recorded component."""
    console.print(tail_logs(_layout(None, state_root, None), component, lines))


@app.command("backup")
def backup_command(
    destination: Path | None = typer.Argument(None),
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a stopped-state backup without provider secrets or source content."""
    try:
        path = backup(_layout(None, state_root, None), destination)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit({"ok": True, "backup": str(path)}, as_json=as_json)


@app.command("verify-backup")
def verify_backup_command(
    source: Path = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Validate a backup manifest and archive paths without extracting it."""
    try:
        payload = verify_backup(source)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit(payload, as_json=as_json)


@app.command("restore")
def restore_command(
    source: Path = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", help="Confirm replacement of local domain data."),
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Restore all selected domain stores from a validated stopped-state backup."""
    if not yes:
        raise typer.BadParameter("restore requires --yes")
    try:
        state = restore(_layout(None, state_root, None), source)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit({"ok": True, "modes": state.modes, "revision": state.revision}, as_json=as_json)


@app.command("upgrade")
def upgrade_command(
    build_ui: bool = typer.Option(True, "--build-ui/--skip-ui"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Back up, fast-forward clean selected repositories, reinstall, and verify."""
    try:
        state = upgrade(_layout(None, state_root, None), build_ui=build_ui)
    except (StackError, ValueError, OSError, RuntimeError) as exc:
        _fail(exc)
    _emit({"ok": True, "revision": state.revision, "components": state.component_revisions}, as_json=as_json)


@app.command("rollback")
def rollback_command(
    yes: bool = typer.Option(False, "--yes"),
    build_ui: bool = typer.Option(True, "--build-ui/--skip-ui"),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """Return clean selected repositories to the revisions recorded before upgrade."""
    if not yes:
        raise typer.BadParameter("rollback requires --yes")
    try:
        state = rollback(_layout(None, state_root, None), build_ui=build_ui)
    except (StackError, ValueError, OSError, RuntimeError) as exc:
        _fail(exc)
    _emit({"ok": True, "revision": state.revision}, as_json=False)


@app.command("uninstall")
def uninstall_command(
    yes: bool = typer.Option(False, "--yes"),
    purge_data: bool = typer.Option(False, "--purge-data"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Remove managed config; preserve domain data unless --purge-data is explicit."""
    if not yes:
        raise typer.BadParameter("uninstall requires --yes")
    try:
        state = uninstall(_layout(None, state_root, None), purge_data=purge_data)
    except (StackError, ValueError, OSError) as exc:
        _fail(exc)
    _emit({"ok": True, "purged_data": purge_data, "revision": state.revision}, as_json=as_json)
