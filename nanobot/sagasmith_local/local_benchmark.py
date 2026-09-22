"""Measure the real Host/stdio local path using disposable data and no LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

from loguru import logger

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig
from nanobot.session.manager import SessionManager


async def measure(
    executable: Path, root: Path, iterations: int, *, official_library: Path | None = None,
    dnd_skills: Path | None = None,
) -> dict:
    registry = ToolRegistry()
    store = SessionManager(root / "host")
    config = MCPServerConfig(
        type="stdio", command=str(executable), args=["-m", "sagasmith_dnd_mcp.server"],
        local_authority=True, bound_principal_id="system:local", protocol_mode="2026-07-28",
        expose_resources_and_prompts=False, env={
            "SAGASMITH_DND_MCP_HOME": str(root / "dnd"),
            "SAGASMITH_DND_MCP_AUTO_SEED": "0", "SAGASMITH_DND_LOCAL_AUTHORITY": "1",
            "SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID": "system:local",
            "SAGASMITH_AUTH_CONTEXT_SECRET": "",
            "SAGASMITH_DND_SKILLS_DIR": str(dnd_skills.resolve() if dnd_skills else root / "skills"),
            "SAGASMITH_MODULEGEN_SKILLS_DIR": str(root / "modulegen"),
            "SAGASMITH_DND_OFFICIAL_CONTENT_LIBRARY": (
                str(official_library.resolve()) if official_library else ""
            ),
        },
    )
    samples = []

    async def call(name, arguments):
        started = perf_counter()
        result = await registry.execute("mcp_dnd_" + name, arguments)
        if getattr(result, "is_error", False):
            raise RuntimeError(str(result))
        data = result.structured_content
        samples.append({"operation": name, "host_ms": (perf_counter() - started) * 1000,
                        "runtime": data.get("local_execution", {})})
        return data.get("result", data)

    started = perf_counter()
    async with AsyncExitStack() as stack:
        connections = await connect_mcp_servers({"dnd": config}, registry, session_store=store)
        for connection in connections.values():
            stack.push_async_callback(connection.aclose)
        if "dnd" not in connections:
            raise RuntimeError("DND stdio connection did not start")
        cold_ms = (perf_counter() - started) * 1000
        with request_context(RequestContext(channel="cli", chat_id="benchmark")):
            campaign = await call("campaign_create", {"name": f"Disposable local benchmark {uuid4().hex}"})
            source = await call("character_create_from", {"mode": "direct", "payload": {
                "campaign_id": campaign["id"], "name": "Source",
            }})
            target = await call("character_create_from", {"mode": "direct", "payload": {
                "campaign_id": campaign["id"], "name": "Target",
            }})
            await call("inventory_change", {"owner": "character", "owner_id": source["id"],
                "action": "add", "payload": {"item": {
                    "id": "rope", "name": "Rope", "kind": "equipment",
                    "quantity": iterations + 1,
                }}})
            for _ in range(iterations):
                receipt = await call("inventory_transfer", {
                    "mode": "character_to_character", "payload": {
                        "source_character_id": source["id"],
                        "target_character_id": target["id"], "item_id": "rope", "quantity": 1,
                    },
                })
            assert sum(item["quantity"] for item in
                       receipt["target"]["sheet"]["inventory"]["items"]) == iterations
            assert receipt["source"]["sheet"]["inventory"]["items"][0]["quantity"] == 1
            for _ in range(iterations):
                await call("campaign_query", {"view": "binding"})
            definitions = registry.get_definitions()
            visible_count = len(definitions)
            schema_bytes = len(json.dumps(definitions, ensure_ascii=False).encode("utf-8"))
    return {
        "schema": "sagasmith.local-authority-benchmark/v1",
        "llm_used": False, "authoritative_user_data_used": False,
        "official_content_library_configured": official_library is not None,
        "bundled_skills_configured": dnd_skills is not None,
        "cold_connection_ms": cold_ms, "iterations": iterations,
        "visible_tools": visible_count, "visible_schema_utf8_bytes": schema_bytes,
        "warm_transfer_median_ms": statistics.median(
            row["host_ms"] for row in samples if row["operation"] == "inventory_transfer"
        ),
        "warm_binding_median_ms": statistics.median(
            row["host_ms"] for row in samples if row["operation"] == "campaign_query"
        ),
        "samples": samples,
    }


def main():
    logger.remove()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dnd-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument(
        "--official-library", type=Path,
        help="Verified local content library to mount into the disposable database",
    )
    parser.add_argument("--dnd-skills", type=Path, help="DND skills containing bundled SRD dependencies")
    parser.add_argument(
        "--measure-restart", action="store_true",
        help="Reconnect to the same disposable database to measure installed-content startup",
    )
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be at least 1")
    if args.official_library and not args.dnd_skills:
        parser.error("--official-library requires --dnd-skills for bundled SRD dependencies")
    with TemporaryDirectory(prefix="sagasmith-authority-benchmark-") as directory:
        result = asyncio.run(measure(
            args.dnd_python.resolve(), Path(directory), args.iterations,
            official_library=args.official_library,
            dnd_skills=args.dnd_skills,
        ))
        if args.measure_restart:
            result["restart"] = asyncio.run(measure(
                args.dnd_python.resolve(), Path(directory), args.iterations,
                official_library=args.official_library, dnd_skills=args.dnd_skills,
            ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"samples", "restart"}}))
    if "restart" in result:
        print(json.dumps({"restart": {key: value for key, value in result["restart"].items()
                                      if key != "samples"}}))


if __name__ == "__main__":
    main()
