"""Campaign narrative memory — store, retrieve, and prune long-term facts."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models.runtime import CampaignMemory


class CampaignMemoryService:
    """Read, write, and prune campaign-scoped narrative memories.

    Memories are stored in the database, NOT in USER.md. USER.md only holds
    player-role name mappings.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert(
        self,
        campaign_id: str,
        *,
        kind: str,
        text: str,
        priority: str = "medium",
        status: str = "candidate",
        entity_type: str | None = None,
        entity_id: str | None = None,
        fact_type: str | None = None,
        source_save_id: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        """Insert or update a memory based on the entity-fact unique key.

        When entity_type/entity_id/fact_type are all provided and a matching row
        exists, the existing row is updated. Otherwise a new row is created.

        Returns the memory id.
        """
        with self.database.transaction() as session:
            return upsert_memory_in_session(
                session,
                campaign_id,
                kind=kind,
                text=text,
                priority=priority,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                fact_type=fact_type,
                source_save_id=source_save_id,
                supersedes=supersedes,
            )

    def get_active(self, campaign_id: str) -> list[dict[str, Any]]:
        """Return memories with status IN ('stable', 'permanent')."""
        with self.database.transaction() as session:
            rows = session.scalars(
                select(CampaignMemory)
                .where(
                    and_(
                        CampaignMemory.campaign_id == campaign_id,
                        CampaignMemory.status.in_(["stable", "permanent"]),
                    )
                )
                .order_by(CampaignMemory.priority.desc(), CampaignMemory.updated_at.desc())
            ).all()

            return [_memory_row_to_dict(r) for r in rows]

    def list_by_status(
        self, campaign_id: str, statuses: list[str]
    ) -> list[dict[str, Any]]:
        """Return memories matching any of the given statuses."""
        with self.database.transaction() as session:
            rows = session.scalars(
                select(CampaignMemory)
                .where(
                    and_(
                        CampaignMemory.campaign_id == campaign_id,
                        CampaignMemory.status.in_(statuses),
                    )
                )
                .order_by(CampaignMemory.priority.desc(), CampaignMemory.updated_at.desc())
            ).all()
            return [_memory_row_to_dict(r) for r in rows]

    def prune(self, campaign_id: str, min_score: int = 3) -> int:
        """Remove candidate memories with score below threshold.

        Returns count of removed memories.
        """
        with self.database.transaction() as session:
            rows = session.scalars(
                select(CampaignMemory).where(
                    and_(
                        CampaignMemory.campaign_id == campaign_id,
                        CampaignMemory.status == "candidate",
                        CampaignMemory.score < min_score,
                    )
                )
            ).all()

            count = len(rows)
            for r in rows:
                session.delete(r)
            session.flush()
            return count

    def get_by_save(self, source_save_id: str) -> list[dict[str, Any]]:
        """Return all memories created from a specific save."""
        with self.database.transaction() as session:
            rows = session.scalars(
                select(CampaignMemory).where(
                    CampaignMemory.source_save_id == source_save_id,
                )
            ).all()
            return [_memory_row_to_dict(r) for r in rows]


def _memory_row_to_dict(row: CampaignMemory) -> dict[str, Any]:
    return {
        "id": row.id,
        "campaign_id": row.campaign_id,
        "kind": row.kind,
        "text": row.text,
        "priority": row.priority,
        "status": row.status,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "fact_type": row.fact_type,
        "supersedes": row.supersedes,
        "source_save_id": row.source_save_id,
        "score": row.score,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def trigger_memory_from_recap(
    database: Database,
    campaign_id: str,
    save_id: str,
    recap: dict[str, Any],
) -> list[dict[str, Any]]:
    """Process recap.memory_candidates in its own transaction."""
    with database.transaction() as session:
        return trigger_memory_from_recap_in_session(session, campaign_id, save_id, recap)


def upsert_memory_in_session(
    session: Session,
    campaign_id: str,
    *,
    kind: str,
    text: str,
    priority: str = "medium",
    status: str = "candidate",
    entity_type: str | None = None,
    entity_id: str | None = None,
    fact_type: str | None = None,
    source_save_id: str | None = None,
    supersedes: str | None = None,
) -> str:
    """Insert or update a memory using an existing DB transaction."""
    now = datetime.now(UTC)
    entity_type = entity_type or "plot"
    entity_id = entity_id or _stable_key("entity", kind, text)
    fact_type = fact_type or kind

    existing = session.scalar(
        select(CampaignMemory).where(
            and_(
                CampaignMemory.campaign_id == campaign_id,
                CampaignMemory.entity_type == entity_type,
                CampaignMemory.entity_id == entity_id,
                CampaignMemory.fact_type == fact_type,
            )
        )
    )

    if existing is not None:
        existing.kind = kind
        existing.text = text
        existing.priority = priority
        existing.status = status
        existing.source_save_id = source_save_id
        if supersedes:
            existing.supersedes = supersedes
        existing.updated_at = now
        session.flush()
        return existing.id

    memory_id = f"mem_{uuid.uuid4().hex[:16]}"
    session.add(
        CampaignMemory(
            id=memory_id,
            campaign_id=campaign_id,
            kind=kind,
            text=text,
            priority=priority,
            status=status,
            entity_type=entity_type,
            entity_id=entity_id,
            fact_type=fact_type,
            supersedes=supersedes,
            source_save_id=source_save_id,
            score=None,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    return memory_id


def trigger_memory_from_recap_in_session(
    session: Session,
    campaign_id: str,
    save_id: str,
    recap: dict[str, Any],
) -> list[dict[str, Any]]:
    """Process recap.memory_candidates and write to campaign_memories table.

    - P0 (priority=high) → status="permanent"
    - P1 (priority=medium) → status="candidate"
    - P2 (priority=low) → skipped (only in snapshot)
    - future_impact items → status="candidate" (as plot_commitment kind)
    - player_choices with irreversible flag → status="permanent"

    Returns list of memory actions taken, suitable for SnapshotInfo.memory_actions.
    Failures are logged and do not raise.
    """
    actions: list[dict[str, Any]] = []

    candidates = recap.get("memory_candidates", [])
    if not isinstance(candidates, list):
        candidates = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind", "unknown")
        text = candidate.get("text", "")
        priority = candidate.get("priority", "medium")

        if not text:
            continue

        # P2: low priority → skip
        if priority == "low":
            actions.append({
                "action": "skipped",
                "kind": kind,
                "priority": priority,
                "text": text[:100],
                "reason": "low priority (P2, snapshot only)",
            })
            continue

        # P0: high → permanent; P1: medium → candidate
        status = "permanent" if priority == "high" else "candidate"

        entity_type, entity_id, fact_type = _memory_identity(candidate, kind, text)

        try:
            memory_id = upsert_memory_in_session(
                session,
                campaign_id,
                kind=kind,
                text=text,
                priority=priority,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                fact_type=fact_type or kind,
                source_save_id=save_id,
            )
            actions.append({
                "action": "upsert",
                "kind": kind,
                "priority": priority,
                "status": status,
                "memory_id": memory_id,
                "text": text[:100],
                "source_save_id": save_id,
            })
        except Exception as exc:
            logger.warning("Failed to upsert memory candidate: {}", exc)
            actions.append({
                "action": "error",
                "kind": kind,
                "priority": priority,
                "text": text[:100],
                "error": str(exc),
            })

    # Also process future_impact as plot_commitment memories (P1)
    future_impacts = recap.get("future_impact", [])
    if isinstance(future_impacts, list):
        for impact in future_impacts:
            if not isinstance(impact, str) or not impact.strip():
                continue
            try:
                memory_id = upsert_memory_in_session(
                    session,
                    campaign_id,
                    kind="plot_commitment",
                    text=f"后续影响: {impact.strip()}",
                    priority="medium",
                    status="candidate",
                    entity_type="plot",
                    entity_id="future_impact",
                    fact_type=_stable_key("impact", impact),
                    source_save_id=save_id,
                )
                actions.append({
                    "action": "upsert",
                    "kind": "plot_commitment",
                    "priority": "medium",
                    "status": "candidate",
                    "memory_id": memory_id,
                    "text": impact.strip()[:100],
                    "source_save_id": save_id,
                })
            except Exception as exc:
                logger.warning("Failed to upsert future_impact memory: {}", exc)

    return actions


def _memory_identity(
    candidate: dict[str, Any],
    kind: str,
    text: str,
) -> tuple[str, str, str]:
    """Return a stable non-null unique key for a recap memory candidate."""
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


def _derive_entity(kind: str, text: str) -> tuple[str | None, str | None, str | None]:
    """Derive entity_type/entity_id/fact_type from the memory kind and text.

    Heuristic mapping:
    - npc_relation → entity_type="npc"
    - plot_commitment → entity_type="plot"
    - location_fact → entity_type="location"
    - quest_state → entity_type="quest"
    - faction_relation → entity_type="faction"
    - item_fact → entity_type="item"
    """
    entity_type = _entity_type_for_kind(kind)
    return entity_type, _stable_key("entity", entity_type, text), kind
