"""Tests for the dnd-dm-skill-aligned D&D database."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from nanobot.dnd.db import Database, sqlite_database_url
from nanobot.dnd.db.migration import current_revision
from nanobot.dnd.db.models import (
    Campaign,
    CampaignEvent,
    CampaignSave,
    ChannelBinding,
    Character,
    Combat,
    DiceRoll,
    Party,
    StateRevision,
    ToolAudit,
    WorldState,
)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    url = sqlite_database_url(tmp_path / "dnd.db")
    db = Database(url)
    db.upgrade_schema()
    try:
        yield db
    finally:
        db.dispose()


def test_migration_creates_v2_domain_schema_without_legacy_mode_tables(
    database: Database,
) -> None:
    tables = set(inspect(database.engine).get_table_names())

    assert {
        "campaigns",
        "world_states",
        "parties",
        "characters",
        "combats",
        "campaign_saves",
        "plot_summaries",
        "campaign_events",
        "module_sources",
        "module_chapters",
        "scene_indexes",
        "scene_states",
        "dice_rolls",
        "tool_audits",
        "state_revisions",
        "rule_sources",
        "rule_sets",
        "rule_publications",
        "rule_sections",
        "embedding_models",
        "rule_chunks",
        "compendium_entries",
        "campaign_rule_profiles",
        "campaign_rule_publications",
        "channel_bindings",
    } <= tables
    assert "lobby_session_states" not in tables
    assert "task_sessions" not in tables
    assert "agent_jobs" not in tables
    assert current_revision(database.url) == "20260624_07"


def test_skill_aggregate_state_round_trip(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus", module_name="Descent"))
        session.flush()
        session.add(
            WorldState(
                id="world_1",
                campaign_id="campaign_1",
                state_json={
                    "current_chapter": 1,
                    "current_scene": "Elfsong Tavern",
                    "day_in_game": 1,
                    "faction_relations": {},
                    "quest_progress": {"进行中": ["进入地狱"]},
                },
            )
        )
        session.add(
            Party(
                id="party_1",
                campaign_id="campaign_1",
                location="Elfsong Tavern",
                shared_gold=20,
            )
        )
        session.flush()
        session.add(
            Character(
                id="character_1",
                campaign_id="campaign_1",
                party_id="party_1",
                name="Kalen",
                class_name="Wizard",
                level=3,
                hp=18,
                max_hp=18,
                armor_class=14,
                sheet_json={"stats": {"intelligence": 16}, "spellSlots": [4, 2]},
            )
        )
        session.flush()
        session.add(
            ChannelBinding(
                id="binding_1",
                campaign_id="campaign_1",
                channel="napcat",
                external_user_id="123456",
                external_chat_id="group_1",
                character_id="character_1",
            )
        )

    with database.transaction() as session:
        world = session.scalar(select(WorldState))
        character = session.get(Character, "character_1")
        binding = session.scalar(select(ChannelBinding))

        assert world is not None and world.state_json["current_chapter"] == 1
        assert character is not None and character.sheet_json["spellSlots"] == [4, 2]
        assert binding is not None and binding.character_id == character.id
        assert session.get(Campaign, "campaign_1").engine_source == (
            "dnd-dm-skill/dnd-engine/src/dnd_engine"
        )


def test_combat_save_event_and_audit_round_trip(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus"))
        session.flush()
        session.add(
            Combat(
                id="combat_1",
                campaign_id="campaign_1",
                name="Tavern Ambush",
                location="Elfsong Tavern",
                state_json={
                    "units": [{"name": "Cultist", "hp": 9, "initiative": 12}],
                    "log": ["combat started"],
                },
            )
        )
        session.add(
            CampaignSave(
                id="save_1",
                campaign_id="campaign_1",
                slot=1,
                chapter="1",
                location="Elfsong Tavern",
                snapshot_json={
                    "party": [],
                    "mainQuests": [],
                    "completedNodes": [],
                    "plotSummary": "The party arrived.",
                },
            )
        )
        session.add(
            CampaignEvent(
                id="event_1",
                campaign_id="campaign_1",
                event_type="scene_entered",
                content="The party entered the tavern.",
            )
        )
        session.add(
            DiceRoll(
                id="roll_1",
                request_id="request_1",
                campaign_id="campaign_1",
                formula="1d20+5",
                result=17,
                detail_json={"rolls": [12], "bonus": 5},
            )
        )
        session.add(
            ToolAudit(
                id="audit_1",
                request_id="request_1",
                campaign_id="campaign_1",
                actor_id="123456",
                tool_name="dnd_roll",
                engine_function="dnd_engine.dice.rolls.rolling",
                arguments_json={"formula": "1d20+5"},
                result_json={"result": 17},
                before_state_json={"roll_count": 0},
                after_state_json={"roll_count": 1},
                duration_ms=4,
                state_version=1,
            )
        )
        session.flush()
        session.add(
            StateRevision(
                id="revision_1",
                campaign_id="campaign_1",
                tool_audit_id="audit_1",
                actor_id="123456",
                aggregate_type="world",
                aggregate_key="default",
                engine_function="dnd_engine.state.world.set_current_scene_state",
                state_version=2,
                before_state_json={"current_scene": "Gate"},
                after_state_json={"current_scene": "Tavern"},
            )
        )

    with database.transaction() as session:
        assert session.get(Combat, "combat_1").state_json["units"][0]["hp"] == 9
        assert session.get(CampaignSave, "save_1").snapshot_json["plotSummary"]
        assert session.get(DiceRoll, "roll_1").result == 17
        audit = session.get(ToolAudit, "audit_1")
        revision = session.get(StateRevision, "revision_1")
        assert audit.state_version == 1 and audit.success is True
        assert audit.engine_function == "dnd_engine.dice.rolls.rolling"
        assert revision.after_state_json == {"current_scene": "Tavern"}


def test_channel_binding_uniqueness_rolls_back_transaction(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus"))
        session.flush()
        session.add(
            Character(
                id="character_1",
                campaign_id="campaign_1",
                name="Kalen",
            )
        )

    with pytest.raises(IntegrityError), database.transaction() as session:
        for binding_id in ("binding_1", "binding_2"):
            session.add(
                ChannelBinding(
                    id=binding_id,
                    campaign_id="campaign_1",
                    channel="napcat",
                    external_user_id="123456",
                    character_id="character_1",
                )
            )

    with database.transaction() as session:
        assert session.scalar(select(ChannelBinding)) is None


def test_audit_records_are_append_only(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus"))
        session.flush()
        session.add(
            ToolAudit(
                id="audit_1",
                request_id="request_1",
                campaign_id="campaign_1",
                tool_name="dnd_world",
                engine_function="dnd_engine.state.world.advance_day",
                result_json={"day_in_game": 2},
            )
        )

    with pytest.raises(RuntimeError, match="append-only"), database.transaction() as session:
        audit = session.get(ToolAudit, "audit_1")
        audit.result_json = {"day_in_game": 99}
