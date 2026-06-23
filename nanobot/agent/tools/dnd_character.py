"""Native character library tool — PCs and NPCs, decoupled from campaigns."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.dnd.db.characters import CharacterService
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.world import WorldService


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create", "list", "show", "update", "bind", "unbind",
                    "npc_attitude", "npc_status", "faction", "world_summary",
                ],
                "description": (
                    "create/list/show/update/bind/unbind: character CRUD. "
                    "npc_attitude: adjust NPC attitude score (+/-). "
                    "npc_status: set or view NPC status (alive/dead/location/trust/fear). "
                    "faction: adjust faction relationship score (+/-). "
                    "world_summary: get compact world state summary."
                ),
            },
            "name": {"type": "string", "description": "Character display name."},
            "character_type": {
                "type": "string",
                "enum": ["pc", "npc"],
                "description": "pc (player character) or npc (non-player character). Default: pc.",
            },
            "character_id": {"type": "string", "description": "Character ID."},
            "campaign_id": {"type": "string", "description": "Campaign ID (for binding or listing by campaign)."},
            "player_name": {"type": "string", "description": "Player name (PCs only)."},
            "class_name": {"type": "string", "description": "Class (Fighter, Wizard, etc.)."},
            "level": {"type": "integer", "description": "Character level."},
            "hp": {"type": "integer", "description": "Current hit points."},
            "max_hp": {"type": "integer", "description": "Maximum hit points."},
            "armor_class": {"type": "integer", "description": "Armor Class."},
            "sheet_json": {"type": "object", "description": "Full character sheet JSON."},
            # Lore fields
            "race": {"type": "string", "description": "Race / species."},
            "background": {"type": "string", "description": "Character background."},
            "alignment": {
                "type": "string",
                "description": "Alignment (LG, NG, CG, LN, N, CN, LE, NE, CE).",
            },
            "personality_traits": {"type": "string", "description": "Personality traits text."},
            "ideals": {"type": "string", "description": "Ideals text."},
            "bonds": {"type": "string", "description": "Bonds text."},
            "flaws": {"type": "string", "description": "Flaws text."},
            "appearance": {"type": "string", "description": "Physical appearance description."},
            "backstory": {"type": "string", "description": "Full backstory text."},
            "goals": {"type": "string", "description": "Current goals and motivations."},
            "notes": {"type": "string", "description": "DM notes (hidden from players)."},
            "portrait_url": {"type": "string", "description": "Portrait image URL or path."},
            # Update-specific
            "fields": {
                "type": "object",
                "description": "For update action: dict of field_name → new_value.",
            },
            # NPC attitude / faction
            "npc_name": {"type": "string", "description": "NPC name for attitude/status actions."},
            "faction_name": {"type": "string", "description": "Faction name for faction action."},
            "delta": {
                "type": "integer",
                "description": "Attitude/relation score change (+1 friendly, -2 hostile, etc.).",
            },
            "attitude": {
                "type": "integer",
                "description": "Absolute NPC attitude score (-5 vengeful to +5 allied).",
            },
            "trust": {"type": "integer", "description": "NPC trust level (0-10)."},
            "fear": {"type": "integer", "description": "NPC fear level (0-10)."},
            "status": {"type": "string", "description": "NPC status label (alive, dead, missing, etc.)."},
            "location": {"type": "string", "description": "NPC current location."},
        },
        "required": ["action"],
    }
)
class DndCharacterTool(Tool):
    """Create and manage D&D characters — PCs and NPCs — in the character library."""

    name = "dnd_character"
    description = (
        "Character library + NPC attitude + faction relations. "
        "Create/list/show/update/bind/unbind PCs and NPCs. "
        "Adjust NPC attitude scores (npc_attitude) and status (npc_status). "
        "Adjust faction relations (faction). Get world summary (world_summary). "
        "NPCs live in a global library; PCs bind to campaigns."
    )
    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(Database())

    def __init__(self, database: Database, *, migrate: bool = True) -> None:
        self.database = database
        self._migrate = migrate
        self._ready = False
        self.service = CharacterService(database)
        self.world = WorldService(database)

    @property
    def read_only(self) -> bool:
        return False

    def _ensure_ready(self) -> None:
        if not self._ready:
            if self._migrate:
                self.database.upgrade_schema()
            self._ready = True

    @staticmethod
    def _context_campaign_id() -> str | None:
        context = current_request_context()
        if context is None:
            return None
        value = context.metadata.get("campaign_id")
        return str(value) if value else None

    def _execute_sync(
        self,
        *,
        action: str,
        name: str | None = None,
        character_type: str | None = None,
        character_id: str | None = None,
        campaign_id: str | None = None,
        player_name: str | None = None,
        class_name: str | None = None,
        level: int | None = None,
        hp: int | None = None,
        max_hp: int | None = None,
        armor_class: int | None = None,
        sheet_json: dict | None = None,
        race: str | None = None,
        background: str | None = None,
        alignment: str | None = None,
        personality_traits: str | None = None,
        ideals: str | None = None,
        bonds: str | None = None,
        flaws: str | None = None,
        appearance: str | None = None,
        backstory: str | None = None,
        goals: str | None = None,
        notes: str | None = None,
        portrait_url: str | None = None,
        fields: dict | None = None,
        npc_name: str | None = None,
        faction_name: str | None = None,
        delta: int | None = None,
        attitude: int | None = None,
        trust: int | None = None,
        fear: int | None = None,
        status: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_ready()

        if action == "create":
            if not name:
                raise ValueError("name is required for create")
            info = self.service.create(
                name,
                character_type=character_type or "pc",
                campaign_id=campaign_id or self._context_campaign_id(),
                player_name=player_name,
                class_name=class_name,
                level=level,
                hp=hp,
                max_hp=max_hp,
                armor_class=armor_class,
                sheet_json=sheet_json,
                race=race,
                background=background,
                alignment=alignment,
                personality_traits=personality_traits,
                ideals=ideals,
                bonds=bonds,
                flaws=flaws,
                appearance=appearance,
                backstory=backstory,
                goals=goals,
                notes=notes,
                portrait_url=portrait_url,
            )
            return asdict(info)

        if action == "list":
            return [
                asdict(item)
                for item in self.service.list(
                    campaign_id=campaign_id,
                    character_type=character_type,
                )
            ]

        if action == "show":
            if not character_id:
                raise ValueError("character_id is required for show")
            return asdict(self.service.get(character_id))

        if action == "update":
            if not character_id:
                raise ValueError("character_id is required for update")
            if not fields:
                raise ValueError("fields dict is required for update")
            return asdict(self.service.update(character_id, **fields))

        if action == "bind":
            if not character_id or not campaign_id:
                raise ValueError("character_id and campaign_id required for bind")
            return asdict(self.service.bind_to_campaign(character_id, campaign_id))

        if action == "unbind":
            if not character_id:
                raise ValueError("character_id is required for unbind")
            return asdict(self.service.unbind_from_campaign(character_id))

        # ── NPC attitude / faction / world ──────────────────────────

        resolved_campaign = campaign_id or self._context_campaign_id()
        if not resolved_campaign:
            raise ValueError("campaign_id is required for world state actions")

        if action == "npc_attitude":
            if not npc_name:
                raise ValueError("npc_name is required for npc_attitude")
            return self.world.update_npc_attitude(
                resolved_campaign, npc_name, delta or 0,
                note=notes or "",
            )

        if action == "npc_status":
            if not npc_name:
                raise ValueError("npc_name is required for npc_status")
            if any(x is not None for x in (status, attitude, trust, fear, notes, location)):
                return self.world.set_npc_status(
                    resolved_campaign, npc_name,
                    status=status, attitude=attitude,
                    trust=trust, fear=fear,
                    note=notes, location=location,
                )
            # No fields to set → get current status.
            result = self.world.get_npc_status(resolved_campaign, npc_name)
            return result or {"npc_name": npc_name, "status": "unknown"}

        if action == "faction":
            if not faction_name:
                raise ValueError("faction_name is required for faction action")
            return self.world.update_faction(
                resolved_campaign, faction_name, delta or 0, note=notes or "",
            )

        if action == "world_summary":
            return {
                "summary": self.world.get_summary(resolved_campaign),
                "factions": self.world.get_factions(resolved_campaign),
                "npcs": self.world.list_npc_statuses(resolved_campaign),
                "quests": self.world.get_quests(resolved_campaign),
            }

        raise ValueError(f"unknown action: {action}")

    async def execute(self, action: str, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self._execute_sync, action=action, **kwargs)
