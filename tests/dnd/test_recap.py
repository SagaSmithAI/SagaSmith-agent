"""Tests for recap generation, campaign memory, and snapshot integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.dnd.db.database import Database, sqlite_database_url
from nanobot.dnd.db.memory import CampaignMemoryService, trigger_memory_from_recap
from nanobot.dnd.db.models import Campaign, CampaignEvent, Character, Party, PlotSummary, WorldState
from nanobot.dnd.db.models.runtime import CampaignMemory, CampaignSave
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
# CampaignMemoryService tests
# ---------------------------------------------------------------------------

class TestCampaignMemoryService:
    def test_upsert_creates_new_memory(self, database):
        _seed_campaign(database, "c_mem")
        service = CampaignMemoryService(database)
        mid = service.upsert(
            "c_mem", kind="npc_relation", text="Met innkeeper.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="innkeeper_01", fact_type="met",
        )
        assert mid.startswith("mem_")

        memories = service.get_active("c_mem")
        assert len(memories) == 1
        assert memories[0]["text"] == "Met innkeeper."
        assert memories[0]["status"] == "permanent"

    def test_upsert_updates_existing_by_key(self, database):
        _seed_campaign(database, "c_mem2")
        service = CampaignMemoryService(database)
        mid1 = service.upsert(
            "c_mem2", kind="npc_relation", text="Met innkeeper.",
            priority="medium", status="candidate",
            entity_type="npc", entity_id="innkeeper_01", fact_type="met",
        )
        mid2 = service.upsert(
            "c_mem2", kind="npc_relation", text="Innkeeper became an ally.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="innkeeper_01", fact_type="met",
        )
        assert mid1 == mid2  # Same id — updated in place

        # After update, status is now permanent — get_active should return it
        active = service.get_active("c_mem2")
        assert len(active) == 1
        assert active[0]["text"] == "Innkeeper became an ally."
        assert active[0]["status"] == "permanent"

    def test_get_active_filters_by_status(self, database):
        _seed_campaign(database, "c_mem3")
        service = CampaignMemoryService(database)
        service.upsert("c_mem3", kind="plot_commitment", text="Stable fact.",
                       priority="medium", status="stable")
        service.upsert("c_mem3", kind="plot_commitment", text="Permanent fact.",
                       priority="high", status="permanent")
        service.upsert("c_mem3", kind="plot_commitment", text="Candidate fact.",
                       priority="medium", status="candidate")

        active = service.get_active("c_mem3")
        assert len(active) == 2
        texts = {m["text"] for m in active}
        assert "Stable fact." in texts
        assert "Permanent fact." in texts
        assert "Candidate fact." not in texts

    def test_prune_removes_low_score_candidates(self, database):
        _seed_campaign(database, "c_mem4")
        service = CampaignMemoryService(database)
        service.upsert("c_mem4", kind="plot_commitment", text="Low score.",
                       priority="medium", status="candidate")
        # Set the score manually
        with database.transaction() as session:
            mem = session.scalars(
                __import__("sqlalchemy").select(CampaignMemory).where(
                    CampaignMemory.campaign_id == "c_mem4",
                )
            ).first()
            if mem:
                mem.score = 2

        count = service.prune("c_mem4", min_score=3)
        assert count == 1


# ---------------------------------------------------------------------------
# trigger_memory_from_recap tests
# ---------------------------------------------------------------------------

class TestTriggerMemoryFromRecap:
    def test_p0_high_priority_writes_permanent(self, database):
        _seed_campaign(database, "c_trigger")
        recap = _make_recap(memory_candidates=[
            {"kind": "plot_commitment", "text": "Important plot fact.", "priority": "high"},
        ])
        actions = trigger_memory_from_recap(database, "c_trigger", "save_001", recap)
        assert len(actions) >= 1
        high_actions = [a for a in actions if a.get("priority") == "high"]
        assert len(high_actions) >= 1
        assert high_actions[0]["status"] == "permanent"

    def test_p2_low_priority_skipped(self, database):
        _seed_campaign(database, "c_trigger2")
        recap = _make_recap(memory_candidates=[
            {"kind": "item_fact", "text": "Bought ale.", "priority": "low"},
        ])
        actions = trigger_memory_from_recap(database, "c_trigger2", "save_002", recap)
        skipped = [a for a in actions if a.get("action") == "skipped"]
        assert len(skipped) == 1

    def test_future_impact_writes_as_candidate(self, database):
        _seed_campaign(database, "c_trigger3")
        recap = _make_recap(
            memory_candidates=[],
            future_impact=["The innkeeper may offer a quest."],
        )
        actions = trigger_memory_from_recap(database, "c_trigger3", "save_003", recap)
        upserts = [a for a in actions if a.get("action") == "upsert"]
        assert len(upserts) >= 1

    def test_duplicate_entity_fact_updates(self, database):
        _seed_campaign(database, "c_trigger4")
        service = CampaignMemoryService(database)
        # Insert via service directly (entity_id set, dedup works)
        mid1 = service.upsert(
            "c_trigger4", kind="npc_relation", text="Met innkeeper.",
            priority="medium", status="candidate",
            entity_type="npc", entity_id="innkeeper_01", fact_type="met",
        )
        mid2 = service.upsert(
            "c_trigger4", kind="npc_relation", text="Innkeeper now an ally.",
            priority="medium", status="candidate",
            entity_type="npc", entity_id="innkeeper_01", fact_type="met",
        )
        assert mid1 == mid2  # Same id — updated in place

        all_mems = service.list_by_status("c_trigger4", ["candidate", "permanent", "stable"])
        npc_mems = [m for m in all_mems if m["kind"] == "npc_relation"]
        assert len(npc_mems) == 1  # Updated, not duplicated
        assert "ally" in npc_mems[0]["text"]

    def test_trigger_dedupes_candidates_without_explicit_entity_id(self, database):
        _seed_campaign(database, "c_trigger5")
        recap = _make_recap(
            memory_candidates=[
                {"kind": "plot_commitment", "text": "The party promised to help Mira.", "priority": "medium"},
            ],
            future_impact=[],
        )

        first = trigger_memory_from_recap(database, "c_trigger5", "save_005", recap)
        second = trigger_memory_from_recap(database, "c_trigger5", "save_006", recap)

        first_upsert = next(a for a in first if a.get("action") == "upsert")
        second_upsert = next(a for a in second if a.get("action") == "upsert")
        assert first_upsert["memory_id"] == second_upsert["memory_id"]

        memories = CampaignMemoryService(database).list_by_status("c_trigger5", ["candidate"])
        assert len(memories) == 1
        assert memories[0]["source_save_id"] == "save_006"


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
