"""Character library — PCs and NPCs, decoupled from campaigns."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nanobot.dnd.db.campaigns import CampaignNotFoundError
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models import Campaign, Character, Party, ToolAudit


class CharacterError(RuntimeError):
    """Base error for character persistence."""


class CharacterAlreadyExistsError(CharacterError):
    """A character with the same name already exists in the given scope."""


class CharacterNotFoundError(CharacterError):
    """The requested character does not exist."""


@dataclass(frozen=True)
class CharacterInfo:
    id: str
    name: str
    character_type: str
    campaign_id: str | None
    party_id: str | None
    player_name: str | None
    class_name: str
    level: int
    hp: int
    max_hp: int
    armor_class: int
    sheet_json: dict[str, Any]
    # Lore / roleplay
    race: str | None = None
    background: str | None = None
    alignment: str | None = None
    personality_traits: str | None = None
    ideals: str | None = None
    bonds: str | None = None
    flaws: str | None = None
    appearance: str | None = None
    backstory: str | None = None
    goals: str | None = None
    notes: str | None = None
    portrait_url: str | None = None


class CharacterService:
    """Create and manage characters — PCs and NPCs — with an optional campaign binding."""

    def __init__(self, database: Database) -> None:
        self.database = database

    # ── Create ──────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        *,
        character_id: str | None = None,
        character_type: str = "pc",
        campaign_id: str | None = None,
        party_id: str | None = None,
        player_name: str | None = None,
        class_name: str | None = None,
        level: int | None = None,
        hp: int | None = None,
        max_hp: int | None = None,
        armor_class: int | None = None,
        sheet_json: dict[str, Any] | None = None,
        # Lore fields
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
        actor_id: str | None = None,
    ) -> CharacterInfo:
        if character_type not in ("pc", "npc"):
            raise ValueError("character_type must be 'pc' or 'npc'")
        sheet = dict(sheet_json or {})
        resolved_class = class_name or str(
            sheet.get("class_name") or sheet.get("class") or ""
        )
        resolved_level = int(level if level is not None else sheet.get("level", 1))
        hp_data = sheet.get("hp")
        resolved_hp = int(
            hp if hp is not None
            else (hp_data.get("current", 10) if isinstance(hp_data, dict) else hp_data or 10)
        )
        resolved_max_hp = int(
            max_hp if max_hp is not None
            else (hp_data.get("max", resolved_hp) if isinstance(hp_data, dict) else resolved_hp)
        )
        resolved_ac = int(armor_class if armor_class is not None else sheet.get("ac", 10))
        character_id = character_id or f"character_{uuid.uuid4().hex[:16]}"

        campaign = None
        if campaign_id:
            with self.database.transaction() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    raise CampaignNotFoundError(f"campaign not found: {campaign_id}")

        try:
            with self.database.transaction() as session:
                # If binding to campaign, auto-link to its party.
                resolved_party_id = party_id
                if campaign_id and resolved_party_id is None:
                    party = session.scalar(
                        select(Party).where(Party.campaign_id == campaign_id)
                    )
                    if party is not None:
                        resolved_party_id = party.id

                character = Character(
                    id=character_id,
                    character_type=character_type,
                    campaign_id=campaign_id,
                    party_id=resolved_party_id,
                    name=name,
                    player_name=player_name,
                    class_name=resolved_class,
                    level=resolved_level,
                    hp=resolved_hp,
                    max_hp=resolved_max_hp,
                    armor_class=resolved_ac,
                    sheet_json=sheet,
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
                session.add(character)
                session.flush()
                audit_id = f"audit_character_{uuid.uuid4().hex[:16]}"
                session.add(
                    ToolAudit(
                        id=audit_id,
                        request_id=f"character-create:{uuid.uuid4().hex}",
                        campaign_id=campaign_id or "",
                        actor_id=actor_id,
                        tool_name="dnd_character_create",
                        engine_function="database.character.create",
                        arguments_json={
                            "name": name,
                            "character_type": character_type,
                            "player_name": player_name,
                        },
                        result_json={"character_id": character.id},
                        after_state_json=sheet,
                        success=True,
                        state_version=character.state_version,
                    )
                )
                return self._info(character)
        except IntegrityError as exc:
            scope = f"campaign {campaign_id}" if campaign_id else "global library"
            raise CharacterAlreadyExistsError(
                f"character '{name}' already exists in {scope}"
            ) from exc

    # ── List ────────────────────────────────────────────────────────────

    def list(
        self,
        *,
        campaign_id: str | None = None,
        character_type: str | None = None,
    ) -> list[CharacterInfo]:
        with self.database.transaction() as session:
            conditions = []
            if campaign_id is not None:
                if campaign_id:
                    conditions.append(Character.campaign_id == campaign_id)
                else:
                    conditions.append(Character.campaign_id.is_(None))
            if character_type is not None:
                conditions.append(Character.character_type == character_type)
            statement = (
                select(Character)
                .where(*conditions)
                .order_by(Character.character_type, Character.created_at, Character.id)
            )
            return [self._info(c) for c in session.scalars(statement)]

    # ── Get ─────────────────────────────────────────────────────────────

    def get(self, character_id: str) -> CharacterInfo:
        with self.database.transaction() as session:
            character = session.get(Character, character_id)
            if character is None:
                raise CharacterNotFoundError(f"character not found: {character_id}")
            return self._info(character)

    # ── Campaign binding ────────────────────────────────────────────────

    def bind_to_campaign(
        self,
        character_id: str,
        campaign_id: str,
        *,
        party_id: str | None = None,
        actor_id: str | None = None,
    ) -> CharacterInfo:
        with self.database.transaction() as session:
            character = session.get(Character, character_id)
            if character is None:
                raise CharacterNotFoundError(f"character not found: {character_id}")
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise CampaignNotFoundError(f"campaign not found: {campaign_id}")
            if character.character_type != "pc":
                raise CharacterError("only PCs can be bound to campaigns")
            if character.campaign_id == campaign_id:
                return self._info(character)  # already bound

            # Auto-link to party.
            resolved_party_id = party_id
            if resolved_party_id is None:
                party = session.scalar(
                    select(Party).where(Party.campaign_id == campaign_id)
                )
                if party is not None:
                    resolved_party_id = party.id

            character.campaign_id = campaign_id
            character.party_id = resolved_party_id
            character.state_version = (character.state_version or 0) + 1
            session.flush()
            return self._info(character)

    def unbind_from_campaign(
        self,
        character_id: str,
        *,
        actor_id: str | None = None,
    ) -> CharacterInfo:
        with self.database.transaction() as session:
            character = session.get(Character, character_id)
            if character is None:
                raise CharacterNotFoundError(f"character not found: {character_id}")
            character.campaign_id = None
            character.party_id = None
            character.state_version = (character.state_version or 0) + 1
            session.flush()
            return self._info(character)

    # ── Update ──────────────────────────────────────────────────────────

    def update(
        self,
        character_id: str,
        *,
        actor_id: str | None = None,
        **fields: Any,
    ) -> CharacterInfo:
        allowed = {
            "name", "player_name", "class_name", "level", "hp", "max_hp",
            "armor_class", "sheet_json", "character_type",
            "race", "background", "alignment",
            "personality_traits", "ideals", "bonds", "flaws",
            "appearance", "backstory", "goals", "notes", "portrait_url",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
        with self.database.transaction() as session:
            character = session.get(Character, character_id)
            if character is None:
                raise CharacterNotFoundError(f"character not found: {character_id}")
            for key, value in fields.items():
                if hasattr(character, key):
                    setattr(character, key, value)
            character.state_version = (character.state_version or 0) + 1
            session.flush()
            return self._info(character)

    # ── Info ────────────────────────────────────────────────────────────

    @staticmethod
    def _info(character: Character) -> CharacterInfo:
        return CharacterInfo(
            id=character.id,
            name=character.name,
            character_type=character.character_type,
            campaign_id=character.campaign_id,
            party_id=character.party_id,
            player_name=character.player_name,
            class_name=character.class_name,
            level=character.level,
            hp=character.hp,
            max_hp=character.max_hp,
            armor_class=character.armor_class,
            sheet_json=dict(character.sheet_json or {}),
            race=character.race,
            background=character.background,
            alignment=character.alignment,
            personality_traits=character.personality_traits,
            ideals=character.ideals,
            bonds=character.bonds,
            flaws=character.flaws,
            appearance=character.appearance,
            backstory=character.backstory,
            goals=character.goals,
            notes=character.notes,
            portrait_url=character.portrait_url,
        )
