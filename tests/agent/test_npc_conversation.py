import json
from copy import deepcopy
from pathlib import Path

import pytest

from nanobot.agent.npc_conversation import (
    NpcConversationWorkerError,
    NpcConversationWorkerPool,
    normalize_worker_proposal,
)
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.npc_conversation import NpcConversationWorkerTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig
from nanobot.providers.base import GenerationSettings, LLMResponse
from nanobot.utils.llm_runtime import LLMRuntime


def _capsule(*, actor_id="npc", bootstrap=True, sequence=1):
    actor_runtime_id = f"conversation:{actor_id}"
    return {
        "schema_version": 3,
        "contract": "npc-conversation.v3",
        "conversation_id": "conversation",
        "activation_id": f"activation-{sequence}",
        "actor_runtime_id": actor_runtime_id,
        "actor_id": actor_id,
        "lease_id": f"lease-{sequence}",
        "lease_expires_at_ns": 9999999999999999999,
        "conversation_revision": 2,
        "context_manifest": {
            "campaign_id": "campaign",
            "branch_id": "branch",
            "actor_revision": 2,
            "working_state_revision": 0,
            "inbox_cursor": sequence,
            "conversation_revision": 2,
        },
        "bootstrap": (
            {
                "actor": {"id": actor_id, "name": f"Actor {actor_id}"},
                "actor_knowledge": [
                    {
                        "id": f"secret-{actor_id}",
                        "proposition": f"private knowledge for {actor_id}",
                    }
                ],
                "constraints": {"allowed_basis_refs": [f"actor:{actor_id}:identity"]},
            }
            if bootstrap
            else None
        ),
        "working_state": {"facts": [], "actor_knowledge": [], "commitments": []},
        "inbox": [
            {
                "event_id": f"conversation-event:conversation:{sequence}",
                "sequence": sequence,
                "type": "speech",
                "speaker_actor_id": "pc",
                "content": f"Question {sequence}",
                "comprehension": "full",
                "audience_decision_id": f"audience-{sequence}",
            }
        ],
        "constraints": {
            "allowed_basis_refs": [
                f"actor:{actor_id}:identity",
                f"conversation-event:conversation:{sequence}",
            ],
            "allowed_target_actor_ids": [actor_id, "pc"],
            "may_call_tools": False,
            "may_roll_dice": False,
            "may_write_state": False,
            "output_contract": "npc-conversation-proposal.v4",
        },
    }


def _proposal(capsule, text="My answer."):
    return {
        "schema_version": 4,
        "conversation_id": capsule["conversation_id"],
        "activation_id": capsule["activation_id"],
        "actor_runtime_id": capsule["actor_runtime_id"],
        "response_bid": {"should_respond": True, "urgency": 60, "reason": "Asked."},
        "private_intent": "Keep a private secret.",
        "utterance_segments": [
            {
                "text": text,
                "speech_act": "assert",
                "truth_posture": "believes_true",
                "basis_refs": [f"actor:{capsule['actor_id']}:identity"],
                "targets": ["pc"],
                "language": "Common",
                "delivery": "calmly",
            }
        ],
        "proposed_action": {
            "summary": "",
            "target_refs": [],
            "settlement": "narrative",
            "mechanic_hint": "",
        },
        "resolution_requests": [],
        "working_deltas": {"facts": [], "actor_knowledge": [], "commitments": []},
        "visible_cues": [],
        "decision_summary": "Answer while protecting the secret.",
    }


