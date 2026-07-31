from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.npc_turn import (
    NpcTurnError,
    NpcTurnRunner,
    normalize_npc_turn_proposal,
    validate_proposal_against_bundle,
)
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.npc_portrayal import PortrayNpcTool
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest
from nanobot.utils.llm_runtime import LLMRuntime


class QueueProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.generation = GenerationSettings(temperature=0.1, max_tokens=8_192)

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "npc-test"


def _runtime(provider: QueueProvider) -> LLMRuntime:
    return LLMRuntime.capture(
        provider,
        "npc-test",
        context_window_tokens=128_000,
    )


def _bundle() -> dict[str, Any]:
    allowed = ["actor:npc-1:identity", "stimulus:abc"]
    bundle = {
        "schema_version": 1,
        "bundle_id": "bundle-1",
        "purpose": "npc_turn",
        "authority": {
            "campaign_id": "campaign-1",
            "branch_id": "branch-1",
            "head_snapshot_id": None,
            "campaign_revision": 4,
            "latest_event_sequence": 8,
            "actor_revision": 2,
            "scene_state_version": 1,
        },
        "actor": {
            "id": "npc-1",
            "name": "Zaltember",
            "character_type": "npc",
            "summary": "A frightened giant child.",
            "revision": 2,
            "profile": {},
            "self_state": {},
        },
        "interlocutors": [
            {"id": "pc-1", "name": "Envoy", "character_type": "pc"}
        ],
        "stimulus": {
            "kind": "speech",
            "speaker_actor_id": "pc-1",
            "content": "Who are you?",
            "language": "Common",
            "target_actor_ids": ["npc-1"],
            "source_event_ids": [],
            "basis_ref": "stimulus:abc",
        },
        "perception": [],
        "actor_knowledge": [],
        "common_context": [],
        "relationships": [],
        "goals": [],
        "conversation_window": [],
        "scene": None,
        "portrayal_context": [
            {
                "source_excerpt": "DM-only characterization",
                "context_role": "dm_portrayal_context",
                "disclosure_policy": "not_speakable_without_actor_basis",
            }
        ],
        "constraints": {
            "allowed_basis_refs": allowed,
            "allowed_target_actor_ids": ["npc-1", "pc-1"],
            "module_evidence_is_actor_knowledge": False,
            "common_context_is_actor_knowledge": False,
            "may_roll_dice": False,
            "may_call_tools": False,
            "may_write_state": False,
            "output_contract": "npc-turn-proposal.v1",
        },
        "retrieval": {},
        "bundle_receipt": {
            "bundle_id": "bundle-1",
            "actor_id": "npc-1",
            "allowed_basis_refs": allowed,
            "signature": "signed-by-mcp",
        },
    }
    unsigned = {key: value for key, value in bundle.items() if key != "bundle_receipt"}
    bundle["bundle_receipt"]["bundle_digest"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return bundle


def _proposal() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "bundle_id": "bundle-1",
        "speaker_actor_id": "npc-1",
        "intent": {"kind": "identify", "summary": "Seek safety through leverage."},
        "utterance": {
            "text": "I am Duke Zalto's son.",
            "language": "Common",
            "delivery": "frightened",
        },
        "speech_acts": [
            {
                "kind": "assert",
                "content": "He identifies himself.",
                "truth_posture": "believes_true",
                "basis_refs": ["actor:npc-1:identity"],
                "targets": ["pc-1"],
            }
        ],
        "proposed_action": {"kind": "none", "target_ref": "", "summary": ""},
        "resolution_requests": [],
        "proposed_deltas": {"facts": [], "actor_knowledge": []},
        "portrayal": {"emotion": "afraid", "visible_cues": ["avoids eye contact"]},
        "decision_summary": "Identity may discourage immediate harm.",
    }


@pytest.mark.asyncio
async def test_npc_turn_runner_uses_fresh_tool_free_nonpersistent_call() -> None:
    provider = QueueProvider(
        [LLMResponse(content=json.dumps(_proposal()), finish_reason="stop")]
    )
    runner = NpcTurnRunner()

    result = await runner.run(_bundle(), runtime=_runtime(provider))

    assert result.proposal == _proposal()
    assert result.tools_exposed == 0
    assert result.session_persisted is False
    assert result.generation_attempts == 1
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None
    assert len(provider.calls[0]["messages"]) == 2
    assert [item["role"] for item in provider.calls[0]["messages"]] == ["system", "user"]
    assert "workspace" not in provider.calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_npc_turn_runner_rejects_a_bundle_changed_after_receipt_issue() -> None:
    provider = QueueProvider(
        [LLMResponse(content=json.dumps(_proposal()), finish_reason="stop")]
    )
    bundle = _bundle()
    bundle["actor"]["summary"] = "Injected replacement identity"

    with pytest.raises(NpcTurnError, match="bundle digest"):
        await NpcTurnRunner().run(bundle, runtime=_runtime(provider))

    assert provider.calls == []


