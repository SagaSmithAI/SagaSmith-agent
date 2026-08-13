"""Owned-field reconciliation for Agent configuration and domain environments."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .model import InstallMode, StackLayout, atomic_json_write

LOOPBACK_CIDR = "127.0.0.1/32"
OWNED_SERVERS = frozenset({"sagasmith_dnd", "sagasmith_coc", "sagasmith_narrative"})


def _python_executable(repo: Path) -> Path:
    windows = repo / ".venv" / "Scripts" / "python.exe"
    return windows if os.name == "nt" else repo / ".venv" / "bin" / "python"


def desired_servers(layout: StackLayout, modes: tuple[InstallMode, ...]) -> dict[str, Any]:
    selected = set(modes)
    result: dict[str, Any] = {}
    if InstallMode.DND in selected:
        result["sagasmith_dnd"] = {
            "type": "streamableHttp",
            "url": "http://127.0.0.1:8767/mcp",
            "headers": {},
            "toolTimeout": 900,
            "enabledTools": ["*"],
            "exposeResourcesAndPrompts": True,
            "injectPrincipal": True,
            "sessionScoped": True,
        }
    if InstallMode.COC in selected:
        result["sagasmith_coc"] = {
            "type": "streamableHttp",
            "url": "http://127.0.0.1:8769/mcp",
            "headers": {},
            "toolTimeout": 900,
            "enabledTools": ["*"],
            "exposeResourcesAndPrompts": True,
            "injectPrincipal": True,
            "sessionScoped": True,
        }
    if InstallMode.NARRATIVE in selected:
        repo = layout.repo("SagaSmith-narrative-mcp")
        result["sagasmith_narrative"] = {
            "type": "stdio",
            "command": str(_python_executable(repo)),
            "args": ["-m", "sagasmith_narrative_mcp.server"],
            "cwd": str(repo),
            "env": narrative_environment(layout),
            "toolTimeout": 900,
            "enabledTools": ["*"],
            "exposeResourcesAndPrompts": True,
            "injectPrincipal": True,
            "sessionScoped": True,
        }
    return result


def desired_skill_roots(layout: StackLayout, modes: tuple[InstallMode, ...]) -> list[str]:
    selected = set(modes)
    result: list[str] = []
    if InstallMode.DND in selected:
        result.append(str(layout.repo("SagaSmith-dnd-skills") / "full" / "skills"))
    if InstallMode.COC in selected:
        result.append(str(layout.repo("SagaSmith-coc-skills") / "full" / "skills"))
    if InstallMode.NARRATIVE in selected:
        result.append(str(layout.repo("SagaSmith-narrative-skills") / "skills"))
    if selected:
        result.append(str(layout.repo("SagaSmith-module-gen-skills")))
    return result


def _is_owned_skill_path(value: str, layout: StackLayout) -> bool:
    try:
        path = Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    roots = (
        layout.repo("SagaSmith-dnd-skills"),
        layout.repo("SagaSmith-coc-skills"),
        layout.repo("SagaSmith-narrative-skills"),
        layout.repo("SagaSmith-module-gen-skills"),
    )
    return any(path == root or root in path.parents for root in roots)


def reconcile_agent_config(
    config: dict[str, Any],
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
) -> dict[str, Any]:
    result = dict(config)
    tools = dict(result.get("tools") or {})
    servers = dict(tools.get("mcpServers") or {})
    for name in OWNED_SERVERS:
        servers.pop(name, None)
    servers.update(desired_servers(layout, modes))
    tools["mcpServers"] = servers
    whitelist = [str(item) for item in tools.get("ssrfWhitelist") or []]
    if LOOPBACK_CIDR not in whitelist:
        whitelist.append(LOOPBACK_CIDR)
    tools["ssrfWhitelist"] = whitelist
    result["tools"] = tools

    agents = dict(result.get("agents") or {})
    defaults = dict(agents.get("defaults") or {})
    current = [str(item) for item in defaults.get("externalSkillsDirs") or []]
    preserved = [item for item in current if not _is_owned_skill_path(item, layout)]
    defaults["externalSkillsDirs"] = [*preserved, *desired_skill_roots(layout, modes)]
    agents["defaults"] = defaults
    result["agents"] = agents
    return result


def configure_agent(layout: StackLayout, modes: tuple[InstallMode, ...]) -> bool:
    if layout.config_path.exists():
        try:
            current = json.loads(layout.config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read Agent config {layout.config_path}: {exc}") from exc
        if not isinstance(current, dict):
            raise ValueError("Agent config root must be an object")
    else:
        current = {}
    desired = reconcile_agent_config(current, layout, modes)
    if desired == current:
        return False
    if layout.config_path.exists():
        backup = layout.config_path.with_suffix(layout.config_path.suffix + ".bak")
        shutil.copy2(layout.config_path, backup)
    atomic_json_write(layout.config_path, desired)
    return True


def agent_webui_url(layout: StackLayout) -> str:
    port = 8765
    if layout.config_path.is_file():
        try:
            value = json.loads(layout.config_path.read_text(encoding="utf-8"))
            configured = value.get("channels", {}).get("websocket", {}).get("port")
            if configured is not None:
                port = int(configured)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return f"http://127.0.0.1:{port}/"


def dnd_environment(layout: StackLayout) -> dict[str, str]:
    values = {
        "SAGASMITH_DND_MCP_HOME": str(layout.data_dir / "dnd"),
        "SAGASMITH_DND_SKILLS_DIR": str(layout.repo("SagaSmith-dnd-skills")),
        "SAGASMITH_MODULEGEN_SKILLS_DIR": str(layout.repo("SagaSmith-module-gen-skills")),
        "SAGASMITH_DND_MCP_TRANSPORT": "streamable-http",
        "SAGASMITH_DND_MCP_HTTP_HOST": "127.0.0.1",
        "SAGASMITH_DND_MCP_HTTP_PORT": "8767",
        "SAGASMITH_DND_MCP_URL": "http://127.0.0.1:8767/mcp",
        "SAGASMITH_DND_GATEWAY_HOST": "127.0.0.1",
        "SAGASMITH_DND_GATEWAY_PORT": "8766",
        "SAGASMITH_DND_UI_DIST": str(layout.repo("SagaSmith-dnd-ui") / "dist"),
        "SAGASMITH_AGENT_WEBUI_URL": agent_webui_url(layout),
    }
    rule_root = layout.workspace_root / "reference" / "DnD-Books" / "5e" / "Books"
    module_root = layout.workspace_root / "reference" / "DnD-Books" / "5e" / "Campaign"
    if configured := os.environ.get("SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS"):
        values["SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS"] = configured
    elif rule_root.exists():
        values["SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS"] = str(rule_root)
    if configured := os.environ.get("SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS"):
        values["SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS"] = configured
    elif module_root.exists():
        values["SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS"] = str(module_root)
    for name, default in (
        ("SAGASMITH_DND_MCP_RULE_OCR", "1"),
        ("SAGASMITH_DND_MCP_RULE_OCR_SCALE", "2.0"),
        ("SAGASMITH_DND_MCP_MODULE_OCR", "1"),
        ("SAGASMITH_DND_MCP_MODULE_OCR_SCALE", "2.0"),
    ):
        values[name] = os.environ.get(name, default)
    return values


def coc_environment(layout: StackLayout) -> dict[str, str]:
    values = {
        "SAGASMITH_COC_MCP_HOME": str(layout.data_dir / "coc"),
        "SAGASMITH_COC_SKILLS_DIR": str(layout.repo("SagaSmith-coc-skills")),
        "SAGASMITH_MODULEGEN_SKILLS_DIR": str(layout.repo("SagaSmith-module-gen-skills")),
        "SAGASMITH_COC_MCP_TRANSPORT": "streamable-http",
        "SAGASMITH_COC_MCP_HTTP_HOST": "127.0.0.1",
        "SAGASMITH_COC_MCP_HTTP_PORT": "8769",
        "SAGASMITH_COC_MCP_URL": "http://127.0.0.1:8769/mcp",
        "SAGASMITH_COC_GATEWAY_HOST": "127.0.0.1",
        "SAGASMITH_COC_GATEWAY_PORT": "8768",
        "SAGASMITH_COC_UI_DIST": str(layout.repo("sagasmith-coc-ui") / "dist"),
    }
    if configured := os.environ.get("SAGASMITH_COC_MCP_MODULE_IMPORT_ROOTS"):
        values["SAGASMITH_COC_MCP_MODULE_IMPORT_ROOTS"] = configured
    return values


def narrative_environment(layout: StackLayout) -> dict[str, str]:
    return {
        "SAGASMITH_NARRATIVE_MCP_HOME": str(layout.data_dir / "narrative"),
        "SAGASMITH_NARRATIVE_SKILLS_DIR": str(layout.repo("SagaSmith-narrative-skills")),
        "SAGASMITH_MODULEGEN_SKILLS_DIR": str(layout.repo("SagaSmith-module-gen-skills")),
    }
