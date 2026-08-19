from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.domain_context import (
    DOMAIN_CONTEXT_BINDING_KEY,
    DomainContextBinding,
    admit_current_user_to_context_epoch,
    bind_session_context,
    history_attributes,
    principal_fingerprint,
)
from nanobot.agent.loop import AgentLoop
from nanobot.agent.memory import Consolidator, MemoryStore
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import RequestContext
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.session.manager import Session, SessionManager


def _binding(*, branch_id: str = "branch-a") -> DomainContextBinding:
    return DomainContextBinding.from_mapping({
        "domain": "sagasmith-dnd",
        "campaign_id": "campaign-1",
        "principal_fingerprint": principal_fingerprint("discord:user-1"),
        "authorization_fingerprint": "b" * 64,
        "role": "player",
        "audience": "principal",
        "branch_id": branch_id,
    })


def test_binding_change_creates_hard_replay_barrier() -> None:
    session = Session(key="discord:table")
    session.add_message("user", "old branch secret")
    session.metadata["_last_summary"] = {"text": "old", "last_active": "2026-01-01"}

    assert bind_session_context(session, _binding()) is True
    assert session.last_consolidated == 1
    assert "_last_summary" not in session.metadata
    assert session.get_history() == []

    session.add_message("user", "current branch")
    assert bind_session_context(session, _binding()) is False
    assert session.get_history() == [{"role": "user", "content": "current branch"}]

    assert bind_session_context(session, _binding(branch_id="branch-b")) is True
    assert session.get_history() == []


def test_current_pending_user_can_be_admitted_to_the_new_epoch() -> None:
    session = Session(key="discord:table")
    session.add_message("user", "old campaign request")
    assert bind_session_context(session, _binding()) is True

    session.add_message("user", "resume campaign one")
    assert bind_session_context(session, _binding(branch_id="branch-b")) is True
    assert session.get_history() == []

    assert admit_current_user_to_context_epoch(session) is True
    assert session.get_history() == [
        {"role": "user", "content": "resume campaign one"}
    ]
    expected_attributes = {
        "role": "user",
        "content": "resume campaign one",
        "classification": "campaign_private",
        "dream_eligible": False,
        "prompt_eligible": False,
        "context_namespace": (
            "sagasmith-dnd:campaign-1:"
            f"{principal_fingerprint('discord:user-1')}"
        ),
        "context_epoch": _binding(branch_id="branch-b").context_epoch,
    }
    assert {
        key: session.messages[-1].get(key) for key in expected_attributes
    } == expected_attributes


def test_binding_rejects_untrusted_epoch_and_principal_shapes() -> None:
    valid = _binding().to_dict()
    with pytest.raises(ValueError, match="does not match"):
        DomainContextBinding.from_mapping({**valid, "context_epoch": "0" * 64})
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        DomainContextBinding.from_mapping({**valid, "principal_fingerprint": "user-1"})


def test_context_epoch_uses_utf8_canonical_json_compatible_with_mcp() -> None:
    binding = DomainContextBinding(
        domain="sagasmith-dnd",
        campaign_id="战役一",
        principal_fingerprint="a" * 64,
        authorization_fingerprint="b" * 64,
        role="dm",
        audience="dm",
        branch_id="分支甲",
    )

    assert binding.derived_epoch() == (
        "963ddb25065a5676a0c5ea6d55de339ad4dacc4a0d32b984464b68fe72f1ba56"
    )


def test_authorization_fingerprint_change_creates_hard_replay_barrier() -> None:
    session = Session(key="discord:authority")
    first = _binding()
    assert bind_session_context(session, first) is True
    session.messages.append({"role": "assistant", "content": "private actor detail"})

    changed = DomainContextBinding.from_mapping(
        {**first.to_dict(), "authorization_fingerprint": "c" * 64, "context_epoch": ""}
    )
    assert bind_session_context(session, changed) is True
    assert session.last_consolidated == len(session.messages)


