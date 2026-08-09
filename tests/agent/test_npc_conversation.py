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
        "schema_version": 1,
        "contract": "npc-conversation.v1",
        "conversation_id": "conversation",
        "activation_id": f"activation-{sequence}",
        "actor_runtime_id": actor_runtime_id,
        "actor_id": actor_id,
        "lease_id": f"lease-{sequence}",
        "lease_expires_at_ns": 9999999999999999999,
        "context_manifest": {
            "campaign_id": "campaign",
            "branch_id": "branch",
            "actor_revision": 2,
            "campaign_revision": 5,
            "working_state_revision": 0,
            "inbox_cursor": sequence,
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
                "perceived_by": [actor_id],
                "understood_by": [actor_id],
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
            "output_contract": "npc-conversation-proposal.v2",
        },
    }


def _proposal(capsule, text="My answer."):
    return {
        "schema_version": 2,
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
        "proposed_action": {"kind": "none", "target_ref": "", "summary": ""},
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


def test_worker_rejects_free_utterance_and_uncited_factual_segment() -> None:
    capsule = _capsule()
    free = _proposal(capsule)
    free["utterance"] = {"text": "Bypass."}
    with pytest.raises(NpcConversationWorkerError, match="unknown fields"):
        normalize_worker_proposal(free, capsule)

    uncited = _proposal(capsule)
    uncited["utterance_segments"][0]["basis_refs"] = []
    with pytest.raises(NpcConversationWorkerError, match="requires a basis_ref"):
        normalize_worker_proposal(uncited, capsule)


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


@pytest.mark.asyncio
async def test_bridge_keeps_private_capsule_and_proposal_out_of_director_result() -> None:
    capsule = _capsule()
    provider = FakeProvider([_proposal(capsule, "Safe publication.")])
    registry = ToolRegistry()
    checkout = FakeMcpTool("npc_activation_checkout", capsule)

    def submit_response(kwargs):
        assert kwargs["proposal"]["private_intent"] == "Keep a private secret."
        return {
            "status": "published",
            "publication": {
                "speech": kwargs["proposal"]["utterance_segments"][0]["text"],
                "visible_cues": [],
            },
            "resolution_requests": [],
        }

    submit = FakeMcpTool("npc_activation_submit", submit_response)
    registry.register(checkout)
    registry.register(submit)
    tool = NpcConversationWorkerTool(registry)
    activation = {
        "activation_id": capsule["activation_id"],
        "actor_runtime_id": capsule["actor_runtime_id"],
        "actor_id": capsule["actor_id"],
        "worker_handle": "opaque-handle",
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
    assert checkout.calls[0]["include_bootstrap"] is True
    assert submit.calls[0]["lease_id"] == capsule["lease_id"]

    released = json.loads(await tool.execute("release", "conversation"))
    assert released["released_workers"] == 1
