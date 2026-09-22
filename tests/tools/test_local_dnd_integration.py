"""Optional cross-repository acceptance against a real stdio authority."""

import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig
from nanobot.session.manager import SessionManager


@pytest.mark.asyncio
async def test_host_local_dnd_stdio_end_to_end(tmp_path):
    sibling = Path(__file__).resolve().parents[2].parent / "Sagasmith-dnd"
    executable = Path(os.environ.get("SAGASMITH_DND_TEST_PYTHON", str(
        sibling / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )))
    if not executable.is_file():
        pytest.skip("set SAGASMITH_DND_TEST_PYTHON to the DND integration environment")
    registry = ToolRegistry()
    store = SessionManager(tmp_path / "agent")
    cfg = MCPServerConfig(type="stdio", command=str(executable),
        args=["-m", "sagasmith_dnd_mcp.server"], local_authority=True,
        bound_principal_id="system:local", protocol_mode="2026-07-28",
        expose_resources_and_prompts=False, env={
            "SAGASMITH_DND_MCP_HOME": str(tmp_path / "dnd"),
            "SAGASMITH_DND_MCP_AUTO_SEED": "0", "SAGASMITH_DND_LOCAL_AUTHORITY": "1",
            "SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID": "system:local",
            "SAGASMITH_AUTH_CONTEXT_SECRET": "",
            "SAGASMITH_DND_SKILLS_DIR": str(tmp_path / "skills"),
            "SAGASMITH_MODULEGEN_SKILLS_DIR": str(tmp_path / "modulegen"),
        })
    async with AsyncExitStack() as stack:
        connections = await connect_mcp_servers({"dnd": cfg}, registry, session_store=store)
        for connection in connections.values():
            stack.push_async_callback(connection.aclose)
        with request_context(RequestContext(channel="cli", chat_id="one", session_key="cli:one")):
            created = await registry.execute("mcp_dnd_campaign_create", {"name": "Host local"})
            assert not created.is_error, str(created)
            assert created.context_barrier
            campaign = created.structured_content
            assert "idempotency_key" not in registry.get("mcp_dnd_campaign_create").parameters["properties"]
            await registry.execute("mcp_dnd_local_capabilities", {"query": "snapshot_create"})
            assert "mcp_dnd_snapshot_create" in registry.definition_names()
            saved = await registry.execute("mcp_dnd_snapshot_create", {"label": "Host saved"})
            assert not saved.is_error, str(saved)
            assert saved.structured_content["local_context"]["campaign_id"] == campaign["id"]
            # Supplemental capability selection does not alter server tools/list.
            assert "mcp_dnd_exposure" not in registry.definition_names()
            binding = await registry.execute("mcp_dnd_campaign_query", {"view": "binding"})
            assert not binding.is_error, str(binding)
            blocks = registry.get_runtime_context_providers()
            assert blocks
            context = await blocks[-1](None)
            assert campaign["id"] in json.dumps(context.content)
