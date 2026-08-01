"""Host-side context boundaries for MCP-owned domains.

Domain state remains authoritative in the MCP server.  This module only keeps
the Agent's conversational memory from crossing a campaign, principal, role,
audience, or branch boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DOMAIN_CONTEXT_BINDING_KEY = "_domain_context_binding"
MEMORY_POLICY_KEY = "_memory_policy"
MEMORY_POLICY_STANDARD = "standard"
MEMORY_POLICY_DOMAIN_AUTHORITATIVE = "domain_authoritative"
MEMORY_POLICY_ISOLATED = "isolated"

_ALLOWED_POLICIES = {
    MEMORY_POLICY_STANDARD,
    MEMORY_POLICY_DOMAIN_AUTHORITATIVE,
    MEMORY_POLICY_ISOLATED,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if required and not result:
        raise ValueError(f"{field} is required")
    return result


def principal_fingerprint(principal_id: str) -> str:
    """Return a stable non-reversible principal identifier for session metadata."""

    return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DomainContextBinding:
    """One stable conversational authority/audience epoch."""

    domain: str
    campaign_id: str
    principal_fingerprint: str
    role: str = ""
    audience: str = ""
    branch_id: str = ""
    context_epoch: str = ""
    memory_policy: str = MEMORY_POLICY_DOMAIN_AUTHORITATIVE

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainContextBinding":
        policy = _text(value.get("memory_policy"), "memory_policy")
        policy = policy or MEMORY_POLICY_DOMAIN_AUTHORITATIVE
        if policy not in _ALLOWED_POLICIES:
            raise ValueError(f"unsupported memory_policy: {policy}")
        fingerprint = _text(
            value.get("principal_fingerprint"),
            "principal_fingerprint",
            required=True,
        )
        if _SHA256.fullmatch(fingerprint) is None:
            raise ValueError("principal_fingerprint must be a lowercase SHA-256 digest")
        binding = cls(
            domain=_text(value.get("domain"), "domain", required=True),
            campaign_id=_text(value.get("campaign_id"), "campaign_id", required=True),
            principal_fingerprint=fingerprint,
            role=_text(value.get("role"), "role"),
            audience=_text(value.get("audience"), "audience"),
            branch_id=_text(value.get("branch_id"), "branch_id"),
            context_epoch=_text(value.get("context_epoch"), "context_epoch"),
            memory_policy=policy,
        )
        expected_epoch = binding.derived_epoch()
        if binding.context_epoch and binding.context_epoch != expected_epoch:
            raise ValueError("context_epoch does not match the binding fields")
        return cls(**{**binding.__dict__, "context_epoch": expected_epoch})

    def derived_epoch(self) -> str:
        payload = {
            "domain": self.domain,
            "campaign_id": self.campaign_id,
            "principal_fingerprint": self.principal_fingerprint,
            "role": self.role,
            "audience": self.audience,
            "branch_id": self.branch_id,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "campaign_id": self.campaign_id,
            "principal_fingerprint": self.principal_fingerprint,
            "role": self.role,
            "audience": self.audience,
            "branch_id": self.branch_id,
            "context_epoch": self.context_epoch or self.derived_epoch(),
            "memory_policy": self.memory_policy,
        }


def binding_from_metadata(metadata: Mapping[str, Any] | None) -> DomainContextBinding | None:
    raw = metadata.get(DOMAIN_CONTEXT_BINDING_KEY) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    try:
        return DomainContextBinding.from_mapping(raw)
    except ValueError:
        return None


def validate_authoritative_binding(
    authority: Mapping[str, Any],
    *,
    field: str = "authority",
) -> DomainContextBinding:
    """Require one exact domain-authoritative binding matching its envelope."""

    raw = authority.get("host_context_binding")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field}.host_context_binding is required")
    binding = DomainContextBinding.from_mapping(raw)
    if binding.memory_policy != MEMORY_POLICY_DOMAIN_AUTHORITATIVE:
        raise ValueError(
            f"{field}.host_context_binding must use domain_authoritative memory"
        )
    campaign_id = _text(authority.get("campaign_id"), f"{field}.campaign_id", required=True)
    branch_id = _text(authority.get("branch_id"), f"{field}.branch_id", required=True)
    if binding.campaign_id != campaign_id or binding.branch_id != branch_id:
        raise ValueError(f"{field}.host_context_binding does not match bundle authority")
    return binding


def memory_policy_for_metadata(metadata: Mapping[str, Any] | None) -> str:
    binding = binding_from_metadata(metadata)
    if binding is not None:
        return binding.memory_policy
    raw = metadata.get(MEMORY_POLICY_KEY) if isinstance(metadata, Mapping) else None
    return raw if isinstance(raw, str) and raw in _ALLOWED_POLICIES else MEMORY_POLICY_STANDARD


def bind_session_context(session: Any, binding: DomainContextBinding) -> bool:
    """Bind a session and create a hard replay barrier when its epoch changes.

    Historical messages remain on disk for audit, but ``last_consolidated`` is
    advanced so they are not replayed into the new epistemic context.
    """

    normalized = DomainContextBinding.from_mapping(binding.to_dict())
    previous = binding_from_metadata(getattr(session, "metadata", None))
    changed = previous is None or previous.context_epoch != normalized.context_epoch
    if changed:
        session.last_consolidated = len(session.messages)
        session.metadata.pop("_last_summary", None)
    session.metadata[DOMAIN_CONTEXT_BINDING_KEY] = normalized.to_dict()
    session.metadata[MEMORY_POLICY_KEY] = normalized.memory_policy
    return changed


def admit_current_user_to_context_epoch(session: Any) -> bool:
    """Keep the already-persisted current user turn on the new side of a barrier.

    The caller must invoke this only while its own pending-user marker is set.
    MCP binding happens after the inbound turn is durably appended, so a hard
    barrier would otherwise leave the eventual assistant reply without the user
    message that caused it when history is replayed on the next turn.
    """

    binding = binding_from_metadata(getattr(session, "metadata", None))
    messages = getattr(session, "messages", None)
    if binding is None or not isinstance(messages, list) or not messages:
        return False
    index = len(messages) - 1
    message = messages[index]
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    message.update(history_attributes(session.metadata))
    session.last_consolidated = min(int(session.last_consolidated), index)
    return True


def history_attributes(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return safe persistence attributes for one consolidated history record."""

    binding = binding_from_metadata(metadata)
    if binding is None or binding.memory_policy == MEMORY_POLICY_STANDARD:
        return {}
    return {
        "classification": "campaign_private",
        "dream_eligible": False,
        "prompt_eligible": False,
        "context_namespace": (
            f"{binding.domain}:{binding.campaign_id}:{binding.principal_fingerprint}"
        ),
        "context_epoch": binding.context_epoch,
    }


def summary_matches_context(
    metadata: Mapping[str, Any] | None,
    summary_metadata: Mapping[str, Any] | None,
) -> bool:
    binding = binding_from_metadata(metadata)
    if binding is None:
        return True
    if not isinstance(summary_metadata, Mapping):
        return False
    return summary_metadata.get("context_epoch") == binding.context_epoch


def stamp_summary_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    binding = binding_from_metadata(metadata)
    return {"context_epoch": binding.context_epoch} if binding is not None else {}
