from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
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
from nanobot.sagasmith_local.model import InstallMode, McpTransport, load_release_revisions

SECRET = "cross-domain-host-conformance-secret-at-least-32-bytes"
AGENT_ROOT = Path(__file__).parents[2]
DEFAULT_WORKSPACE = AGENT_ROOT.parent
REQUIRED_ENV = "SAGASMITH_REAL_DOMAINS_REQUIRED"
WORKSPACE_ENV = "SAGASMITH_REAL_DOMAIN_WORKSPACE"
STATE_ROOT_ENV = "SAGASMITH_REAL_DOMAIN_STATE_ROOT"
LANE_ENV = "SAGASMITH_REAL_DOMAIN_LANE"
PROTOCOL_MODE_ENV = "SAGASMITH_REAL_DOMAIN_PROTOCOL_MODE"
TRANSPORTS = (McpTransport.STDIO, McpTransport.STREAMABLE_HTTP)
# A cold Windows D&D run has exceeded 60 seconds after completing its MCP calls.
# Keep teardown bounded without turning that valid first-run cost into a failure.
CASE_TIMEOUT_SECONDS = 120


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


def _workspace() -> Path:
    configured = os.environ.get(WORKSPACE_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_WORKSPACE


def _required() -> bool:
    return os.environ.get(REQUIRED_ENV, "").strip().casefold() in {"1", "true", "yes"}


def _protocol_mode() -> str:
    mode = os.environ.get(PROTOCOL_MODE_ENV, "legacy").strip() or "legacy"
    if mode not in {"legacy", "2026-07-28"}:
        pytest.fail(f"unsupported real-domain protocol mode: {mode}")
    return mode


def _downstream_python(workspace: Path, domain: Domain) -> Path:
    candidate = workspace / domain.repo / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    if candidate.is_file():
        return candidate
    message = f"{domain.name} development environment is unavailable: {candidate}"
    if _required():
        pytest.fail(message)
    pytest.skip(message)


def _checkout_revision(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def _verify_required_ci_lane() -> None:
    if not _required():
        return
    workspace = _workspace()
    missing = [domain.name for domain in DOMAINS if not (workspace / domain.repo).is_dir()]
    if missing:
        pytest.fail("required real-domain repositories are missing: " + ", ".join(missing))
    for domain in DOMAINS:
        _downstream_python(workspace, domain)

    state_root = os.environ.get(STATE_ROOT_ENV, "").strip()
    lane = os.environ.get(LANE_ENV, "").strip()
    if not state_root or lane not in {"release-lock", "latest-main"}:
        pytest.fail("required real-domain lane metadata is incomplete")
    if _protocol_mode() != "2026-07-28":
        pytest.fail("required real-domain lanes must exercise MCP 2026-07-28")
    state_path = Path(state_root).expanduser().resolve() / "stack.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        pytest.fail(f"cannot read required real-domain state {state_path}: {exc}")
    assert state["source"] == "release"
    assert state["mcp_transport"] == McpTransport.STREAMABLE_HTTP.value
    assert Path(state["workspace_root"]).resolve() == workspace
    revisions = state["component_revisions"]
    expected_repositories = {
        "SagaSmith-agent",
        "sagasmith-core",
        *(domain.repo for domain in DOMAINS),
    }
    assert expected_repositories <= set(revisions)
    assert all(
        len(revisions[repository]) == 40
        for repository in expected_repositories
    )
    assert _checkout_revision(AGENT_ROOT) == revisions["SagaSmith-agent"]
    assert all(
        _checkout_revision(workspace / repository) == revisions[repository]
        for repository in expected_repositories - {"SagaSmith-agent"}
    )
    if lane == "release-lock":
        assert state["release_ref"] == "manifest"
        expected = load_release_revisions(
            AGENT_ROOT / "sagasmith-stack-lock.json", tuple(InstallMode)
        )
        assert {repository: revisions[repository] for repository in expected} == expected
    else:
        assert state["release_ref"] == "main"


def _contexts() -> list[TrustedHostContext]:
    contexts = [
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
    return [
        replace(
            context,
            resource_owner_principal="campaign:owner:table-1",
            acting_host_principal="workload:sagasmith-agent",
        )
        for context in contexts
    ]


@pytest.mark.parametrize("transport", TRANSPORTS, ids=lambda item: item.value)
@pytest.mark.parametrize("domain", DOMAINS, ids=lambda item: item.name)
@pytest.mark.parametrize("context", _contexts(), ids=lambda item: item.host)
def test_real_domain_accepts_each_host_only_through_signed_bridge(
    transport: McpTransport,
    domain: Domain,
    context: TrustedHostContext,
    tmp_path: Path,
) -> None:
    asyncio.run(
        asyncio.wait_for(
            _exercise(transport, domain, context, tmp_path),
            timeout=CASE_TIMEOUT_SECONDS,
        )
    )


def _http_bridge_target(domain: Domain) -> tuple[dict[str, object], str]:
    configured = os.environ.get(STATE_ROOT_ENV, "").strip()
    if not configured:
        message = f"{STATE_ROOT_ENV} is required for streamable HTTP conformance"
        if _required():
            pytest.fail(message)
        pytest.skip(message)
    state_path = Path(configured).expanduser().resolve() / "stack.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        config_path = Path(state["config_path"]).expanduser().resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        server = config["tools"]["mcpServers"][f"sagasmith_{domain.name}"]
    except (KeyError, OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        pytest.fail(f"cannot read installed streamable HTTP target for {domain.name}: {exc}")
    assert isinstance(server, dict)
    assert server["type"] == "streamableHttp"
    assert server["targetService"] == f"sagasmith-{domain.name}-mcp"
    assert server["authorizationAudience"] == server["targetService"]
    secret = str(server.get("authContextSecret") or "")
    assert len(secret.encode("utf-8")) >= 32
    return {
        "type": server["type"],
        "url": server["url"],
        "headers": server.get("headers") or {},
        "protocolMode": _protocol_mode(),
        "targetService": server["targetService"],
        "authorizationAudience": server["authorizationAudience"],
    }, secret


def _stdio_bridge_target(
    workspace: Path, domain: Domain, tmp_path: Path
) -> tuple[dict[str, object], str]:
    repo = workspace / domain.repo
    downstream_python = _downstream_python(workspace, domain)
    return {
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
                    str(workspace / "sagasmith-core" / "src"),
                ]
            ),
        },
        "protocolMode": _protocol_mode(),
        "targetService": f"sagasmith-{domain.name}-mcp",
        "authorizationAudience": f"sagasmith-{domain.name}-mcp",
    }, SECRET


async def _exercise(
    transport: McpTransport,
    domain: Domain,
    context: TrustedHostContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    if transport == McpTransport.STREAMABLE_HTTP:
        server_config, secret = _http_bridge_target(domain)
    else:
        server_config, secret = _stdio_bridge_target(workspace, domain, tmp_path)

    config_path = tmp_path / "bridge.json"
    context_path = tmp_path / "context.json"
    secret_path = tmp_path / "secret"
    config_path.write_text(json.dumps(server_config), encoding="utf-8")
    context_path.write_text(json.dumps(context.to_dict()), encoding="utf-8")
    secret_path.write_text(secret, encoding="utf-8")
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
            assert not result.is_error, result.content
            receipt = result.content[0].meta["sagasmith_auth_context_receipt"]
            principal_field = (
                "requester_principal"
                if _protocol_mode() == "2026-07-28"
                else "actor_principal"
            )
            assert receipt[principal_field] == context.actor_principal
            assert receipt["conversation_principal"] == context.conversation_principal
            if _protocol_mode() == "2026-07-28":
                assert receipt["requester_principal"] == context.requester_principal
                assert (
                    receipt["resource_owner_principal"]
                    == context.resource_owner_principal
                )
                assert receipt["acting_host_principal"] == context.acting_host_principal
                assert len(
                    {
                        receipt["requester_principal"],
                        receipt["resource_owner_principal"],
                        receipt["acting_host_principal"],
                    }
                ) == 3
                assert receipt["target_service"] == f"sagasmith-{domain.name}-mcp"
                assert receipt["allowed_operations"] == ["exposure"]
            else:
                assert receipt["authorization_epoch"] == 0
            await session.list_resources()
            await session.list_prompts()