class FakeProvider:
    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.calls = []

    async def chat_with_retry(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        proposal = self.proposals.pop(0)
        return LLMResponse(
            content=json.dumps(proposal),
            usage={"prompt_tokens": 100, "cached_tokens": 40},
        )


class CapsuleProposalProvider:
    def __init__(self) -> None:
        self.calls = []

    async def chat_with_retry(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        prompt = json.loads(kwargs["messages"][-1]["content"])
        actor_runtime_id = str(prompt["actor_runtime_id"])
        actor_id = actor_runtime_id.split(":", 1)[1]
        targets = [
            item for item in prompt["constraints"]["allowed_target_actor_ids"] if item != actor_id
        ]
        proposal = {
            "schema_version": 4,
            "conversation_id": prompt["conversation_id"],
            "activation_id": prompt["activation_id"],
            "actor_runtime_id": actor_runtime_id,
            "response_bid": {
                "should_respond": True,
                "urgency": 60,
                "reason": "A direct question was asked.",
            },
            "private_intent": "Keep unrelated private context private.",
            "utterance_segments": [
                {
                    "text": "The boat left just before dusk.",
                    "speech_act": "answer",
                    "truth_posture": "believes_true",
                    "basis_refs": [prompt["constraints"]["allowed_basis_refs"][0]],
                    "targets": targets[:1],
                    "language": "English",
                    "delivery": "quietly",
                }
            ],
            "proposed_action": {
                "summary": "",
                "target_refs": [],
                "settlement": "narrative",
                "mechanic_hint": "",
            },
            "resolution_requests": [],
            "working_deltas": {"facts": [], "actor_knowledge": [], "commitments": []},
            "visible_cues": ["glances toward the harbor"],
            "decision_summary": "Answer the investigator without exposing private context.",
        }
        return LLMResponse(content=json.dumps(proposal), usage={"prompt_tokens": 100})


def _runtime(provider):
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(temperature=0.2, max_tokens=1000),
        context_window_tokens=16_000,
    )


@pytest.mark.asyncio
async def test_worker_reuses_one_actor_context_and_cache_friendly_prefix() -> None:
    first = _capsule(sequence=1)
    second = _capsule(bootstrap=False, sequence=2)
    provider = FakeProvider([_proposal(first, "First."), _proposal(second, "Second.")])
    pool = NpcConversationWorkerPool()

    assert pool.checkout_options("conversation", "conversation:npc") == {
        "cursor": 0,
        "include_bootstrap": True,
    }
    assert (await pool.activate(first, runtime=_runtime(provider)))["utterance_segments"][0][
        "text"
    ] == "First."
    pool.confirm_last_activation("conversation", "conversation:npc")
    assert pool.checkout_options("conversation", "conversation:npc") == {
        "cursor": 1,
        "include_bootstrap": False,
    }

    await pool.activate(second, runtime=_runtime(provider))
    pool.confirm_last_activation("conversation", "conversation:npc")
    assert provider.calls[1]["messages"][:3] == provider.calls[0]["messages"][:3]
    assert len(provider.calls[1]["messages"]) > len(provider.calls[0]["messages"])
    status = pool.status("conversation")
    assert status["worker_count"] == 1
    assert status["workers"][0]["turn_count"] == 2
    assert status["workers"][0]["cached_tokens"] == 80


@pytest.mark.asyncio
async def test_different_npcs_never_share_private_message_history() -> None:
    mara = _capsule(actor_id="mara")
    tomas = _capsule(actor_id="tomas")
    provider = FakeProvider([_proposal(mara), _proposal(tomas)])
    pool = NpcConversationWorkerPool()

    await pool.activate(mara, runtime=_runtime(provider))
    pool.confirm_last_activation("conversation", "conversation:mara")
    await pool.activate(tomas, runtime=_runtime(provider))

    mara_prompt = json.dumps(provider.calls[0]["messages"])
    tomas_prompt = json.dumps(provider.calls[1]["messages"])
    assert "private knowledge for mara" in mara_prompt
    assert "private knowledge for mara" not in tomas_prompt
    assert "private knowledge for tomas" in tomas_prompt


@pytest.mark.asyncio
async def test_worker_repair_preserves_role_order_and_rollback_restores_cursor() -> None:
    capsule = _capsule(sequence=3)
    provider = FakeProvider([{}, _proposal(capsule, "Repaired.")])
    pool = NpcConversationWorkerPool()

    proposal = await pool.activate(capsule, runtime=_runtime(provider))
    assert proposal["utterance_segments"][0]["text"] == "Repaired."
    assert [message["role"] for message in provider.calls[1]["messages"][-3:]] == [
        "user",
        "assistant",
        "user",
    ]
    assert pool.checkout_options("conversation", "conversation:npc")["cursor"] == 3

    pool.rollback_last_activation("conversation", "conversation:npc")
    assert pool.checkout_options("conversation", "conversation:npc") == {
        "cursor": 0,
        "include_bootstrap": False,
    }
    assert pool.status("conversation")["workers"][0]["turn_count"] == 0


def test_worker_checks_transport_identity_but_leaves_semantics_to_mcp() -> None:
    capsule = _capsule()
    free = _proposal(capsule)
    free["utterance"] = {"text": "Bypass."}
    assert normalize_worker_proposal(free, capsule)["utterance"]["text"] == "Bypass."

    wrong = _proposal(capsule)
    wrong["activation_id"] = "another-activation"
    with pytest.raises(NpcConversationWorkerError, match="activation_id"):
        normalize_worker_proposal(wrong, capsule)


class FakeMcpTool:
    def __init__(self, original_name, response, *, server_name="dnd"):
        self._original_name = original_name
        self._server_name = server_name
        self._response = response
        self.calls = []
        self.name = f"mcp_{server_name}_{original_name}"

    async def execute(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        value = self._response(kwargs) if callable(self._response) else self._response
        return json.dumps(value)


def test_host_private_transport_stays_callable_but_out_of_model_definitions() -> None:
    registry = ToolRegistry()
    transport = FakeMcpTool("npc_conversation_transport", {})
    transport._model_visible = False
    registry.register(transport)
    assert transport.name in registry.tool_names
    assert transport.name not in registry.definition_names()


@pytest.mark.asyncio
async def test_bridge_keeps_private_capsule_and_proposal_out_of_director_result() -> None:
    capsule = _capsule()
    provider = FakeProvider([_proposal(capsule, "Safe publication.")])
    registry = ToolRegistry()

    def transport_response(kwargs):
        if kwargs["action"] == "claim_activation":
            return capsule
        assert kwargs["action"] == "submit_proposal"
        proposal = kwargs["payload"]["proposal"]
        assert proposal["private_intent"] == "Keep a private secret."
        return {
            "status": "publication_ready",
            "publication": {
                "speech": proposal["utterance_segments"][0]["text"],
                "visible_cues": [],
            },
            "resolution_requests": [],
            "conversation_revision": 3,
        }

    transport = FakeMcpTool("npc_conversation_transport", transport_response)
    registry.register(transport)
    tool = NpcConversationWorkerTool(registry)
    activation = {
        "activation_ref": "opaque-activation-ref",
        "actor_id": capsule["actor_id"],
        "from_cursor": 0,
        "conversation_revision": 1,
    }
    with request_context(
        RequestContext(channel="test", chat_id="chat", runtime=_runtime(provider))
    ):
        rendered = await tool.execute(
            "activate",
            "conversation",
            campaign_id="campaign",
            activation=activation,
        )
    result = json.loads(rendered)
    assert result["publication"]["speech"] == "Safe publication."
    assert "private knowledge" not in rendered
    assert "private_intent" not in rendered
    assert "proposal" not in rendered
    assert transport.calls[0]["action"] == "claim_activation"
    assert transport.calls[0]["payload"]["include_bootstrap"] is True
    assert transport.calls[1]["payload"]["lease_id"] == capsule["lease_id"]

    released = json.loads(await tool.execute("release", "conversation"))
    assert released["released_workers"] == 1


@pytest.mark.asyncio
async def test_bridge_repairs_mcp_validation_failure_within_same_lease() -> None:
    capsule = _capsule()
    first = _proposal(capsule, "Unaccepted.")
    repaired = _proposal(capsule, "Repaired.")
    provider = FakeProvider([first, repaired])
    calls = []

    def transport_response(kwargs):
        calls.append(deepcopy(kwargs))
        if kwargs["action"] == "claim_activation":
            return capsule
        submissions = [item for item in calls if item["action"] == "submit_proposal"]
        if len(submissions) == 1:
            return {
                "status": "validation_failed",
                "validation_issues": [{"path": "proposal", "message": "repair me"}],
                "lease_retained": True,
                "conversation_revision": 2,
            }
        return {
            "status": "publication_ready",
            "publication": {"speech": "Repaired."},
            "conversation_revision": 3,
        }

    registry = ToolRegistry()
    registry.register(FakeMcpTool("npc_conversation_transport", transport_response))
    tool = NpcConversationWorkerTool(registry)
    with request_context(
        RequestContext(channel="test", chat_id="chat", runtime=_runtime(provider))
    ):
        rendered = await tool.execute(
            "activate",
            "conversation",
            campaign_id="campaign",
            activation={
                "activation_ref": "activation-ref",
                "actor_id": "npc",
                "from_cursor": 0,
                "conversation_revision": 1,
            },
        )
    result = json.loads(rendered)
    assert result["publication"]["speech"] == "Repaired."
    submissions = [item for item in calls if item["action"] == "submit_proposal"]
    assert len(submissions) == 2
    assert {item["payload"]["lease_id"] for item in submissions} == {capsule["lease_id"]}
    repair_payload = json.loads(provider.calls[1]["messages"][-1]["content"])
    assert "npc-conversation-proposal.v4" in repair_payload["instruction"]
    assert "raw allowed_target_actor_ids" in repair_payload["instruction"]


@pytest.mark.asyncio
async def test_real_coc_stdio_host_dispatches_hidden_conversation_transport(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    coc_repo = workspace / "SagaSmith-coc-mcp"
    executable = coc_repo / ".venv" / "Scripts" / "sagasmith-coc-mcp.exe"
    if not executable.exists():
        pytest.skip("the sibling CoC MCP development runtime is unavailable")

    registry = ToolRegistry()
    connections = await connect_mcp_servers(
        {
            "coc": MCPServerConfig(
                command=str(executable),
                cwd=str(coc_repo),
                env={
                    "SAGASMITH_COC_MCP_HOME": str(tmp_path / "coc-home"),
                    "SAGASMITH_COC_SKILLS_DIR": str(workspace / "SagaSmith-coc-skills"),
                    "SAGASMITH_MODULEGEN_SKILLS_DIR": str(
                        workspace / "SagaSmith-module-gen-skills"
                    ),
                },
                enabled_tools=["*"],
                expose_resources_and_prompts=False,
            )
        },
        registry,
    )

    async def invoke(tool_name: str, **arguments):
        tool = registry.get(f"mcp_coc_{tool_name}")
        assert tool is not None, tool_name
        result = await tool.execute(**arguments)
        value = json.loads(str(result))
        return value.get("result", value)

    try:
        hidden_name = "mcp_coc_npc_conversation_transport"
        assert hidden_name in registry.tool_names
        assert hidden_name not in registry.definition_names()
        await invoke("exposure", action="open")
        await invoke("exposure", action="set", add_tool_ids=["campaign_change"])
        campaign = await invoke(
            "campaign_change",
            action="create",
            data={"name": "Agent stdio dialogue", "idempotency_key": "campaign"},
        )
        await invoke("exposure", action="open", campaign_id=campaign["id"])
        await invoke(
            "exposure",
            action="set",
            campaign_id=campaign["id"],
            add_tool_ids=["campaign_change", "character_change"],
        )
        investigator = await invoke(
            "character_change",
            action="create",
            campaign_id=campaign["id"],
            data={
                "name": "Morgan",
                "character_type": "investigator",
                "expected_campaign_revision": campaign["revision"],
                "idempotency_key": "investigator",
            },
        )
        npc = await invoke(
            "character_change",
            action="create",
            campaign_id=campaign["id"],
            data={
                "name": "Harbormaster",
                "character_type": "npc",
                "summary": "Knows when the lighthouse boat departed.",
                "expected_campaign_revision": campaign["revision"],
                "idempotency_key": "npc",
            },
        )
        await invoke(
            "campaign_change",
            action="set_phase",
            campaign_id=campaign["id"],
            data={"phase": "play", "expected_revision": campaign["revision"]},
        )
        await invoke(
            "exposure",
            action="set",
            campaign_id=campaign["id"],
            add_tool_ids=["npc_conversation"],
        )
        opened = await invoke(
            "npc_conversation",
            action="open",
            campaign_id=campaign["id"],
            data={
                "participant_actor_ids": [investigator["id"], npc["id"]],
                "query": "lighthouse departure",
                "idempotency_key": "open",
            },
        )
        ingested = await invoke(
            "npc_conversation",
            action="ingest",
            campaign_id=campaign["id"],
            data={
                "conversation_id": opened["conversation_id"],
                "event": {
                    "type": "speech",
                    "speaker_actor_id": investigator["id"],
                    "content": "When did the lighthouse boat leave?",
                    "declared_target_actor_ids": [npc["id"]],
                },
                "audience_facts": {
                    "decision_id": "audience-ingest",
                    "resolver": "agent",
                    "perceived_actor_ids": [investigator["id"], npc["id"]],
                    "understood_actor_ids": [investigator["id"], npc["id"]],
                    "response_actor_ids": [npc["id"]],
                    "partial_renditions": {},
                    "basis_refs": [],
                    "reason": "Both participants are face to face and share English.",
                },
                "expected_conversation_revision": opened["conversation_revision"],
                "idempotency_key": "ingest",
            },
        )
        activation = ingested["activations"][0]
        provider = CapsuleProposalProvider()
        worker = NpcConversationWorkerTool(registry)
        with request_context(
            RequestContext(channel="test", chat_id="real-coc", runtime=_runtime(provider))
        ):
            rendered = await worker.execute(
                "activate",
                opened["conversation_id"],
                campaign_id=campaign["id"],
                activation=activation,
                mcp_server="coc",
            )
        result = json.loads(rendered)
        assert result["status"] == "publication_ready"
        assert result["publication"]["speech"] == "The boat left just before dusk."
        assert "private_intent" not in rendered
        assert "proposal" not in rendered
        published = await invoke(
            "npc_conversation",
            action="publish",
            campaign_id=campaign["id"],
            data={
                "conversation_id": opened["conversation_id"],
                "publication_id": result["publication"]["publication_id"],
                "audience_facts": {
                    "decision_id": "audience-publish",
                    "resolver": "agent",
                    "perceived_actor_ids": [investigator["id"], npc["id"]],
                    "understood_actor_ids": [investigator["id"], npc["id"]],
                    "response_actor_ids": [],
                    "partial_renditions": {},
                    "basis_refs": [],
                    "reason": "The reply is spoken clearly in shared English.",
                },
                "expected_conversation_revision": result["conversation_revision"],
                "idempotency_key": "publish",
            },
        )
        assert published["publication"]["speech"] == "The boat left just before dusk."
    finally:
        for connection in connections.values():
            await connection.aclose()


@pytest.mark.asyncio
async def test_real_dnd_stdio_host_dispatches_hidden_conversation_transport(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    dnd_repo = workspace / "SagaSmith-dnd-mcp"
    executable = dnd_repo / ".venv" / "Scripts" / "sagasmith-dnd-mcp.exe"
    if not executable.exists():
        pytest.skip("the sibling D&D MCP development runtime is unavailable")

    registry = ToolRegistry()
    connections = await connect_mcp_servers(
        {
            "dnd": MCPServerConfig(
                command=str(executable),
                cwd=str(dnd_repo),
                env={
                    "SAGASMITH_DND_MCP_HOME": str(tmp_path / "dnd-home"),
                    "SAGASMITH_DND_SKILLS_DIR": str(workspace / "SagaSmith-dnd-skills"),
                    "SAGASMITH_MODULEGEN_SKILLS_DIR": str(
                        workspace / "SagaSmith-module-gen-skills"
                    ),
                    "SAGASMITH_DND_MCP_AUTO_SEED": "0",
                },
                enabled_tools=["*"],
                expose_resources_and_prompts=False,
            )
        },
        registry,
    )

    async def invoke(tool_name: str, **arguments):
        tool = registry.get(f"mcp_dnd_{tool_name}")
        assert tool is not None, tool_name
        result = await tool.execute(**arguments)
        value = json.loads(str(result))
        return value.get("result", value)

    try:
        hidden_name = "mcp_dnd_npc_conversation_transport"
        assert hidden_name in registry.tool_names
        assert hidden_name not in registry.definition_names()
        await invoke("exposure", action="open")
        await invoke("exposure", action="set", add_tool_ids=["campaign_create"])
        campaign = await invoke(
            "campaign_create",
            name="Agent stdio dialogue",
            idempotency_key="campaign",
        )
        await invoke("exposure", action="open", campaign_id=campaign["id"])
        await invoke(
            "exposure",
            action="set",
            campaign_id=campaign["id"],
            add_tool_ids=["character_create_from"],
        )
        npc = await invoke(
            "character_create_from",
            mode="direct",
            payload={
                "campaign_id": campaign["id"],
                "name": "Mara",
                "character_type": "npc",
                "summary": "Knows when the harbor watch changed.",
            },
            idempotency_key="npc",
        )
        pc = await invoke(
            "character_create_from",
            mode="direct",
            payload={"campaign_id": campaign["id"], "name": "Aria"},
            idempotency_key="pc",
        )
        current = await invoke(
            "campaign_query", view="get", payload={"campaign_id": campaign["id"]}
        )
        await invoke(
            "game_phase",
            campaign_id=campaign["id"],
            action="set",
            tool_profile="play",
            expected_revision=current["revision"],
            idempotency_key="play",
        )
        await invoke(
            "exposure",
            action="set",
            campaign_id=campaign["id"],
            add_tool_ids=["npc_conversation"],
        )
        opened = await invoke(
            "npc_conversation",
            campaign_id=campaign["id"],
            action="open",
            payload={
                "participant_actor_ids": [pc["id"], npc["id"]],
                "query": "harbor watch",
                "idempotency_key": "open",
            },
        )
        ingested = await invoke(
            "npc_conversation",
            campaign_id=campaign["id"],
            action="ingest",
            payload={
                "conversation_id": opened["conversation_id"],
                "event": {
                    "type": "speech",
                    "speaker_actor_id": pc["id"],
                    "content": "When did the harbor watch change?",
                    "language": "Common",
                    "declared_target_actor_ids": [npc["id"]],
                },
                "audience_facts": {
                    "decision_id": "audience-ingest",
                    "resolver": "agent",
                    "perceived_actor_ids": [pc["id"], npc["id"]],
                    "understood_actor_ids": [pc["id"], npc["id"]],
                    "response_actor_ids": [npc["id"]],
                    "partial_renditions": {},
                    "basis_refs": ["scene:current"],
                    "reason": "Both participants are face to face and share Common.",
                },
                "expected_conversation_revision": opened["conversation_revision"],
                "idempotency_key": "ingest",
            },
        )
        provider = CapsuleProposalProvider()
        worker = NpcConversationWorkerTool(registry)
        with request_context(
            RequestContext(channel="test", chat_id="real-dnd", runtime=_runtime(provider))
        ):
            rendered = await worker.execute(
                "activate",
                opened["conversation_id"],
                campaign_id=campaign["id"],
                activation=ingested["activations"][0],
                mcp_server="dnd",
            )
        result = json.loads(rendered)
        assert result["status"] == "publication_ready"
        assert "private_intent" not in rendered
        assert "proposal" not in rendered
        published = await invoke(
            "npc_conversation",
            campaign_id=campaign["id"],
            action="publish",
            payload={
                "conversation_id": opened["conversation_id"],
                "publication_id": result["publication"]["publication_id"],
                "audience_facts": {
                    "decision_id": "audience-publish",
                    "resolver": "agent",
                    "perceived_actor_ids": [pc["id"], npc["id"]],
                    "understood_actor_ids": [pc["id"], npc["id"]],
                    "response_actor_ids": [],
                    "partial_renditions": {},
                    "basis_refs": ["scene:current"],
                    "reason": "The reply is spoken clearly in shared Common.",
                },
                "expected_conversation_revision": result["conversation_revision"],
                "idempotency_key": "publish",
            },
        )
        assert published["publication"]["speech"] == "The boat left just before dusk."
    finally:
        for connection in connections.values():
            await connection.aclose()
