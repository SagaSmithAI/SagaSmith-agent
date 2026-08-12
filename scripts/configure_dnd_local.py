"""Inspect or atomically configure the repo-local D&D MCP HTTP connection."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_MCP_URL = "http://127.0.0.1:8767/mcp"
LOOPBACK_CIDR = "127.0.0.1/32"


def desired_server(url: str) -> dict[str, Any]:
    return {
        "type": "streamableHttp",
        "url": url,
        "headers": {},
        "toolTimeout": 900,
        "enabledTools": ["*"],
        "exposeResourcesAndPrompts": True,
        "injectPrincipal": True,
    }


def updated_config(config: dict[str, Any], url: str) -> dict[str, Any]:
    result = dict(config)
    tools = dict(result.get("tools") or {})
    servers = dict(tools.get("mcpServers") or {})
    servers["sagasmith_dnd"] = desired_server(url)
    servers.pop("sagasmith_coc", None)
    tools["mcpServers"] = servers
    whitelist = [str(item) for item in tools.get("ssrfWhitelist") or []]
    if LOOPBACK_CIDR not in whitelist:
        whitelist.append(LOOPBACK_CIDR)
    tools["ssrfWhitelist"] = whitelist
    result["tools"] = tools
    return result


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read UTF-8 JSON config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("config root must be an object")
    return value


def atomic_write(path: Path, value: dict[str, Any]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    path = Path(args.config).expanduser().resolve()
    current = load_config(path)
    desired = updated_config(current, DEFAULT_MCP_URL)
    if current == desired:
        print("SagaSmith local D&D MCP configuration: OK")
        return 0
    if not args.apply:
        print("SagaSmith local D&D MCP configuration needs an update.")
        print(f"Run: {Path(__file__).name} --config {path} --apply")
        return 1

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    atomic_write(path, desired)
    print(f"Updated: {path}")
    print(f"Backup:  {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
