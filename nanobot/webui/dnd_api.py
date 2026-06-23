"""D&D REST API handlers for the WebUI dashboard."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from nanobot.dnd.db.campaigns import CampaignService
from nanobot.dnd.db.characters import CharacterService
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.dnd.db.world import WorldService


def _db() -> Database:
    db = Database()
    db.upgrade_schema()
    return db


# ── Campaigns ────────────────────────────────────────────────────────────

def list_campaigns(args: dict[str, str] | None = None) -> list[dict[str, Any]]:
    args = args or {}
    status = args.get("status") or None
    svc = CampaignService(_db())
    try:
        campaigns = svc.list(status=status)
        return [asdict(c) for c in campaigns]
    finally:
        _db().dispose()


def get_campaign(campaign_id: str) -> dict[str, Any]:
    svc = CampaignService(_db())
    try:
        return asdict(svc.get(campaign_id))
    finally:
        _db().dispose()


def create_campaign(body: dict[str, Any]) -> dict[str, Any]:
    svc = CampaignService(_db())
    try:
        info = svc.create(
            body["name"],
            campaign_id=body.get("id"),
            module_name=body.get("module_name"),
            description=body.get("description"),
            rule_set_id=body.get("rule_set_id"),
            publication_ids=body.get("publication_ids"),
            locale=body.get("locale"),
        )
        return asdict(info)
    finally:
        _db().dispose()


def delete_campaign(campaign_id: str) -> dict[str, Any]:
    svc = CampaignService(_db())
    try:
        svc.delete(campaign_id)
        return {"deleted": campaign_id}
    finally:
        _db().dispose()


def set_campaign_status(campaign_id: str, status: str) -> dict[str, Any]:
    svc = CampaignService(_db())
    try:
        return asdict(svc.set_status(campaign_id, status))
    finally:
        _db().dispose()


# ── Characters ───────────────────────────────────────────────────────────

def list_characters(args: dict[str, str] | None = None) -> list[dict[str, Any]]:
    args = args or {}
    svc = CharacterService(_db())
    try:
        characters = svc.list(
            campaign_id=args.get("campaign_id"),
            character_type=args.get("character_type"),
        )
        return [asdict(c) for c in characters]
    finally:
        _db().dispose()


def get_character(character_id: str) -> dict[str, Any]:
    svc = CharacterService(_db())
    try:
        return asdict(svc.get(character_id))
    finally:
        _db().dispose()


def create_character(body: dict[str, Any]) -> dict[str, Any]:
    svc = CharacterService(_db())
    try:
        info = svc.create(
            body["name"],
            character_id=body.get("id"),
            character_type=body.get("character_type", "pc"),
            campaign_id=body.get("campaign_id"),
            player_name=body.get("player_name"),
            class_name=body.get("class_name"),
            level=body.get("level"),
            hp=body.get("hp"),
            max_hp=body.get("max_hp"),
            armor_class=body.get("armor_class"),
            sheet_json=body.get("sheet_json"),
            race=body.get("race"),
            background=body.get("background"),
            alignment=body.get("alignment"),
            personality_traits=body.get("personality_traits"),
            ideals=body.get("ideals"),
            bonds=body.get("bonds"),
            flaws=body.get("flaws"),
            appearance=body.get("appearance"),
            backstory=body.get("backstory"),
            goals=body.get("goals"),
            notes=body.get("notes"),
            portrait_url=body.get("portrait_url"),
        )
        return asdict(info)
    finally:
        _db().dispose()


def update_character(character_id: str, body: dict[str, Any]) -> dict[str, Any]:
    svc = CharacterService(_db())
    try:
        return asdict(svc.update(character_id, **body))
    finally:
        _db().dispose()


def bind_character(character_id: str, campaign_id: str) -> dict[str, Any]:
    svc = CharacterService(_db())
    try:
        return asdict(svc.bind_to_campaign(character_id, campaign_id))
    finally:
        _db().dispose()


def unbind_character(character_id: str) -> dict[str, Any]:
    svc = CharacterService(_db())
    try:
        return asdict(svc.unbind_from_campaign(character_id))
    finally:
        _db().dispose()


# ── World State ──────────────────────────────────────────────────────────

def get_world_state(campaign_id: str) -> dict[str, Any]:
    world = WorldService(_db())
    try:
        return {
            "summary": world.get_summary(campaign_id),
            "factions": world.get_factions(campaign_id),
            "npcs": world.list_npc_statuses(campaign_id),
            "quests": world.get_quests(campaign_id),
            "full_state": world.get_full_state(campaign_id),
        }
    finally:
        _db().dispose()


def update_faction(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    world = WorldService(_db())
    try:
        return world.update_faction(
            campaign_id,
            body["faction_name"],
            int(body.get("delta", 0)),
            note=body.get("note", ""),
        )
    finally:
        _db().dispose()


def update_npc_status(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    world = WorldService(_db())
    try:
        return world.set_npc_status(
            campaign_id,
            body["character_id"],
            status=body.get("status"),
            attitude=body.get("attitude"),
            trust=body.get("trust"),
            fear=body.get("fear"),
            note=body.get("note"),
            location=body.get("location"),
        )
    finally:
        _db().dispose()


def update_npc_attitude(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    world = WorldService(_db())
    try:
        return world.update_npc_attitude(
            campaign_id,
            body["character_id"],
            int(body.get("delta", 0)),
            note=body.get("note", ""),
        )
    finally:
        _db().dispose()


# ── Saves ────────────────────────────────────────────────────────────────

def list_saves(campaign_id: str) -> list[dict[str, Any]]:
    snapshots = CampaignSnapshotService(_db())
    try:
        return [asdict(s) for s in snapshots.list(campaign_id)]
    finally:
        _db().dispose()


# ── Campaign Room ────────────────────────────────────────────────────────

def get_campaign_room(campaign_id: str) -> dict[str, Any]:
    """Return the shared chat session info for a campaign room."""
    from nanobot.dnd.db.campaigns import CampaignService
    from nanobot.dnd.db.characters import CharacterService

    db = _db()
    try:
        campaigns = CampaignService(db)
        try:
            campaigns.get(campaign_id)
        except Exception:
            raise ValueError(f"campaign not found: {campaign_id}")

        chars = CharacterService(db)
        pcs = chars.list(campaign_id=campaign_id, character_type="pc")

        return {
            "campaign_id": campaign_id,
            "chat_id": campaign_id,
            "session_key": f"campaign:{campaign_id}",
            "characters": [
                {"id": c.id, "name": c.name, "class_name": c.class_name,
                 "level": c.level, "player_name": c.player_name}
                for c in pcs
            ],
        }
    finally:
        db.dispose()


# ── Rules ────────────────────────────────────────────────────────────────

def rule_status() -> dict[str, Any]:
    from nanobot.dnd.db.models import EmbeddingModel, RuleChunk, RulePublication, RuleSet, RuleSource
    from sqlalchemy import func, select

    db = _db()
    try:
        with db.transaction() as session:
            rule_sets = []
            for rs in session.scalars(select(RuleSet).order_by(RuleSet.locale, RuleSet.edition)):
                pc = session.scalar(
                    select(func.count()).select_from(RulePublication).where(
                        RulePublication.rule_set_id == rs.id
                    )
                ) or 0
                cc = session.scalar(
                    select(func.count()).select_from(RuleChunk).join(
                        RuleSource, RuleSource.id == RuleChunk.source_id
                    ).where(RuleSource.rule_set_id == rs.id)
                ) or 0
                rule_sets.append({
                    "id": rs.id,
                    "game_system": rs.game_system,
                    "edition": rs.edition,
                    "release": rs.release,
                    "locale": rs.locale,
                    "publications": int(pc),
                    "chunks": int(cc),
                })
            return {"rule_sets": rule_sets}
    finally:
        db.dispose()
