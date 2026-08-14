import json
from copy import deepcopy

import pytest

from nanobot.agent.npc_conversation import (
    NpcConversationWorkerError,
    NpcConversationWorkerPool,
    normalize_worker_proposal,
)
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.npc_conversation import NpcConversationWorkerTool
from nanobot.agent.tools.registry import ToolRegistry
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
