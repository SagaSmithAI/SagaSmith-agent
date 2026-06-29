"""Branch-aware campaign narrative memory."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models.runtime import (
    CampaignMemory,
    CampaignMemoryRevision,
    CampaignSave,
    CampaignSaveAncestor,
    CampaignTimelineHead,
)


class CampaignMemoryService:
    """Resolve campaign facts from immutable save-scoped revisions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        campaign_id: str,
        save_id: str,
        *,
        kind: str,
        text: str,
        priority: str = "medium",
        status: str = "candidate",
        entity_type: str,
        entity_id: str,
        fact_type: str,
        operation: str = "set",
    ) -> tuple[str, str]:
        with self.database.transaction() as session:
            return record_memory_revision_in_session(
                session,
                campaign_id,
                save_id,
                kind=kind,
                text=text,
                priority=priority,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                fact_type=fact_type,
                operation=operation,
            )

    def get_active(self, campaign_id: str) -> list[dict[str, Any]]:
        return self.get_effective(
            campaign_id,
            statuses=["stable", "permanent"],
        )

    def list_by_status(
        self,
        campaign_id: str,
        statuses: list[str],
        *,
        save_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get_effective(
            campaign_id,
            save_id=save_id,
            statuses=statuses,
        )

    def get_by_save(self, save_id: str) -> list[dict[str, Any]]:
        with self.database.transaction() as session:
            revisions = session.scalars(
                select(CampaignMemoryRevision)
                .where(CampaignMemoryRevision.save_id == save_id)
                .order_by(CampaignMemoryRevision.created_at, CampaignMemoryRevision.id)
            ).all()
            return [
                _revision_to_dict(session, revision, distance=0)
                for revision in revisions
            ]

    def get_effective(
        self,
        campaign_id: str,
        *,
        save_id: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.transaction() as session:
            return effective_memories_in_session(
                session,
                campaign_id,
                save_id=save_id,
                statuses=statuses,
            )

    def scope(
        self,
        campaign_id: str,
        *,
        save_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as session:
            target_id, ancestors = memory_scope_in_session(
                session,
                campaign_id,
                save_id=save_id,
            )
            memories = effective_memories_in_session(
                session,
                campaign_id,
                save_id=target_id,
            )
            return {
                "campaign_id": campaign_id,
                "save_id": target_id,
                "included_saves": ancestors,
                "effective_revision_ids": [
                    row["revision_id"] for row in memories
                ],
                "effective_memory_count": len(memories),
            }


def trigger_memory_from_recap(
    database: Database,
    campaign_id: str,
    save_id: str,
    recap: dict[str, Any],
) -> list[dict[str, Any]]:
    with database.transaction() as session:
        return trigger_memory_from_recap_in_session(
            session,
            campaign_id,
            save_id,
            recap,
        )


def trigger_memory_from_recap_in_session(
    session: Session,
    campaign_id: str,
    save_id: str,
    recap: dict[str, Any],
) -> list[dict[str, Any]]:
    """Write recap candidates as revisions attached to one save node."""
    actions: list[dict[str, Any]] = []
    candidates = recap.get("memory_candidates", [])
    if not isinstance(candidates, list):
        candidates = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = str(candidate.get("kind") or "unknown")
        text = str(candidate.get("text") or "").strip()
        priority = str(candidate.get("priority") or "medium")
        if not text:
            continue
        if priority == "low":
            actions.append({
                "action": "skipped",
                "kind": kind,
                "priority": priority,
                "text": text[:100],
                "reason": "low priority (snapshot recap only)",
            })
            continue

        entity_type, entity_id, fact_type = _memory_identity(candidate, kind, text)
        status = "permanent" if priority == "high" else "candidate"
        memory_id, revision_id = record_memory_revision_in_session(
            session,
            campaign_id,
            save_id,
            kind=kind,
            text=text,
            priority=priority,
            status=status,
            entity_type=entity_type,
            entity_id=entity_id,
            fact_type=fact_type,
        )
        actions.append({
            "action": "upsert",
            "kind": kind,
            "priority": priority,
            "status": status,
            "memory_id": memory_id,
            "revision_id": revision_id,
            "text": text[:100],
            "source_save_id": save_id,
        })

    future_impacts = recap.get("future_impact", [])
    if isinstance(future_impacts, list):
        for impact in future_impacts:
            if not isinstance(impact, str) or not impact.strip():
                continue
            text = f"后续影响: {impact.strip()}"
            memory_id, revision_id = record_memory_revision_in_session(
                session,
                campaign_id,
                save_id,
                kind="plot_commitment",
                text=text,
                priority="medium",
                status="candidate",
                entity_type="plot",
                entity_id="future_impact",
                fact_type=_stable_key("impact", impact),
            )
            actions.append({
                "action": "upsert",
                "kind": "plot_commitment",
                "priority": "medium",
                "status": "candidate",
                "memory_id": memory_id,
                "revision_id": revision_id,
                "text": impact.strip()[:100],
                "source_save_id": save_id,
            })

    return actions


def record_memory_revision_in_session(
    session: Session,
    campaign_id: str,
    save_id: str,
    *,
    kind: str,
    text: str,
    priority: str,
    status: str,
    entity_type: str,
    entity_id: str,
    fact_type: str,
    operation: str = "set",
) -> tuple[str, str]:
    """Upsert a logical fact and its value at one immutable save node."""
    save = session.get(CampaignSave, save_id)
    if save is None or save.campaign_id != campaign_id:
        raise ValueError(
            f"save does not belong to campaign: campaign={campaign_id}, save={save_id}"
        )

    memory = session.scalar(
        select(CampaignMemory).where(
            and_(
                CampaignMemory.campaign_id == campaign_id,
                CampaignMemory.entity_type == entity_type,
                CampaignMemory.entity_id == entity_id,
                CampaignMemory.fact_type == fact_type,
            )
        )
    )
    if memory is None:
        memory = CampaignMemory(
            id=f"mem_{uuid.uuid4().hex[:16]}",
            campaign_id=campaign_id,
            kind=kind,
            entity_type=entity_type,
            entity_id=entity_id,
            fact_type=fact_type,
        )
        session.add(memory)
        session.flush()
    elif memory.kind != kind:
        memory.kind = kind

    revision = session.scalar(
        select(CampaignMemoryRevision).where(
            CampaignMemoryRevision.memory_id == memory.id,
            CampaignMemoryRevision.save_id == save_id,
        )
    )
    if revision is None:
        revision = CampaignMemoryRevision(
            id=f"memrev_{uuid.uuid4().hex}",
            campaign_id=campaign_id,
            memory_id=memory.id,
            save_id=save_id,
            operation=operation,
            text=text,
            priority=priority,
            status=status,
        )
        session.add(revision)
    else:
        revision.operation = operation
        revision.text = text
        revision.priority = priority
        revision.status = status
    session.flush()
    return memory.id, revision.id


def memory_scope_in_session(
    session: Session,
    campaign_id: str,
    *,
    save_id: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return target save and root-to-target Chroma scope."""
    target_id = save_id
    if target_id is None:
        head = session.get(CampaignTimelineHead, campaign_id)
        target_id = head.active_save_id if head else None
    if target_id is None:
        return None, []
    target = session.get(CampaignSave, target_id)
    if target is None or target.campaign_id != campaign_id:
        raise ValueError(
            f"save does not belong to campaign: campaign={campaign_id}, save={target_id}"
        )
    rows = session.execute(
        select(CampaignSaveAncestor.distance, CampaignSave)
        .join(
            CampaignSave,
            CampaignSave.id == CampaignSaveAncestor.ancestor_save_id,
        )
        .where(CampaignSaveAncestor.descendant_save_id == target_id)
        .order_by(CampaignSaveAncestor.distance.desc())
    ).all()
    return target_id, [
        {
            "id": save.id,
            "slot": save.slot,
            "label": save.label,
            "distance": distance,
        }
        for distance, save in rows
    ]


def effective_memories_in_session(
    session: Session,
    campaign_id: str,
    *,
    save_id: str | None = None,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve one nearest non-deleted revision per logical fact."""
    target_id, ancestors = memory_scope_in_session(
        session,
        campaign_id,
        save_id=save_id,
    )
    if target_id is None:
        return []
    distance_by_save = {
        str(row["id"]): int(row["distance"])
        for row in ancestors
    }
    revisions = session.scalars(
        select(CampaignMemoryRevision).where(
            CampaignMemoryRevision.campaign_id == campaign_id,
            CampaignMemoryRevision.save_id.in_(distance_by_save),
        )
    ).all()
    revisions.sort(
        key=lambda revision: (
            distance_by_save[revision.save_id],
            revision.created_at,
            revision.id,
        )
    )

    selected: dict[str, CampaignMemoryRevision] = {}
    for revision in revisions:
        selected.setdefault(revision.memory_id, revision)

    allowed_statuses = set(statuses) if statuses else None
    result: list[dict[str, Any]] = []
    for revision in selected.values():
        if revision.operation == "delete":
            continue
        if allowed_statuses is not None and revision.status not in allowed_statuses:
            continue
        result.append(
            _revision_to_dict(
                session,
                revision,
                distance=distance_by_save[revision.save_id],
            )
        )
    result.sort(
        key=lambda row: (
            _priority_rank(str(row["priority"])),
            -int(row["distance"]),
            str(row["created_at"]),
        ),
        reverse=True,
    )
    return result


def _revision_to_dict(
    session: Session,
    revision: CampaignMemoryRevision,
    *,
    distance: int,
) -> dict[str, Any]:
    memory = session.get(CampaignMemory, revision.memory_id)
    if memory is None:
        raise RuntimeError(f"memory fact missing for revision: {revision.id}")
    return {
        "id": memory.id,
        "revision_id": revision.id,
        "campaign_id": memory.campaign_id,
        "kind": memory.kind,
        "entity_type": memory.entity_type,
        "entity_id": memory.entity_id,
        "fact_type": memory.fact_type,
        "text": revision.text,
        "priority": revision.priority,
        "status": revision.status,
        "operation": revision.operation,
        "source_save_id": revision.save_id,
        "distance": distance,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def _priority_rank(priority: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(priority, 0)


def _memory_identity(
    candidate: dict[str, Any],
    kind: str,
    text: str,
) -> tuple[str, str, str]:
    entity_type = str(candidate.get("entity_type") or _entity_type_for_kind(kind))
    entity_id = str(candidate.get("entity_id") or _stable_key("entity", entity_type, text))
    fact_type = str(candidate.get("fact_type") or kind)
    return entity_type, entity_id, fact_type


def _entity_type_for_kind(kind: str) -> str:
    return {
        "npc_relation": "npc",
        "plot_commitment": "plot",
        "location_fact": "location",
        "quest_state": "quest",
        "faction_relation": "faction",
        "item_fact": "item",
    }.get(kind, "plot")


def _stable_key(prefix: str, *parts: str) -> str:
    raw = "\n".join(_normalize_key_part(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _normalize_key_part(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().casefold())
