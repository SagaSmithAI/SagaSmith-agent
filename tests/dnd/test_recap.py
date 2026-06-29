"""Tests for recap generation, campaign memory, and snapshot integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.dnd.db.database import Database, sqlite_database_url
from nanobot.dnd.db.memory import CampaignMemoryService, trigger_memory_from_recap
from nanobot.dnd.db.models import Campaign, CampaignEvent, Character, Party, PlotSummary, WorldState
from nanobot.dnd.db.models.runtime import CampaignSave
from nanobot.dnd.db.recap import RecapGenerator
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.providers.base import LLMResponse


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(sqlite_database_url(tmp_path / "recap.db"))
    db.upgrade_schema()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.chat_with_retry = AsyncMock()
    return p


def _seed_campaign(database: Database, campaign_id: str, *, day: int = 1) -> None:
    with database.transaction() as session:
        session.add(Campaign(id=campaign_id, name=f"Campaign {campaign_id}"))
        session.flush()
        session.add(
            WorldState(
                id=f"world_{campaign_id}",
                campaign_id=campaign_id,
                state_json={"day_in_game": day, "current_chapter": 1, "current_scene": "tavern"},
                state_version=1,
            )
        )
        session.add(
            Party(
                id=f"party_{campaign_id}",
                campaign_id=campaign_id,
                location="Tavern",
                state_json={},
            )
        )
        session.flush()
        session.add(
            Character(
                id=f"char_{campaign_id}",
                campaign_id=campaign_id,
                party_id=f"party_{campaign_id}",
                name="Hero",
                hp=10,
                max_hp=10,
            )
        )
        session.add(
            PlotSummary(
                id=f"summary_{campaign_id}",
                campaign_id=campaign_id,
                summary="The party begins.",
            )
        )
        session.add(
            CampaignEvent(
                id=f"event_{campaign_id}",
                campaign_id=campaign_id,
                event_type="start",
                content="Campaign started",
            )
        )


def _make_recap(**overrides) -> dict:
    recap = {
        "version": 1,
        "baseline": True,
        "from_save_id": None,
        "to_save_id": None,
        "generated_at": "2026-06-27T00:00:00Z",
        "language": "zh-CN",
        "summary": "The party entered the tavern.",
        "plot_progress": ["Party met"],
        "new_characters": [
            {"name": "Innkeeper", "role": "NPC", "relationship": "neutral", "first_seen_at": "Tavern"}
        ],
        "new_locations": ["Tavern"],
        "triggered_events": [],
        "future_impact": ["The innkeeper may have quests."],
        "player_choices": ["Entered the tavern."],
        "memory_candidates": [
            {"kind": "npc_relation", "text": "Met innkeeper at tavern.", "priority": "medium"},
            {"kind": "plot_commitment", "text": "Campaign started in tavern.", "priority": "high"},
            {"kind": "item_fact", "text": "Bought ale.", "priority": "low"},
        ],
        "source": {"mode": "baseline"},
    }
    recap.update(overrides)
    return recap


# ---------------------------------------------------------------------------
# RecapGenerator tests
# ---------------------------------------------------------------------------

class TestRecapGenerator:
    def test_produces_valid_recap_on_json_response(self, mock_provider):
        recap_json = json.dumps({
            "summary": "The party entered the tavern and met the innkeeper.",
            "plot_progress": ["Party met"],
            "new_characters": [
                {"name": "Innkeeper", "role": "NPC", "relationship": "neutral", "first_seen_at": "Tavern"}
            ],
            "new_locations": ["Tavern"],
            "triggered_events": [],
            "future_impact": ["The innkeeper may have quests."],
            "player_choices": ["Entered the tavern."],
            "memory_candidates": [
                {"kind": "npc_relation", "text": "Met innkeeper.", "priority": "medium"}
            ],
        }, ensure_ascii=False)
        mock_provider.chat_with_retry.return_value = LLMResponse(
            content=recap_json, finish_reason="stop",
        )
        generator = RecapGenerator(mock_provider, "test-model")
        payload = {
            "format": "dnd-campaign-snapshot", "schema_version": 3,
            "campaign_id": "c1", "captured_at": "2026-06-27T00:00:00Z",
            "campaign": {"name": "Test"},
            "state": {"world_states": [], "parties": [], "characters": [],
                      "combats": [], "plot_summaries": [], "campaign_events": [],
                      "scene_states": [], "channel_bindings": []},
        }
        import asyncio
        recap = asyncio.run(generator.generate("c1", None, payload))
        assert recap["baseline"] is True
        assert recap["from_save_id"] is None
        assert "entered the tavern" in recap["summary"]
        assert len(recap["new_characters"]) == 1
        assert recap["new_characters"][0]["name"] == "Innkeeper"

    def test_baseline_recap_first_save(self, mock_provider, database):
        recap_json = json.dumps({
            "summary": "Origin story.", "plot_progress": [], "new_characters": [],
            "new_locations": [], "triggered_events": [], "future_impact": [],
            "player_choices": [], "memory_candidates": [],
        }, ensure_ascii=False)
        mock_provider.chat_with_retry.return_value = LLMResponse(
            content=recap_json, finish_reason="stop",
        )
        generator = RecapGenerator(mock_provider, "test-model")
        _seed_campaign(database, "c_baseline")
        with database.transaction() as session:
            payload = CampaignSnapshotService.capture_from_session(session, "c_baseline")
        import asyncio
        recap = asyncio.run(generator.generate("c_baseline", None, payload))
        assert recap["baseline"] is True
        assert recap["from_save_id"] is None
        assert recap["source"]["mode"] == "baseline"

    def test_fallback_on_llm_error(self, mock_provider, database):
        mock_provider.chat_with_retry.side_effect = RuntimeError("LLM down")
        generator = RecapGenerator(mock_provider, "test-model")
        _seed_campaign(database, "c_fail")
        with database.transaction() as session:
            payload = CampaignSnapshotService.capture_from_session(session, "c_fail")
        import asyncio
        recap = asyncio.run(generator.generate("c_fail", None, payload))
        assert recap["source"]["mode"] == "failed"
        assert "暂无法生成" in recap["summary"]

    def test_fallback_on_non_json_response(self, mock_provider, database):
        mock_provider.chat_with_retry.return_value = LLMResponse(
            content="Not valid JSON at all.", finish_reason="stop",
        )
        generator = RecapGenerator(mock_provider, "test-model")
        _seed_campaign(database, "c_nonjson")
        with database.transaction() as session:
            payload = CampaignSnapshotService.capture_from_session(session, "c_nonjson")
        import asyncio
        recap = asyncio.run(generator.generate("c_nonjson", None, payload))
        # Should degrade to raw text as summary
        assert "summary" in recap
        assert len(recap["summary"]) > 0
        assert recap.get("plot_progress") is None or recap.get("plot_progress") == []

    def test_fallback_on_error_finish_reason(self, mock_provider, database):
        mock_provider.chat_with_retry.return_value = LLMResponse(
            content="", finish_reason="error",
        )
        generator = RecapGenerator(mock_provider, "test-model")
        _seed_campaign(database, "c_errfinish")
        with database.transaction() as session:
            payload = CampaignSnapshotService.capture_from_session(session, "c_errfinish")
        import asyncio
        recap = asyncio.run(generator.generate("c_errfinish", None, payload))
        assert recap["source"]["mode"] == "failed"

    def test_delta_recap_has_from_save_id(self, mock_provider, database):
        recap_json = json.dumps({
            "summary": "Delta summary.", "plot_progress": ["Advanced"], "new_characters": [],
            "new_locations": [], "triggered_events": [], "future_impact": [],
            "player_choices": [], "memory_candidates": [],
        }, ensure_ascii=False)
        mock_provider.chat_with_retry.return_value = LLMResponse(
            content=recap_json, finish_reason="stop",
        )
        _seed_campaign(database, "c_delta")
        # Create a previous save first
        service = CampaignSnapshotService(database)
        prev = service.create("c_delta", label="first")
        # Now generate delta
        with database.transaction() as session:
            payload = CampaignSnapshotService.capture_from_session(session, "c_delta")
            prev_save = session.get(CampaignSave, prev.id)

        generator = RecapGenerator(mock_provider, "test-model")
        import asyncio
        recap = asyncio.run(generator.generate("c_delta", prev_save, payload))
        assert recap["baseline"] is False
        assert recap["from_save_id"] == prev.id
        assert recap["source"]["mode"] == "delta_from_previous_snapshot"


# ---------------------------------------------------------------------------
# Branch-aware campaign memory tests
# ---------------------------------------------------------------------------

class TestCampaignMemoryService:
    def test_record_creates_fact_and_save_revision(self, database):
        _seed_campaign(database, "c_mem")
        save = CampaignSnapshotService(database).create("c_mem")
        service = CampaignMemoryService(database)

        memory_id, revision_id = service.record(
            "c_mem",
            save.id,
            kind="npc_relation",
            text="The innkeeper trusts the party.",
            priority="high",
            status="permanent",
            entity_type="npc",
            entity_id="npc_innkeeper",
            fact_type="relationship",
        )

        assert memory_id.startswith("mem_")
        assert revision_id.startswith("memrev_")
        active = service.get_active("c_mem")
        assert len(active) == 1
        assert active[0]["text"] == "The innkeeper trusts the party."
        assert active[0]["source_save_id"] == save.id

    def test_nearest_revision_wins_on_same_lineage(self, database):
        _seed_campaign(database, "c_mem2")
        snapshots = CampaignSnapshotService(database)
        service = CampaignMemoryService(database)
        first = snapshots.create("c_mem2")
        memory_id, _ = service.record(
            "c_mem2", first.id,
            kind="npc_relation", text="Mira is neutral.",
            priority="medium", status="candidate",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )
        second = snapshots.create("c_mem2")
        same_memory_id, _ = service.record(
            "c_mem2", second.id,
            kind="npc_relation", text="Mira is hostile.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        assert same_memory_id == memory_id
        effective = service.get_effective("c_mem2", save_id=second.id)
        assert len(effective) == 1
        assert effective[0]["text"] == "Mira is hostile."

    def test_sibling_branch_memories_are_isolated(self, database):
        _seed_campaign(database, "c_branch")
        snapshots = CampaignSnapshotService(database)
        service = CampaignMemoryService(database)

        root = snapshots.create("c_branch", label="root")
        service.record(
            "c_branch", root.id,
            kind="npc_relation", text="Mira is neutral.",
            priority="medium", status="candidate",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        left = snapshots.create("c_branch", label="left")
        service.record(
            "c_branch", left.id,
            kind="npc_relation", text="Mira is hostile.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        snapshots.restore("c_branch", root.slot, auto_save=False)
        right = snapshots.create("c_branch", label="right")
        service.record(
            "c_branch", right.id,
            kind="npc_relation", text="Mira is an ally.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        left_memory = service.get_effective("c_branch", save_id=left.id)
        right_memory = service.get_effective("c_branch", save_id=right.id)
        assert left_memory[0]["text"] == "Mira is hostile."
        assert right_memory[0]["text"] == "Mira is an ally."

        scope = service.scope("c_branch", save_id=right.id)
        included_ids = {row["id"] for row in scope["included_saves"]}
        assert included_ids == {root.id, right.id}
        assert left.id not in included_ids


class TestTriggerMemoryFromRecap:
    def test_priority_and_future_impact_create_revisions(self, database):
        _seed_campaign(database, "c_trigger")
        save = CampaignSnapshotService(database).create("c_trigger")
        recap = _make_recap(memory_candidates=[
            {
                "kind": "plot_commitment",
                "text": "Important plot fact.",
                "priority": "high",
                "entity_type": "plot",
                "entity_id": "main_plot",
                "fact_type": "commitment",
            },
            {"kind": "item_fact", "text": "Bought ale.", "priority": "low"},
        ])

        actions = trigger_memory_from_recap(database, "c_trigger", save.id, recap)

        high = next(action for action in actions if action.get("priority") == "high")
        skipped = next(action for action in actions if action.get("action") == "skipped")
        assert high["status"] == "permanent"
        assert high["revision_id"].startswith("memrev_")
        assert skipped["priority"] == "low"
        assert len(CampaignMemoryService(database).get_by_save(save.id)) == 2


# ---------------------------------------------------------------------------
# SnapshotInfo recap integration tests
# ---------------------------------------------------------------------------

class TestSnapshotWithRecap:
    def test_snapshot_info_has_recap_field(self, database):
        _seed_campaign(database, "c_snap")
        service = CampaignSnapshotService(database)
        recap = _make_recap()
        result = service.create("c_snap", label="test", recap=recap)
        assert result.recap is not None
        assert result.recap["summary"] == recap["summary"]
        assert result.recap["baseline"] is True
        assert result.recap["to_save_id"] == result.id

    def test_snapshot_creates_without_recap(self, database):
        _seed_campaign(database, "c_norecap")
        service = CampaignSnapshotService(database)
        result = service.create("c_norecap", label="no recap")
        assert result.recap is None

    def test_old_snapshot_without_recap_key(self, database):
        """SnapshotInfo handles saves without 'recap' in snapshot_json."""
        _seed_campaign(database, "c_old")
        service = CampaignSnapshotService(database)
        result = service.create("c_old", label="test")
        assert result.recap is None  # No recap passed, none in snapshot_json

    def test_snapshot_list_returns_recap_summary(self, database):
        _seed_campaign(database, "c_list")
        service = CampaignSnapshotService(database)
        recap = _make_recap(summary="A" * 200)  # long summary
        service.create("c_list", label="test", recap=recap)
        saves = service.list("c_list")
        assert len(saves) == 1
        assert saves[0].recap is not None
        assert len(saves[0].recap["summary"]) == 200

    def test_regenerate_recap_updates_in_place(self, database):
        _seed_campaign(database, "c_regen")
        service = CampaignSnapshotService(database)
        old_recap = _make_recap(summary="Old summary.")
        result = service.create("c_regen", label="test", recap=old_recap)
        slot = result.slot

        new_recap = _make_recap(summary="New summary after regeneration.")
        regenerated = service.regenerate_recap("c_regen", slot, new_recap)
        assert regenerated.recap is not None
        assert regenerated.recap["summary"] == "New summary after regeneration."
        assert isinstance(regenerated.memory_actions, list)

    def test_memory_actions_in_snapshot_info(self, database):
        _seed_campaign(database, "c_actions")
        service = CampaignSnapshotService(database)
        recap = _make_recap()
        result = service.create("c_actions", label="test", recap=recap)
        # memory_actions should be populated
        # (may be empty if no candidates match, but should be a list)
        assert isinstance(result.memory_actions, list)

    def test_snapshot_memory_trigger_runs_once_with_final_save_id(self, database):
        _seed_campaign(database, "c_actions2")
        service = CampaignSnapshotService(database)
        recap = _make_recap(
            memory_candidates=[
                {"kind": "plot_commitment", "text": "Campaign started in tavern.", "priority": "high"},
            ],
            future_impact=[],
        )
        result = service.create("c_actions2", label="test", recap=recap)

        memories = CampaignMemoryService(database).get_by_save(result.id)
        assert len(memories) == 1
        assert memories[0]["source_save_id"] == result.id
        assert not CampaignMemoryService(database).get_by_save("")