@pytest.mark.asyncio
async def test_npc_turn_runner_repairs_once_without_exposing_tools_or_history() -> None:
    provider = QueueProvider(
        [
            LLMResponse(content="not json", finish_reason="stop"),
            LLMResponse(content=f"```json\n{json.dumps(_proposal())}\n```", finish_reason="stop"),
        ]
    )

    result = await NpcTurnRunner().run(_bundle(), runtime=_runtime(provider))

    assert result.generation_attempts == 2
    assert len(provider.calls) == 2
    assert all(call["tools"] is None for call in provider.calls)
    assert all(len(call["messages"]) == 2 for call in provider.calls)
    repair_payload = json.loads(provider.calls[1]["messages"][1]["content"])
    assert repair_payload["task"] == "propose_npc_turn"
    assert repair_payload["repair"]["invalid_output"] == "not json"
    assert "did not return one JSON object" in repair_payload["repair"]["validation_error"]


@pytest.mark.asyncio
async def test_npc_turn_runner_rejects_second_invalid_or_forbidden_output() -> None:
    provider = QueueProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call-1", name="search", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="still not json", finish_reason="stop"),
        ]
    )

    with pytest.raises(NpcTurnError, match="did not return one JSON object"):
        await NpcTurnRunner().run(_bundle(), runtime=_runtime(provider))

    assert all(call["tools"] is None for call in provider.calls)


@pytest.mark.asyncio
async def test_npc_turn_runner_can_apply_a_fresh_zero_tool_guardian() -> None:
    provider = QueueProvider(
        [
            LLMResponse(content=json.dumps(_proposal()), finish_reason="stop"),
            LLMResponse(
                content=json.dumps({"approved": True, "issues": []}),
                finish_reason="stop",
            ),
        ]
    )

    result = await NpcTurnRunner().run(
        _bundle(),
        runtime=_runtime(provider),
        strict_guardian=True,
    )

    assert result.guardian_checks == 1
    assert len(provider.calls) == 2
    assert all(call["tools"] is None for call in provider.calls)
    assert "approved" in provider.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_npc_portrayal_tool_returns_a_proposal_not_authoritative_state() -> None:
    provider = QueueProvider(
        [LLMResponse(content=json.dumps(_proposal()), finish_reason="stop")]
    )
    tool = PortrayNpcTool(NpcTurnRunner())
    context = RequestContext(
        channel="cli",
        chat_id="direct",
        runtime=_runtime(provider),
    )

    with request_context(context):
        value = json.loads(await tool.execute(bundle=_bundle()))

    assert value["proposal"] == _proposal()
    assert value["isolation"]["tools_exposed"] == 0
    assert value["isolation"]["session_persisted"] is False
    assert "committed" not in value


def test_background_subagents_cannot_recursively_portray_npcs(tmp_path: Path) -> None:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )

    assert not manager._build_tools().has("portray_npc")


def test_agent_contract_requires_engine_resolution_for_mechanical_actions() -> None:
    proposal = _proposal()
    proposal["utterance"]["text"] = ""
    proposal["speech_acts"] = []
    proposal["proposed_action"] = {
        "kind": "attack",
        "target_ref": "actor:pc-1",
        "summary": "Attack the envoy.",
    }

    with pytest.raises(NpcTurnError, match="requires an explicit resolution request"):
        normalize_npc_turn_proposal(proposal)

    proposal["resolution_requests"] = [
        {
            "kind": "attack",
            "reason": "Resolve the attack through the combat engine.",
            "actor_ids": ["npc-1", "outsider"],
            "suggested_skill": "",
        }
    ]
    normalized = normalize_npc_turn_proposal(proposal)
    with pytest.raises(NpcTurnError, match="target actors outside its bundle"):
        validate_proposal_against_bundle(normalized, _bundle())

    normalized["resolution_requests"][0]["actor_ids"] = ["npc-1", "pc-1"]
    normalized["proposed_action"]["target_ref"] = "actor:outsider"
    with pytest.raises(NpcTurnError, match="action target is outside its bundle"):
        validate_proposal_against_bundle(normalized, _bundle())