def test_domain_prompt_excludes_workspace_user_memory_and_history(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("agent rules", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text("stable identity", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("safe identity", encoding="utf-8")
    (tmp_path / "USER.md").write_text("other user's private profile", encoding="utf-8")
    store = MemoryStore(tmp_path)
    store.write_memory("dm-only global memory")
    store.append_history("other session secret", session_key="discord:other")
    metadata = {DOMAIN_CONTEXT_BINDING_KEY: _binding().to_dict()}

    prompt = ContextBuilder(tmp_path).build_system_prompt(
        session_key="discord:table",
        unified_session=True,
        session_metadata=metadata,
    )

    assert "agent rules" in prompt
    assert "stable identity" in prompt
    assert "safe identity" in prompt
    assert "other user's private profile" not in prompt
    assert "dm-only global memory" not in prompt
    assert "other session secret" not in prompt


def test_private_domain_history_is_redacted_from_dream_and_prompt(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    attributes = history_attributes({DOMAIN_CONTEXT_BINDING_KEY: _binding().to_dict()})
    store.append_history(
        "the duke is secretly a dragon",
        session_key="discord:dm",
        attributes=attributes,
    )

    persisted = json.loads(store.history_file.read_text(encoding="utf-8"))
    assert persisted["classification"] == "campaign_private"
    assert persisted["dream_eligible"] is False
    assert persisted["prompt_eligible"] is False
    assert store.read_recent_history_for_prompt(
        0,
        session_key="discord:dm",
        unified_session=True,
    ) == []

    built = store.build_dream_prompt()
    assert built is not None
    prompt, _ = built
    assert "the duke is secretly a dragon" not in prompt
    assert "private domain history omitted" in prompt


def test_consolidator_reads_domain_policy_from_session_metadata_record(tmp_path) -> None:
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("discord:table")
    bind_session_context(session, _binding())
    sessions.save(session)
    consolidator = Consolidator(
        MemoryStore(tmp_path),
        sessions,
        build_messages=lambda **_kwargs: [],
        get_tool_definitions=lambda: [],
    )

    attributes = consolidator._history_attributes("discord:table")

    assert attributes["classification"] == "campaign_private"
    assert attributes["dream_eligible"] is False
    assert attributes["prompt_eligible"] is False


@pytest.mark.asyncio
async def test_pre_turn_sync_advances_branch_barrier_before_history_replay() -> None:
    session = Session(key="discord:table")
    bind_session_context(session, _binding(branch_id="branch-a"))
    session.add_message("assistant", "branch-a secret")
    seen: dict[str, object] = {}

    class SyncTool(Tool):
        _context_sync = True
        _domain_context = "sagasmith-dnd"

        @property
        def name(self) -> str:
            return "mcp_sagasmith_dnd_campaign_query"

        @property
        def description(self) -> str:
            return "sync"

        @property
        def parameters(self) -> dict[str, object]:
            return {
                "type": "object",
                "properties": {
                    "view": {"type": "string"},
                    "payload": {"type": "object"},
                },
            }

        async def execute(self, **kwargs: object) -> ToolResult:
            seen.update(kwargs)
            bind_session_context(session, _binding(branch_id="branch-b"))
            return ToolResult("ok", context_barrier=True)

    tools = ToolRegistry()
    tools.register(SyncTool())
    ctx = SimpleNamespace(
        session=session,
        tools=tools,
        request_context=RequestContext(
            channel="discord",
            chat_id="table",
            sender_id="user-1",
            session_key=session.key,
        ),
    )

    await AgentLoop._synchronize_authoritative_domain_context(
        SimpleNamespace(tools=tools),
        ctx,
    )

    assert seen == {
        "view": "binding",
        "payload": {"campaign_id": "campaign-1"},
    }
    assert session.get_history() == []
    assert session.metadata[DOMAIN_CONTEXT_BINDING_KEY]["branch_id"] == "branch-b"


@pytest.mark.asyncio
async def test_pre_turn_sync_supports_action_campaign_query_schema() -> None:
    session = Session(key="telegram:table")
    bind_session_context(session, _binding())
    seen: dict[str, object] = {}

    class SyncTool(Tool):
        _context_sync = True
        _domain_context = "sagasmith-dnd"

        @property
        def name(self) -> str:
            return "mcp_sagasmith_coc_campaign_query"

        @property
        def description(self) -> str:
            return "sync"

        @property
        def parameters(self) -> dict[str, object]:
            return {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "campaign_id": {"type": "string"},
                },
            }

        async def execute(self, **kwargs: object) -> ToolResult:
            seen.update(kwargs)
            bind_session_context(session, _binding())
            return ToolResult("ok")

    tools = ToolRegistry()
    tools.register(SyncTool())
    ctx = SimpleNamespace(
        session=session,
        tools=tools,
        request_context=RequestContext(
            channel="telegram",
            chat_id="table",
            sender_id="member-2",
            actor_principal="user:member-2",
            conversation_principal="group:table",
            session_key=session.key,
        ),
    )

    await AgentLoop._synchronize_authoritative_domain_context(
        SimpleNamespace(tools=tools),
        ctx,
    )

    assert seen == {"action": "get", "campaign_id": "campaign-1"}


@pytest.mark.asyncio
async def test_pre_turn_sync_reopens_authorized_exposure_after_server_restart() -> None:
    session = Session(key="discord:table")
    bind_session_context(session, _binding())
    calls: list[tuple[str, dict[str, object]]] = []
    exposure_open = False

    class SyncTool(Tool):
        _context_sync = True
        _domain_context = "sagasmith-dnd"

        @property
        def name(self) -> str:
            return "mcp_sagasmith_dnd_campaign_query"

        @property
        def description(self) -> str:
            return "sync"

        @property
        def parameters(self) -> dict[str, object]:
            return {
                "type": "object",
                "properties": {
                    "view": {"type": "string"},
                    "payload": {"type": "object"},
                },
            }

        async def execute(self, **kwargs: object) -> ToolResult:
            calls.append(("sync", kwargs))
            if not exposure_open:
                return ToolResult.error("authorization_epoch is stale")
            bind_session_context(session, _binding())
            return ToolResult("ok")

    class ExposureTool(Tool):
        _original_name = "exposure"
        _domain_context = "sagasmith-dnd"

        @property
        def name(self) -> str:
            return "mcp_sagasmith_dnd_exposure"

        @property
        def description(self) -> str:
            return "exposure"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            nonlocal exposure_open
            calls.append(("exposure", kwargs))
            exposure_open = True
            return ToolResult("ok")

    tools = ToolRegistry()
    tools.register(SyncTool())
    tools.register(ExposureTool())
    ctx = SimpleNamespace(
        session=session,
        tools=tools,
        request_context=RequestContext(
            channel="discord",
            chat_id="table",
            sender_id="user-1",
            session_key=session.key,
        ),
    )

    await AgentLoop._synchronize_authoritative_domain_context(
        SimpleNamespace(tools=tools),
        ctx,
    )

    assert calls == [
        ("sync", {"view": "binding", "payload": {"campaign_id": "campaign-1"}}),
        ("exposure", {"action": "open", "campaign_id": "campaign-1"}),
        ("sync", {"view": "binding", "payload": {"campaign_id": "campaign-1"}}),
    ]


@pytest.mark.asyncio
async def test_pre_turn_sync_stays_closed_when_exposure_reopen_is_rejected() -> None:
    session = Session(key="discord:table")
    bind_session_context(session, _binding())

    class FailingTool(Tool):
        _context_sync = True
        _domain_context = "sagasmith-dnd"

        @property
        def name(self) -> str:
            return "mcp_sagasmith_dnd_campaign_query"

        @property
        def description(self) -> str:
            return "sync"

        @property
        def parameters(self) -> dict[str, object]:
            return {
                "type": "object",
                "properties": {
                    "view": {"type": "string"},
                    "payload": {"type": "object"},
                },
            }

        async def execute(self, **_kwargs: object) -> ToolResult:
            return ToolResult.error("stale")

    class RejectedExposure(Tool):
        _original_name = "exposure"
        _domain_context = "sagasmith-dnd"

        @property
        def name(self) -> str:
            return "mcp_sagasmith_dnd_exposure"

        @property
        def description(self) -> str:
            return "exposure"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **_kwargs: object) -> ToolResult:
            return ToolResult.error("access revoked")

    tools = ToolRegistry()
    tools.register(FailingTool())
    tools.register(RejectedExposure())
    ctx = SimpleNamespace(
        session=session,
        tools=tools,
        request_context=RequestContext(
            channel="discord",
            chat_id="table",
            sender_id="user-1",
            session_key=session.key,
        ),
    )

    with pytest.raises(RuntimeError, match="synchronization failed"):
        await AgentLoop._synchronize_authoritative_domain_context(
            SimpleNamespace(tools=tools),
            ctx,
        )


@pytest.mark.asyncio
async def test_pre_turn_sync_fails_closed_without_domain_capability() -> None:
    session = Session(key="discord:table")
    bind_session_context(session, _binding())
    tools = ToolRegistry()
    ctx = SimpleNamespace(
        session=session,
        tools=tools,
        request_context=RequestContext(
            channel="discord",
            chat_id="table",
            sender_id="user-1",
            session_key=session.key,
        ),
    )

    with pytest.raises(RuntimeError, match="cannot be synchronized"):
        await AgentLoop._synchronize_authoritative_domain_context(
            SimpleNamespace(tools=tools),
            ctx,
        )
