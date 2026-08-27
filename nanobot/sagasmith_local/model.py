"""Declarative model and filesystem layout for the local SagaSmith stack."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class InstallMode(StrEnum):
    DND = "dnd"
    COC = "coc"
    NARRATIVE = "narrative"


class InstallProfile(StrEnum):
    DND_ONLY = "dnd-only"
    COC_ONLY = "coc-only"
    NARRATIVE_ONLY = "narrative-only"
    MULTI_SYSTEM = "multi-system"


class McpTransport(StrEnum):
    MIXED = "mixed"
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


ALL_MODES = tuple(InstallMode)
PROFILE_MODES: dict[InstallProfile, tuple[InstallMode, ...]] = {
    InstallProfile.DND_ONLY: (InstallMode.DND,),
    InstallProfile.COC_ONLY: (InstallMode.COC,),
    InstallProfile.NARRATIVE_ONLY: (InstallMode.NARRATIVE,),
    InstallProfile.MULTI_SYSTEM: ALL_MODES,
}
STACK_SCHEMA = "sagasmith.local-stack/v1"
RELEASE_LOCK_SCHEMA = "sagasmith.release-lock/v2"


@dataclass(frozen=True)
class Component:
    mode: InstallMode | None
    repository: str
    kind: str
    required_paths: tuple[str, ...] = ()


COMPONENTS: tuple[Component, ...] = (
    Component(None, "SagaSmith-agent", "python", ("pyproject.toml",)),
    Component(None, "sagasmith-core", "source", ("pyproject.toml",)),
    Component(
        InstallMode.DND,
        "sagasmith-dnd",
        "domain",
        (
            "pyproject.toml",
            "packages/domain/pyproject.toml",
            "packages/mcp/pyproject.toml",
            "skills/full/SKILL.md",
            "skills/dnd-module-generator/SKILL.md",
            "apps/ui/package.json",
        ),
    ),
    Component(
        InstallMode.COC,
        "sagasmith-coc",
        "domain",
        (
            "pyproject.toml",
            "packages/domain/pyproject.toml",
            "packages/mcp/pyproject.toml",
            "skills/full/SKILL.md",
            "skills/coc-module-generator/SKILL.md",
            "apps/ui/package.json",
        ),
    ),
    Component(
        InstallMode.NARRATIVE,
        "sagasmith-narrative",
        "domain",
        (
            "pyproject.toml",
            "packages/domain/pyproject.toml",
            "packages/mcp/pyproject.toml",
            "skills/narrative-project-generator/SKILL.md",
        ),
    ),
)


@dataclass
class ProcessRecord:
    name: str
    pid: int
    command: list[str]
    cwd: str
    log: str
    started_at: str


@dataclass
class StackState:
    schema: str = STACK_SCHEMA
    modes: list[str] = field(default_factory=list)
    source: str = "workspace"
    release_ref: str = "manifest"
    mcp_transport: str = McpTransport.MIXED.value
    workspace_root: str = ""
    config_path: str = ""
    installed_at: str = ""
    revision: int = 0
    processes: list[ProcessRecord] = field(default_factory=list)
    component_revisions: dict[str, str] = field(default_factory=dict)
    previous_revisions: dict[str, str] = field(default_factory=dict)

    @property
    def selected_modes(self) -> tuple[InstallMode, ...]:
        return tuple(InstallMode(value) for value in self.modes)

    @property
    def selected_transport(self) -> McpTransport:
        return McpTransport(self.mcp_transport)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StackState":
        if value.get("schema") != STACK_SCHEMA:
            raise ValueError("unsupported SagaSmith local stack state schema")
        processes = [ProcessRecord(**item) for item in value.get("processes", [])]
        return cls(
            schema=value["schema"],
            modes=list(value.get("modes", [])),
            source=str(value.get("source", "workspace")),
            release_ref=str(value.get("release_ref", "manifest")),
            mcp_transport=str(value.get("mcp_transport", McpTransport.MIXED.value)),
            workspace_root=str(value.get("workspace_root", "")),
            config_path=str(value.get("config_path", "")),
            installed_at=str(value.get("installed_at", "")),
            revision=int(value.get("revision", 0)),
            processes=processes,
            component_revisions=dict(value.get("component_revisions", {})),
            previous_revisions=dict(value.get("previous_revisions", {})),
        )


@dataclass(frozen=True)
class StackLayout:
    agent_root: Path
    workspace_root: Path
    state_root: Path
    config_path: Path

    @classmethod
    def discover(
        cls,
        *,
        agent_root: Path | None = None,
        workspace_root: Path | None = None,
        state_root: Path | None = None,
        config_path: Path | None = None,
    ) -> "StackLayout":
        resolved_agent = (agent_root or Path(__file__).resolve().parents[2]).resolve()
        configured_state = os.environ.get("SAGASMITH_LOCAL_HOME")
        resolved_state = (
            state_root
            or (Path(configured_state).expanduser() if configured_state else resolved_agent / "workspace" / ".sagasmith-local")
        ).resolve()
        resolved_workspace = (workspace_root or resolved_agent.parent).resolve()
        stored_config = ""
        state_file = resolved_state / "stack.json"
        if workspace_root is None and state_file.is_file():
            try:
                stored = json.loads(state_file.read_text(encoding="utf-8"))
                stored_root = str(stored.get("workspace_root") or "").strip()
                stored_config = str(stored.get("config_path") or "").strip()
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                stored_root = ""
            if stored_root:
                resolved_workspace = Path(stored_root).expanduser().resolve()
        resolved_config = (
            config_path
            or (Path(stored_config).expanduser() if stored_config else None)
            or resolved_agent / "config" / "config.json"
        ).resolve()
        return cls(resolved_agent, resolved_workspace, resolved_state, resolved_config)

    @property
    def state_file(self) -> Path:
        return self.state_root / "stack.json"

    @property
    def runtime_file(self) -> Path:
        return self.state_root / "runtime.json"

    @property
    def logs_dir(self) -> Path:
        return self.state_root / "logs"

    @property
    def data_dir(self) -> Path:
        return self.state_root / "data"

    @property
    def backups_dir(self) -> Path:
        return self.state_root / "backups"

    def repo(self, name: str) -> Path:
        return self.workspace_root / name

    def ensure(self) -> None:
        for path in (self.state_root, self.logs_dir, self.data_dir, self.backups_dir):
            path.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> StackState:
        if not self.state_file.exists():
            return StackState(
                workspace_root=str(self.workspace_root),
                config_path=str(self.config_path),
            )
        value = json.loads(self.state_file.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("SagaSmith local stack state must be a JSON object")
        return StackState.from_dict(value)

    def save_state(self, state: StackState) -> None:
        self.ensure()
        atomic_json_write(self.state_file, asdict(state))


def selected_components(modes: tuple[InstallMode, ...]) -> tuple[Component, ...]:
    selected = set(modes)
    seen: set[str] = set()
    result: list[Component] = []
    for component in COMPONENTS:
        if component.mode is not None and component.mode not in selected:
            continue
        if component.repository in seen:
            continue
        seen.add(component.repository)
        result.append(component)
    return tuple(result)


def normalize_modes(values: list[str] | tuple[str, ...] | None) -> tuple[InstallMode, ...]:
    if not values:
        return ALL_MODES
    result: list[InstallMode] = []
    for value in values:
        mode = InstallMode(value.casefold())
        if mode not in result:
            result.append(mode)
    return tuple(result)


def normalize_profile(value: str | None) -> tuple[InstallMode, ...] | None:
    if value is None:
        return None
    return PROFILE_MODES[InstallProfile(value.casefold())]


def normalize_transport(value: str | McpTransport | None) -> McpTransport:
    if value is None:
        return McpTransport.MIXED
    return McpTransport(value)


def transport_for_mode(transport: McpTransport, mode: InstallMode) -> McpTransport:
    if transport != McpTransport.MIXED:
        return transport
    if mode in {InstallMode.DND, InstallMode.COC}:
        return McpTransport.STREAMABLE_HTTP
    return McpTransport.STDIO


def load_release_revisions(path: Path, modes: tuple[InstallMode, ...]) -> dict[str, str]:
    """Load an immutable per-component release lock for the selected modes."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read SagaSmith release lock {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != RELEASE_LOCK_SCHEMA:
        raise ValueError(f"unsupported SagaSmith release lock: {path}")
    shared = value.get("shared")
    profiles = value.get("profiles")
    if not isinstance(shared, dict) or not isinstance(profiles, dict):
        raise ValueError("SagaSmith release lock requires shared and profiles objects")
    raw_components = dict(shared)
    for mode in modes:
        profile = profiles.get(mode.value)
        if not isinstance(profile, dict):
            raise ValueError(f"release lock is missing profile: {mode.value}")
        overlap = set(raw_components) & set(profile)
        if overlap:
            raise ValueError(
                "release lock duplicates shared components in profile: "
                + ", ".join(sorted(overlap))
            )
        raw_components.update(profile)
    selected = {
        component.repository
        for component in selected_components(modes)
        if component.repository != "SagaSmith-agent"
    }
    revisions: dict[str, str] = {}
    for repository in sorted(selected):
        revision = str(raw_components.get(repository) or "").strip().casefold()
        if not revision:
            raise ValueError(f"release lock is missing component: {repository}")
        if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
            raise ValueError(f"release lock has invalid revision for {repository}")
        revisions[repository] = revision
    return revisions


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
