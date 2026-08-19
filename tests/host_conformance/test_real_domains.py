from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nanobot.sagasmith_hosts.contract import (
    TrustedHostContext,
    adapt_claude_code,
    adapt_codex,
    adapt_hermes,
    adapt_nanobot,
    adapt_openclaw,
    adapt_sagasmith_agent,
    adapt_service_worker,
)

SECRET = "cross-domain-host-conformance-secret-at-least-32-bytes"
WORKSPACE = Path(__file__).parents[3]


@dataclass(frozen=True)
class Domain:
    name: str
    repo: str
    module: str
    home_variable: str
    mcp_source: str
    domain_source: str


DOMAINS = (
    Domain(
        "dnd",
        "sagasmith-dnd",
        "sagasmith_dnd_mcp.server",
        "SAGASMITH_DND_MCP_HOME",
        "packages/mcp/src",
        "packages/domain/src",
    ),
    Domain(
        "coc",
        "sagasmith-coc",
        "sagasmith_coc_mcp.server",
        "SAGASMITH_COC_MCP_HOME",
        "packages/mcp/src",
        "packages/domain/src",
    ),
    Domain(
        "narrative",
        "sagasmith-narrative",
        "sagasmith_narrative_mcp.server",
        "SAGASMITH_NARRATIVE_MCP_HOME",
        "packages/mcp/src",
        "packages/domain/src",
    ),
)


def _contexts() -> list[TrustedHostContext]:
    return [
        adapt_sagasmith_agent(
            {
                "channel": "discord",
                "sender_id": "alice",
                "chat_id": "table-1",
                "actor_principal": "user:alice",
                "conversation_principal": "group:table-1",
                "session_key": "discord:table-1",
            }
        ),
        adapt_nanobot(
            {
                "channel": "discord",
                "sender_id": "alice",
                "chat_id": "table-1",
                "metadata": {"chat_type": "group"},
            }
        ),
        adapt_openclaw(
            {
                "messageChannel": "discord",
                "requesterSenderId": "alice",
                "conversationId": "table-1",
                "chatType": "group",
                "sessionId": "discord:table-1",
            }
        ),
        adapt_hermes(
            {
                "platform": "discord",
                "user_id": "alice",
                "chat_id": "table-1",
                "chat_type": "group",
                "session_id": "discord:table-1",
            }
        ),
        adapt_codex(profile_id="alice", project_id="table-1"),
        adapt_claude_code(profile_id="alice", project_id="table-1"),
        adapt_service_worker(
            principal_id="user:alice",
            conversation_id="table-1",
        ),
    ]


@pytest.mark.parametrize("domain", DOMAINS, ids=lambda item: item.name)
@pytest.mark.parametrize("context", _contexts(), ids=lambda item: item.host)
def test_real_domain_accepts_each_host_only_through_signed_bridge(
    domain: Domain, context: TrustedHostContext, tmp_path: Path
) -> None:
    asyncio.run(_exercise(domain, context, tmp_path))


async def _exercise(domain: Domain, context: TrustedHostContext, tmp_path: Path) -> None:
    repo = WORKSPACE / domain.repo
    downstream_python = repo / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    if not downstream_python.is_file():
        pytest.skip(f"{domain.name} development environment is unavailable")

    config_path = tmp_path / "bridge.json"
    context_path = tmp_path / "context.json"
    secret_path = tmp_path / "secret"
    config_path.write_text(
        json.dumps(
            {
                "type": "stdio",
                "command": str(downstream_python),
                "args": ["-m", domain.module],
                "cwd": str(repo / "packages" / "mcp"),
                "env": {
                    domain.home_variable: str(tmp_path / "domain-home"),
                    "SAGASMITH_AUTH_CONTEXT_SECRET": SECRET,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(repo / domain.mcp_source),
                            str(repo / domain.domain_source),
                            str(WORKSPACE / "sagasmith-core" / "src"),
                        ]
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    context_path.write_text(json.dumps(context.to_dict()), encoding="utf-8")
    secret_path.write_text(SECRET, encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "nanobot.sagasmith_hosts.bridge",
            "--config",
            str(config_path),
            "--context",
            str(context_path),
            "--secret-file",
            str(secret_path),
        ],
        cwd=Path(__file__).parents[2],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert "exposure" in {tool.name for tool in (await session.list_tools()).tools}
            result = await session.call_tool(
                "exposure",
                {"action": "open", "principal_id": "model:forged-owner"},
            )
            assert not result.isError, result.content
            receipt = result.content[0].meta["sagasmith_auth_context_receipt"]
            assert receipt["actor_principal"] == context.actor_principal
            assert receipt["conversation_principal"] == context.conversation_principal
            assert receipt["authorization_epoch"] == 0
            await session.list_resources()
            await session.list_prompts()
