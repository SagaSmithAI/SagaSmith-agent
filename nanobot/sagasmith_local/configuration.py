"""Owned-field reconciliation for Agent configuration and domain environments."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from .model import (
    InstallMode,
    McpTransport,
    StackLayout,
    atomic_json_write,
    transport_for_mode,
)

LOOPBACK_CIDR = "127.0.0.1/32"
OWNED_SERVERS = frozenset({"sagasmith_dnd", "sagasmith_coc", "sagasmith_narrative"})


def _python_executable(repo: Path) -> Path:
    windows = repo / ".venv" / "Scripts" / "python.exe"
    return windows if os.name == "nt" else repo / ".venv" / "bin" / "python"


def desired_servers(
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
    *,
    transport: McpTransport = McpTransport.MIXED,
    auth_context_secret: str = "",
) -> dict[str, Any]:
    selected = set(modes)
    result: dict[str, Any] = {}
    if InstallMode.DND in selected:
        domain_transport = transport_for_mode(transport, InstallMode.DND)
        if domain_transport == McpTransport.STDIO:
            repo = layout.repo("sagasmith-dnd")
            result["sagasmith_dnd"] = _stdio_server(
                repo,
                "sagasmith_dnd_mcp.server",
                dnd_environment(
                    layout,
                    transport=domain_transport,
                    auth_context_secret=auth_context_secret,
                ),
                auth_context_secret,
                "dnd5e",
            )
        else:
            result["sagasmith_dnd"] = _http_server(
                "http://127.0.0.1:8767/mcp", auth_context_secret, "dnd5e"
            )
    if InstallMode.COC in selected:
        domain_transport = transport_for_mode(transport, InstallMode.COC)
        if domain_transport == McpTransport.STDIO:
            repo = layout.repo("sagasmith-coc")
            result["sagasmith_coc"] = _stdio_server(
                repo,
                "sagasmith_coc_mcp.server",
                coc_environment(
                    layout,
                    transport=domain_transport,
                    auth_context_secret=auth_context_secret,
                ),
                auth_context_secret,
                "coc7e",
            )
        else:
            result["sagasmith_coc"] = _http_server(
                "http://127.0.0.1:8769/mcp", auth_context_secret, "coc7e"
            )
    if InstallMode.NARRATIVE in selected:
        domain_transport = transport_for_mode(transport, InstallMode.NARRATIVE)
        if domain_transport == McpTransport.STDIO:
            repo = layout.repo("sagasmith-narrative")
            result["sagasmith_narrative"] = _stdio_server(
                repo,
                "sagasmith_narrative_mcp.server",
                narrative_environment(
                    layout,
                    transport=domain_transport,
                    auth_context_secret=auth_context_secret,
                ),
                auth_context_secret,
                "narrative",
            )
        else:
            result["sagasmith_narrative"] = _http_server(
                "http://127.0.0.1:8770/mcp", auth_context_secret, "narrative"
            )
    return result


def _common_server(auth_context_secret: str, system_id: str) -> dict[str, Any]:
    return {
        "toolTimeout": 900,
        "enabledTools": ["*"],
        "exposeResourcesAndPrompts": True,
        "injectPrincipal": True,
        "sessionScoped": True,
        "systemIds": [system_id],
        "protocolMode": "auto",
        "authorizationAudience": "local",
        **({"authContextSecret": auth_context_secret} if auth_context_secret else {}),
        **({"delegationSecret": auth_context_secret} if auth_context_secret else {}),
    }


def _http_server(url: str, auth_context_secret: str, system_id: str) -> dict[str, Any]:
    return {
        "type": "streamableHttp",
        "url": url,
        "headers": {},
        **_common_server(auth_context_secret, system_id),
    }


def _stdio_server(
    repo: Path,
    module: str,
    environment: dict[str, str],
    auth_context_secret: str,
    system_id: str,
) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": str(_python_executable(repo)),
        "args": ["-m", module],
        "cwd": str(repo),
        "env": environment,
        **_common_server(auth_context_secret, system_id),
    }


def desired_skill_roots(layout: StackLayout, modes: tuple[InstallMode, ...]) -> list[str]:
    selected = set(modes)
    result: list[str] = []
    if InstallMode.DND in selected:
        root = layout.repo("sagasmith-dnd") / "skills"
        result.extend(
            [str(root / "full" / "skills"), str(root / "dnd-module-generator")]
        )
    if InstallMode.COC in selected:
        root = layout.repo("sagasmith-coc") / "skills"
        result.extend(
            [str(root / "full" / "skills"), str(root / "coc-module-generator")]
        )
    if InstallMode.NARRATIVE in selected:
        result.append(str(layout.repo("sagasmith-narrative") / "skills"))
    return result


def _is_owned_skill_path(value: str, layout: StackLayout) -> bool:
    try:
        path = Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    roots = (
        layout.repo("sagasmith-dnd") / "skills",
        layout.repo("sagasmith-coc") / "skills",
        layout.repo("sagasmith-narrative") / "skills",
    )
    return any(path == root or root in path.parents for root in roots)


def reconcile_agent_config(
    config: dict[str, Any],
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
    *,
    transport: McpTransport = McpTransport.MIXED,
) -> dict[str, Any]:
    result = dict(config)
    tools = dict(result.get("tools") or {})
    servers = dict(tools.get("mcpServers") or {})
    auth_context_secret = next(
        (
            str(servers[name].get("authContextSecret") or "").strip()
            for name in sorted(OWNED_SERVERS)
            if isinstance(servers.get(name), dict)
            and len(str(servers[name].get("authContextSecret") or "").encode("utf-8")) >= 32
        ),
        "",
    ) or secrets.token_urlsafe(32)
    for name in OWNED_SERVERS:
        servers.pop(name, None)
    servers.update(
        desired_servers(
            layout,
            modes,
            transport=transport,
            auth_context_secret=auth_context_secret,
        )
    )
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


def configure_agent(
    layout: StackLayout,
    modes: tuple[InstallMode, ...],
    *,
    transport: McpTransport = McpTransport.MIXED,
) -> bool:
    if layout.config_path.exists():
        try:
            current = json.loads(layout.config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read Agent config {layout.config_path}: {exc}") from exc
        if not isinstance(current, dict):
            raise ValueError("Agent config root must be an object")
    else:
        current = {}
    desired = reconcile_agent_config(current, layout, modes, transport=transport)
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


def dnd_environment(
    layout: StackLayout,
    *,
    transport: McpTransport = McpTransport.STREAMABLE_HTTP,
    auth_context_secret: str = "",
) -> dict[str, str]:
    embedding_cache = os.environ.get(
        "SAGASMITH_DND_EMBEDDING_CACHE_DIR",
        str(layout.data_dir / "dnd" / "embedding-cache"),
    )
    values = {
        "SAGASMITH_DND_MCP_HOME": str(layout.data_dir / "dnd"),
        "SAGASMITH_DND_SKILLS_DIR": str(layout.repo("sagasmith-dnd") / "skills"),
        "SAGASMITH_MODULEGEN_SKILLS_DIR": str(
            layout.repo("sagasmith-dnd") / "skills" / "dnd-module-generator"
        ),
        "SAGASMITH_DND_MCP_TRANSPORT": transport.value,
        "SAGASMITH_DND_MCP_HTTP_HOST": "127.0.0.1",
        "SAGASMITH_DND_MCP_HTTP_PORT": "8767",
        "SAGASMITH_DND_MCP_URL": "http://127.0.0.1:8767/mcp",
        # sagasmith-core reads the domain prefix selected by the D&D runtime.
        "DND5E_EMBEDDING_CACHE_DIR": embedding_cache,
        "SAGASMITH_DND_GATEWAY_HOST": "127.0.0.1",
        "SAGASMITH_DND_GATEWAY_PORT": "8766",
        "SAGASMITH_AGENT_WEBUI_URL": agent_webui_url(layout),
    }
    ui_dist = layout.repo("sagasmith-dnd") / "apps" / "ui" / "dist"
    if (ui_dist / "index.html").is_file():
        values["SAGASMITH_DND_UI_DIST"] = str(ui_dist)
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
    if secret := auth_context_secret or _configured_auth_context_secret(layout):
        values["SAGASMITH_AUTH_CONTEXT_SECRET"] = secret
    return values


def coc_environment(
    layout: StackLayout,
    *,
    transport: McpTransport = McpTransport.STREAMABLE_HTTP,
    auth_context_secret: str = "",
) -> dict[str, str]:
    embedding_cache = os.environ.get(
        "SAGASMITH_COC_EMBEDDING_CACHE_DIR",
        str(layout.data_dir / "coc" / "embedding-cache"),
    )
    values = {
        "SAGASMITH_COC_MCP_HOME": str(layout.data_dir / "coc"),
        "SAGASMITH_COC_SKILLS_DIR": str(layout.repo("sagasmith-coc") / "skills"),
        "SAGASMITH_MODULEGEN_SKILLS_DIR": str(
            layout.repo("sagasmith-coc") / "skills" / "coc-module-generator"
        ),
        "SAGASMITH_COC_MCP_TRANSPORT": transport.value,
        "SAGASMITH_COC_MCP_HTTP_HOST": "127.0.0.1",
        "SAGASMITH_COC_MCP_HTTP_PORT": "8769",
        "SAGASMITH_COC_MCP_URL": "http://127.0.0.1:8769/mcp",
        # sagasmith-core reads the domain prefix selected by the CoC runtime.
        "COC7_EMBEDDING_CACHE_DIR": embedding_cache,
        "SAGASMITH_COC_GATEWAY_HOST": "127.0.0.1",
        "SAGASMITH_COC_GATEWAY_PORT": "8768",
    }
    ui_dist = layout.repo("sagasmith-coc") / "apps" / "ui" / "dist"
    if (ui_dist / "index.html").is_file():
        values["SAGASMITH_COC_UI_DIST"] = str(ui_dist)
    if configured := os.environ.get("SAGASMITH_COC_MCP_MODULE_IMPORT_ROOTS"):
        values["SAGASMITH_COC_MCP_MODULE_IMPORT_ROOTS"] = configured
    if secret := auth_context_secret or _configured_auth_context_secret(layout):
        values["SAGASMITH_AUTH_CONTEXT_SECRET"] = secret
    return values


def narrative_environment(
    layout: StackLayout,
    *,
    transport: McpTransport = McpTransport.STDIO,
    auth_context_secret: str = "",
) -> dict[str, str]:
    values = {
        "SAGASMITH_NARRATIVE_MCP_HOME": str(layout.data_dir / "narrative"),
        "SAGASMITH_NARRATIVE_SKILLS_DIR": str(
            layout.repo("sagasmith-narrative") / "skills"
        ),
        "SAGASMITH_NARRATIVE_MCP_TRANSPORT": transport.value,
        "SAGASMITH_NARRATIVE_MCP_HTTP_HOST": "127.0.0.1",
        "SAGASMITH_NARRATIVE_MCP_HTTP_PORT": "8770",
        "SAGASMITH_NARRATIVE_MCP_URL": "http://127.0.0.1:8770/mcp",
    }
    secret = auth_context_secret or _configured_auth_context_secret(layout)
    if secret:
        values["SAGASMITH_AUTH_CONTEXT_SECRET"] = secret
    return values


def _configured_auth_context_secret(layout: StackLayout) -> str:
    if not layout.config_path.is_file():
        return ""
    try:
        config = json.loads(layout.config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    servers = config.get("tools", {}).get("mcpServers", {})
    if not isinstance(servers, dict):
        return ""
    for name in sorted(OWNED_SERVERS):
        server = servers.get(name)
        if isinstance(server, dict):
            value = str(server.get("authContextSecret") or "").strip()
            if len(value.encode("utf-8")) >= 32:
                return value
    return ""
