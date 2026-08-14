from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.domain_context import DomainContextBinding, principal_fingerprint
from nanobot.agent.isolated_evaluation import (
    DEFAULT_ISOLATED_CONTRACTS,
    IsolatedEvaluationError,
    IsolatedEvaluationRunner,
)
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.isolated_evaluation import IsolatedEvaluateTool
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider, LLMResponse
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
        return "isolated-test"


def _runtime(provider: QueueProvider) -> LLMRuntime:
    return LLMRuntime.capture(provider, "isolated-test", context_window_tokens=128_000)


def _bundle(kind: str) -> dict[str, Any]:
    subject_kind = {
        "actor_turn": "actor",
        "audience_render": "audience",
        "faction_turn": "faction",
        "source_interpretation": "source",
        "bounded_ruling": "ruling",
    }[kind]
    subject_id = {
        "actor_turn": "npc-1",
        "audience_render": "player-1",
        "faction_turn": "ember-court",
        "source_interpretation": "source-1",
        "bounded_ruling": "ruling-1",
    }[kind]
    output_contract = {
        "actor_turn": "actor-turn-proposal.v1",
        "audience_render": "audience-render-proposal.v1",
        "faction_turn": "faction-turn-proposal.v1",
        "source_interpretation": "source-interpretation-proposal.v1",
        "bounded_ruling": "bounded-ruling-proposal.v1",
    }[kind]
    allowed_basis = ["fact:known", "source:decision-only"]
    claim_basis = ["fact:known"]
    allowed_targets = ["actor:npc-1", "actor:pc-1"]
    host_binding = DomainContextBinding(
        domain="sagasmith-dnd",
        campaign_id="campaign-1",
        principal_fingerprint=principal_fingerprint("principal-1"),
        authorization_fingerprint="b" * 64,
        role="dm",
        audience="dm",
        branch_id="branch-1",
    ).to_dict()
    bundle = {
        "schema_version": 1,
        "bundle_id": f"bundle-{kind}",
        "purpose": kind,
        "authority": {
            "campaign_id": "campaign-1",
            "branch_id": "branch-1",
            "head_snapshot_id": None,
            "campaign_revision": 4,
            "latest_event_sequence": 8,
            "host_context_binding": host_binding,
        },
        "subject": {"kind": subject_kind, "id": subject_id, "name": subject_id},
        "context": {
            "question": "What happens next?" if kind == "source_interpretation" else "",
            "facts": [],
            "source_evidence": [],
        },
        "constraints": {
            "allowed_basis_refs": allowed_basis,
            "allowed_claim_basis_refs": claim_basis,
            "decision_only_basis_refs": ["source:decision-only"],
            "allowed_target_refs": allowed_targets,
            "may_roll_dice": False,
            "may_call_tools": False,
            "may_write_state": False,
            "output_contract": output_contract,
        },
    }
    unsigned_digest = hashlib.sha256(
        json.dumps(
            bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    bundle["bundle_receipt"] = {
        "schema_version": 1,
        "purpose": kind,
        "bundle_id": bundle["bundle_id"],
        "bundle_digest": unsigned_digest,
        "subject_ref": f"{subject_kind}:{subject_id}",
        "principal_fingerprint": host_binding["principal_fingerprint"],
        "allowed_basis_refs": allowed_basis,
        "allowed_claim_basis_refs": claim_basis,
        "allowed_target_refs": allowed_targets,
        "question_digest": hashlib.sha256(
            str(bundle["context"].get("question") or "").strip().encode("utf-8")
        ).hexdigest(),
        "signature": "signed-by-mcp",
    }
    return bundle


def _proposal(kind: str) -> dict[str, Any]:
    common = {"schema_version": 1, "bundle_id": f"bundle-{kind}", "purpose": kind}
    if kind == "actor_turn":
        return {
            **common,
            "actor_id": "npc-1",
            "intent": "Answer cautiously.",
            "proposed_action": {"kind": "none", "target_ref": "", "summary": ""},
            "claims": [],
            "resolution_requests": [],
            "decision_summary": "The actor does not commit.",
        }
    if kind == "audience_render":
        return {
            **common,
            "text": "The envoy waits.",
            "cited_basis_refs": ["fact:known"],
            "omitted_sensitive_refs": [],
            "decision_summary": "Only visible context was rendered.",
        }
    if kind == "faction_turn":
        return {
            **common,
            "faction_id": "ember-court",
            "intent": "Delay escalation.",
            "proposed_actions": [
                {
                    "kind": "send_message",
                    "target_ref": "actor:pc-1",
                    "summary": "Send a guarded answer.",
                    "basis_refs": ["source:decision-only"],
                }
            ],
            "claims": [],
            "resolution_requests": [],
            "decision_summary": "No state is declared changed.",
        }
    if kind == "source_interpretation":
        return {
            **common,
            "question": "What happens next?",
            "interpretation": "The source leaves timing to the DM.",
            "claims": [
                {
                    "statement": "Timing is not fixed.",
                    "basis_refs": ["fact:known"],
                    "posture": "supported",
                }
            ],
            "ambiguities": [],
            "requires_dm_review": False,
        }
    return {
        **common,
        "ruling": "Ask the engine to resolve a check.",
        "claims": [],
        "engine_requests": [
            {
                "kind": "ability_check",
                "reason": "The result is uncertain.",
                "actor_ids": ["pc-1"],
            }
        ],
        "unresolved": [],
        "decision_summary": "No roll was declared by the evaluator.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(DEFAULT_ISOLATED_CONTRACTS))
async def test_all_fixed_contracts_use_fresh_zero_tool_calls(kind: str) -> None:
    provider = QueueProvider(
        [LLMResponse(content=json.dumps(_proposal(kind)), finish_reason="stop")]
    )
    result = await IsolatedEvaluationRunner().run(
        kind,
        _bundle(kind),
        runtime=_runtime(provider),
    )

    assert result.kind == kind
    assert result.proposal == _proposal(kind)
    assert result.tools_exposed == 0
    assert result.session_persisted is False
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None
    assert [item["role"] for item in provider.calls[0]["messages"]] == [
        "system",
        "user",
    ]
    payload = json.loads(provider.calls[0]["messages"][1]["content"])
    assert payload["task"] == f"propose_{kind}"
    assert payload["output_shape"]["purpose"] == kind
    assert payload["bundle"]["bundle_id"] == f"bundle-{kind}"


def test_claims_cannot_promote_decision_only_source_context() -> None:
    contract = DEFAULT_ISOLATED_CONTRACTS["source_interpretation"]
    bundle = contract.validate_bundle(_bundle("source_interpretation"))
    proposal = _proposal("source_interpretation")
    proposal["claims"][0]["basis_refs"] = ["source:decision-only"]
    normalized = contract.normalize_proposal(proposal)

    with pytest.raises(IsolatedEvaluationError, match="decision-only"):
        contract.validate_proposal(normalized, bundle)


@pytest.mark.parametrize("kind", ["actor_turn", "faction_turn"])
def test_generic_turn_contracts_reject_untyped_state_deltas(kind: str) -> None:
    proposal = _proposal(kind)
    proposal["proposed_deltas"] = [{"hp": -99}]

    with pytest.raises(IsolatedEvaluationError, match="unknown fields.*proposed_deltas"):
        DEFAULT_ISOLATED_CONTRACTS[kind].normalize_proposal(proposal)


def test_source_interpretation_cannot_change_question_or_self_approve_ambiguity() -> None:
    contract = DEFAULT_ISOLATED_CONTRACTS["source_interpretation"]
    bundle = contract.validate_bundle(_bundle("source_interpretation"))

    mismatched_receipt = _bundle("source_interpretation")
    mismatched_receipt["bundle_receipt"]["question_digest"] = "0" * 64
    with pytest.raises(IsolatedEvaluationError, match="receipt does not match"):
        contract.validate_bundle(mismatched_receipt)

    changed_question = _proposal("source_interpretation")
    changed_question["question"] = "A different question"
    with pytest.raises(IsolatedEvaluationError, match="question does not match"):
        contract.validate_proposal(
            contract.normalize_proposal(changed_question),
            bundle,
        )

    unreviewed = _proposal("source_interpretation")
    unreviewed["ambiguities"] = ["The source is incomplete."]
    with pytest.raises(IsolatedEvaluationError, match="require DM review"):
        contract.normalize_proposal(unreviewed)

    no_claims = _proposal("source_interpretation")
    no_claims["claims"] = []
    with pytest.raises(IsolatedEvaluationError, match="evidence-bound claim"):
        contract.normalize_proposal(no_claims)

    no_basis = _proposal("source_interpretation")
    no_basis["claims"] = [
        {"statement": "A guess.", "basis_refs": [], "posture": "opinion"}
    ]
    with pytest.raises(IsolatedEvaluationError, match="evidence-bound claim"):
        contract.normalize_proposal(no_basis)


def test_bundle_requires_matching_authoritative_host_context() -> None:
    contract = DEFAULT_ISOLATED_CONTRACTS["actor_turn"]
    missing = _bundle("actor_turn")
    del missing["authority"]["host_context_binding"]
    with pytest.raises(IsolatedEvaluationError, match="host_context_binding.*required"):
        contract.validate_bundle(missing)

    mismatched = _bundle("actor_turn")
    binding = dict(mismatched["authority"]["host_context_binding"])
    binding["branch_id"] = "another-branch"
    binding["context_epoch"] = ""
    mismatched["authority"]["host_context_binding"] = binding
    unsigned = dict(mismatched)
    unsigned.pop("bundle_receipt")
    mismatched["bundle_receipt"]["bundle_digest"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(IsolatedEvaluationError, match="does not match bundle authority"):
        contract.validate_bundle(mismatched)


@pytest.mark.asyncio
async def test_isolated_evaluate_tool_returns_proposal_without_authority() -> None:
    kind = "audience_render"
    provider = QueueProvider(
        [LLMResponse(content=json.dumps(_proposal(kind)), finish_reason="stop")]
    )
    tool = IsolatedEvaluateTool(IsolatedEvaluationRunner())
    with request_context(
        RequestContext(channel="cli", chat_id="direct", runtime=_runtime(provider))
    ):
        result = json.loads(await tool.execute(kind=kind, bundle=_bundle(kind)))

    assert result["proposal"] == _proposal(kind)
    assert result["isolation"]["tools_exposed"] == 0
    assert result["isolation"]["session_persisted"] is False
    assert "committed" not in result


@pytest.mark.asyncio
async def test_isolated_evaluate_tool_runs_independent_jobs_concurrently() -> None:
    kind = "audience_render"
    provider = QueueProvider(
        [
            LLMResponse(content=json.dumps(_proposal(kind)), finish_reason="stop"),
            LLMResponse(content=json.dumps(_proposal(kind)), finish_reason="stop"),
        ]
    )
    tool = IsolatedEvaluateTool(IsolatedEvaluationRunner())
    with request_context(
        RequestContext(channel="cli", chat_id="direct", runtime=_runtime(provider))
    ):
        result = json.loads(
            await tool.execute(
                jobs=[
                    {"kind": kind, "bundle": _bundle(kind)},
                    {"kind": kind, "bundle": _bundle(kind)},
                ]
            )
        )

    assert [item["index"] for item in result["results"]] == [0, 1]
    assert all(item["proposal"] == _proposal(kind) for item in result["results"])
    assert len(provider.calls) == 2
    assert all(call["tools"] is None for call in provider.calls)


@pytest.mark.asyncio
async def test_isolated_evaluate_tool_requires_one_call_shape() -> None:
    tool = IsolatedEvaluateTool(IsolatedEvaluationRunner())
    provider = QueueProvider([])
    with request_context(
        RequestContext(channel="cli", chat_id="direct", runtime=_runtime(provider))
    ):
        missing = await tool.execute()
        mixed = await tool.execute(
            kind="audience_render",
            bundle=_bundle("audience_render"),
            jobs=[{"kind": "audience_render", "bundle": _bundle("audience_render")}],
        )

    assert "provide exactly one" in missing
    assert "provide exactly one" in mixed


def test_background_subagents_cannot_recursively_evaluate(tmp_path: Path) -> None:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )

    assert not manager._build_tools().has("isolated_evaluate")
